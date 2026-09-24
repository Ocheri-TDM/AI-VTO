"""Persistent job queue and replaceable in-process scheduler/worker host."""

import asyncio
import time
from contextlib import suppress
from datetime import timedelta
from uuid import uuid4

from app.database.catalog import CatalogSafety
from app.database.models import SupplierSyncState
from app.domain.catalog import completion
from app.domain.errors import SupplierError
from app.domain.models import utcnow
from app.domain.research import CoverageStatus, ResearchCoverage
from app.observability import log_event


class JobCancelled(Exception):
    pass


class YieldJob(Exception):
    """A safe checkpoint boundary used to give interactive work a turn."""
    pass


class BackgroundIndex:
    def __init__(self, repository, providers, settings):
        self.repository, self.settings = repository, settings
        self.providers = {p.supplier: p for p in providers}
        self.tasks = []
        self.owner = str(uuid4())
        self.on_update = None
        self.safety = CatalogSafety(repository)

    def start(self):
        self.tasks = [asyncio.create_task(self.schedule())]
        # Reserve one fair lane for full catalog traversal. Interactive jobs have
        # their own lane below and must not starve discovery indefinitely.
        self.tasks.append(asyncio.create_task(self.worker({
            "CATALOG_DISCOVERY", "FULL_CATALOG_RECONCILIATION", "INCREMENTAL_CATALOG_SYNC"
        })))
        self.tasks += [
            asyncio.create_task(self.worker())
            for _ in range(max(0, self.settings.global_research_concurrency - 1))
        ]
        # Keep one execution lane for user-triggered zero-result research. Supplier
        # leases still prevent two jobs from driving the same adapter concurrently.
        self.tasks.append(asyncio.create_task(self.worker({"TARGETED_RESEARCH"})))
        self.tasks.append(asyncio.create_task(self.worker({
            "OBSERVATION_REFRESH", "AVAILABILITY_REFRESH", "TARGETED_REFRESH"
        })))

    async def close(self):
        for task in self.tasks:
            task.cancel()
        for task in self.tasks:
            with suppress(asyncio.CancelledError):
                await task

    async def schedule(self):
        boot = utcnow()
        while True:
            try:
                for index, supplier in enumerate(self.providers):
                    now = utcnow()
                    async with self.repository.sessions() as db, db.begin():
                        stmt = self.repository.insert(db, SupplierSyncState).values(
                            supplier=supplier,
                            next_discovery_at=now
                            + timedelta(seconds=index * self.settings.scheduler_stagger_seconds),
                            next_refresh_at=now
                            + timedelta(seconds=index * self.settings.scheduler_stagger_seconds),
                            next_incremental_at=now
                            + timedelta(seconds=index * self.settings.scheduler_stagger_seconds),
                            next_reconciliation_at=now
                            + timedelta(seconds=index * self.settings.scheduler_stagger_seconds),
                        )
                        await db.execute(stmt.on_conflict_do_nothing())
                        state = await db.get(SupplierSyncState, supplier)
                        # SQLite may return naive datetimes in tests.
                        reconciliation_at = state.next_reconciliation_at or state.next_discovery_at
                        incremental_at = state.next_incremental_at or state.next_discovery_at
                        discovery_due = reconciliation_at.replace(tzinfo=now.tzinfo) <= now
                        incremental_due = incremental_at.replace(tzinfo=now.tzinfo) <= now
                        refresh_due = state.next_refresh_at.replace(tzinfo=now.tzinfo) <= now
                    if discovery_due:
                        await self.repository.enqueue(
                            supplier,
                            "FULL_CATALOG_RECONCILIATION",
                            {"reason": "scheduled_full_reconciliation",
                             "parser_version": self.providers[supplier].parser_version},
                            available_at=max(
                                now, boot + timedelta(seconds=index * self.settings.scheduler_stagger_seconds)
                            ),
                        )
                    full = await self.repository.active_job(
                        supplier, {"CATALOG_DISCOVERY", "FULL_CATALOG_RECONCILIATION"}
                    )
                    if incremental_due and not full:
                        await self.repository.enqueue(
                            supplier, "INCREMENTAL_CATALOG_SYNC",
                            {"reason": "scheduled_incremental"}, priority=40,
                            available_at=max(
                                now, boot + timedelta(seconds=index * self.settings.scheduler_stagger_seconds)
                            ),
                        )
                    if refresh_due:
                        due, _counts = await self.repository.adaptive_refresh_candidates(
                            supplier, limit=self.settings.targeted_refresh_limit
                        )
                    else:
                        due = []
                    if due:
                        priority = {"HOT": 80, "WARM": 60, "COLD": 20}[due[0][2]]
                        await self.repository.enqueue(
                            supplier,
                            "AVAILABILITY_REFRESH",
                            {"pending": [url for _, url, _, _ in due],
                             "offer_ids": [offer for offer, _, _, _ in due],
                             "reason": "adaptive_availability",
                             "parser_version": self.providers[supplier].parser_version},
                            priority=priority,
                            available_at=max(
                                now, boot + timedelta(seconds=index * self.settings.scheduler_stagger_seconds)
                            ),
                        )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log_event("index_scheduler_error", error_type=type(exc).__name__)
            await asyncio.sleep(5)

    async def worker(self, kinds=None):
        while True:
            try:
                job = await self.repository.claim(self.owner, kinds=kinds)
                if job:
                    await self.run(job)
                else:
                    await asyncio.sleep(1)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log_event("index_worker_error", error_type=type(exc).__name__)
                await asyncio.sleep(5)

    async def request(self, job, call, metric):
        if await self.repository.cancelled(job["id"]):
            raise JobCancelled()
        for attempt in range(self.settings.provider_retries + 1):
            await asyncio.sleep(
                self.settings.research_provider_delays.get(
                    job["supplier"], self.settings.research_request_delay_seconds
                )
                * (2**attempt)
            )
            start = time.perf_counter()
            try:
                result = await call()
                job["metrics"]["requests_per_supplier"] = job["metrics"].get("requests_per_supplier", 0) + 1
                job["metrics"][metric] = job["metrics"].get(metric, 0) + 1
                job["metrics"][metric + "_ms"] = round(
                    job["metrics"].get(metric + "_ms", 0) + (time.perf_counter() - start) * 1000, 2
                )
                return result
            except SupplierError as exc:
                if "403" in str(exc) or exc.code == "SUPPLIER_HTTP_ERROR":
                    job["metrics"]["http_403"] = job["metrics"].get("http_403", 0) + 1
                if "429" in str(exc):
                    job["metrics"]["http_429"] = job["metrics"].get("http_429", 0) + 1
                if not exc.retryable or attempt == self.settings.provider_retries:
                    raise

    async def run(self, job):
        started = time.perf_counter()
        job["started_perf"] = started
        status, error = "COMPLETED", None
        try:
            await self.safety.begin(job)
            timeout = (self.settings.targeted_provider_timeout_seconds
                       if job["kind"] == "TARGETED_RESEARCH"
                       else self.settings.research_job_timeout_seconds)
            async with asyncio.timeout(timeout):
                provider = self.providers[job["supplier"]]
                if job["kind"] in ("OBSERVATION_REFRESH", "AVAILABILITY_REFRESH", "TARGETED_REFRESH"):
                    await self.refresh(job, provider)
                elif job["kind"] == "TARGETED_RESEARCH":
                    await self.targeted(job, provider)
                else:
                    await self.discover(job, provider)
                    if any(branch['status'] != 'COMPLETE' for branch in job['checkpoint'].get('branches', [])):
                        status, error = 'PENDING', 'COVERAGE_LIMITED'
                    if job['kind'] == 'CATALOG_DISCOVERY' and not completion(job['checkpoint']):
                        status, error = 'PENDING', 'COVERAGE_LIMITED'
                if not await self.safety.finish(job, status == 'COMPLETED'):
                    status, error = 'FAILED', 'PARSER_ANOMALY'
        except TimeoutError:
            status, error = "PENDING", "TIME_BUDGET"
        except JobCancelled:
            status = "CANCELLED"
        except YieldJob:
            status, error = "PENDING", None
        except asyncio.CancelledError:
            status = "CANCELLED" if await self.repository.cancelled(job["id"]) else "PENDING"
            raise
        except SupplierError as exc:
            status, error = "PENDING", exc.code
        except Exception as exc:
            status, error = "PENDING", type(exc).__name__
            log_event("index_job_error", supplier=job["supplier"], error_type=error)
        finally:
            if status == 'PENDING' and job['checkpoint'].get('run_id'):
                try:
                    if not await self.safety.finish(job, False):
                        status, error = 'FAILED', 'PARSER_ANOMALY'
                except Exception as exc:
                    # Publishing a slice must not prevent checkpoint/lease release.
                    error = type(exc).__name__
                    log_event('index_slice_publish_error', supplier=job['supplier'], error_type=error)
            job["metrics"]["duration_ms"] = round(
                job["metrics"].get("duration_ms", 0) + (time.perf_counter() - started) * 1000, 2
            )
            duration_minutes = max(job["metrics"]["duration_ms"] / 60000, 1 / 60)
            observed = job["metrics"].get("products_seen", 0)
            requests = job["metrics"].get("requests_per_supplier", 0)
            job["metrics"]["products_per_minute"] = round(observed / duration_minutes, 2)
            job["metrics"]["requests_per_minute"] = round(requests / duration_minutes, 2)
            job["metrics"]["requests_per_product"] = round(requests / observed, 3) if observed else None
            changed = job["metrics"].get("products_changed", 0)
            unchanged = job["metrics"].get("products_unchanged", 0)
            job["metrics"]["useful_change_rate"] = round(changed / (changed + unchanged), 4) \
                if changed + unchanged else None
            if job["kind"] == "TARGETED_RESEARCH":
                job["metrics"]["targeted_total_ms"] = job["metrics"]["duration_ms"]
                job["metrics"]["supplier_targeted_search_ms"] = round(
                    job["metrics"].get("pages_opened_ms", 0)
                    + job["metrics"].get("product_pages_opened_ms", 0), 2
                )
            if error:
                job["metrics"]["errors"] = job["metrics"].get("errors", 0) + 1
                if job["kind"] == "OBSERVATION_REFRESH":
                    async with self.repository.sessions() as db, db.begin():
                        from sqlalchemy import select, update

                        from app.database.models import ProductObservation, SupplierOffer

                        await db.execute(
                            update(ProductObservation)
                            .where(
                                ProductObservation.status == "REFRESHING",
                                ProductObservation.offer_id.in_(
                                    select(SupplierOffer.id).where(SupplierOffer.supplier == job["supplier"])
                                ),
                            )
                            .values(status="FAILED_REFRESH")
                        )
            await self.repository.checkpoint(job["id"], job["checkpoint"], job["metrics"])
            await self.repository.finish(job, self.owner, status, error)

    async def store(self, job, products):
        await self.safety.stage(job, products)
        # New discoveries become searchable immediately. Existing observations are
        # published as a validated run so an aggregate stock collapse is quarantined.
        from sqlalchemy import select

        from app.database.models import SupplierOffer
        async with self.repository.sessions() as db:
            known = set((await db.scalars(select(SupplierOffer.id).where(SupplierOffer.id.in_([p.id for p in products])))).all())
        await self.repository.ingest([p for p in products if p.id not in known])
        job["metrics"]["products_seen"] = job["metrics"].get("products_seen", 0) + len(products)
        job["metrics"]["products_updated"] = job["metrics"].get("products_updated", 0) + len(products)
        if self.on_update:
            await self.on_update(job)

    async def refresh(self, job, provider):
        checkpoint = job["checkpoint"]
        if "pending" not in checkpoint:
            due, counts = await self.repository.adaptive_refresh_candidates(
                job["supplier"], limit=self.settings.targeted_refresh_limit
            )
            checkpoint["pending"] = [url for _, url, _, _ in due]
            checkpoint["refresh_tiers"] = counts
        checkpoint['pending'] = list(dict.fromkeys(provider.observation_group_key(url) for url in checkpoint['pending']))
        processed = 0
        while checkpoint["pending"]:
            products = await self.request(
                job, lambda: provider.research_products(checkpoint["pending"][0]), "product_pages_opened"
            )
            await self.store(job, products)
            checkpoint["pending"].pop(0)
            await self.repository.checkpoint(job["id"], checkpoint, job["metrics"])
            processed += 1
            if (processed >= self.settings.background_slice_products and checkpoint["pending"]
                    and await self.repository.higher_priority_pending(job.get("priority", 0))):
                raise YieldJob()

    async def targeted(self, job, provider):
        """One relevant supplier branch/search page per interactive execution slice."""
        checkpoint = job["checkpoint"]
        if "branches" not in checkpoint:
            branch = ResearchCoverage(
                supplier=job["supplier"], category=checkpoint.get("category", ""),
                query=checkpoint["query"], route=checkpoint.get("route"),
            )
            if branch.route:
                branch.cursor.listing_url = branch.route
            checkpoint["branches"] = [branch.model_dump(mode="json") | {
                "label": checkpoint.get("label", ""),
                "category_graph_hit": bool(branch.route),
                "supplier_search_used": not bool(branch.route),
            }]
            await self.repository.checkpoint(job["id"], checkpoint, job["metrics"])
        item = checkpoint["branches"][0]
        branch = ResearchCoverage.model_validate(item)
        job["metrics"]["category_graph_hit"] = bool(item.get("category_graph_hit"))
        job["metrics"]["supplier_search_used"] = bool(item.get("supplier_search_used"))
        cursor = branch.cursor
        if cursor.exhausted:
            return
        if not cursor.listing_loaded:
            page = await self.request(job, lambda: provider.research_listing(branch), "pages_opened")
            branch.pages_scanned += 1
            branch.listings_seen += len(page.urls)
            cursor.pending_urls = [u for u in page.urls if u not in cursor.visited_urls]
            cursor.next_url, cursor.listing_loaded = page.next_url, True
            branch.pagination_exhausted = page.exhausted
            item.update(branch.model_dump(mode="json"))
            await self.repository.checkpoint(job["id"], checkpoint, job["metrics"])
        batch = []
        while cursor.pending_urls:
            url = cursor.pending_urls[0]
            products = await self.request(job, lambda: provider.research_products(url), "product_pages_opened")
            if not branch.route:
                observed_route = provider.targeted_category_route(url)
                if observed_route:
                    branch.route = observed_route
                    item["route"] = observed_route
                    item["label"] = checkpoint["query"]
                    checkpoint["discovered_route"] = observed_route
            for product in products:
                label = item.get("label", "")
                if label:
                    product.metadata.setdefault("supplier_category", label)
                    product.metadata.setdefault("breadcrumbs", [label])
            # Commit the first result immediately, then use small batches so a
            # large category does not turn PostgreSQL round trips into minutes.
            batch.extend(products)
            first = "time_to_first_product_ms" not in job["metrics"]
            if batch and (first or len(batch) >= 8):
                await self.store(job, batch)
                batch = []
            if products and first:
                job["metrics"]["time_to_first_product_ms"] = round(
                    (time.perf_counter() - job["started_perf"]) * 1000, 2
                )
                job["metrics"]["time_to_first_result_event_ms"] = job["metrics"]["time_to_first_product_ms"]
            job["metrics"]["products_matched"] = job["metrics"].get("products_matched", 0) + len(products)
            branch.products_seen += len(products)
            cursor.visited_urls.append(cursor.pending_urls.pop(0))
            item.update(branch.model_dump(mode="json"))
            await self.repository.checkpoint(job["id"], checkpoint, job["metrics"])
        if batch:
            await self.store(job, batch)
        if branch.pagination_exhausted or not cursor.next_url:
            cursor.exhausted = True
            branch.status = CoverageStatus.COMPLETE
        else:
            cursor.listing_url, cursor.page_number, cursor.listing_loaded = (
                cursor.next_url, cursor.page_number + 1, False
            )
            item.update(branch.model_dump(mode="json"))
            await self.repository.checkpoint(job["id"], checkpoint, job["metrics"])
            return await self.targeted(job, provider)
        item.update(branch.model_dump(mode="json"))

    async def discover(self, job, provider):
        checkpoint = job["checkpoint"]
        if job['kind'] == 'CATALOG_DISCOVERY' and 'branches' in checkpoint and not checkpoint.get('roots_observed'):
            checkpoint['legacy_query_coverage'] = checkpoint.pop('branches')
        if "branches" not in checkpoint:
            if checkpoint.get("query"):
                pairs = [(checkpoint.get("category", ""), checkpoint["query"])]
            else:
                navigation = await self.request(job, lambda: provider.catalog_navigation(), 'navigation_pages')
                checkpoint['roots_observed'] = True
                checkpoint['roots'] = [r.model_dump() for r in navigation.routes]
                pairs = []
            checkpoint["branches"] = [
                ResearchCoverage(supplier=job["supplier"], category=c, query=q).model_dump(mode="json")
                for c, q in pairs
            ]
            for route in checkpoint.get('roots', []):
                branch = ResearchCoverage(supplier=job['supplier'], category='', query='', route=route['url'])
                branch.cursor.listing_url = route['url']
                checkpoint['branches'].append(branch.model_dump(mode='json') | {'label': route['label']})
            await self.repository.checkpoint(job['id'], checkpoint, job['metrics'])
        repaired_routes = False
        for item in checkpoint['branches']:
            # Repair checkpoints written by adapters that cleared published routes.
            # Reuse only a route already present in the observed root graph.
            if not item.get('route') and not item.get('query'):
                listing = item.get('cursor', {}).get('listing_url', '') or ''
                route = next((root['url'] for root in checkpoint.get('roots', [])
                              if root['url'].split('?', 1)[0] == listing.split('?', 1)[0]), None)
                if route:
                    item['route'] = route
                    repaired_routes = True
        if repaired_routes:
            await self.repository.checkpoint(job['id'], checkpoint, job['metrics'])
        processed_in_slice = 0
        for item in checkpoint["branches"]:
            branch = ResearchCoverage.model_validate(item)
            if branch.status == "COMPLETE":
                continue
            cursor = branch.cursor
            try:
                branch.error_code = None
                if branch.route and not item.get('navigation_observed'):
                    navigation = await self.request(job, lambda: provider.catalog_navigation(branch.route), 'navigation_pages')
                    known = {b.get('route') for b in checkpoint['branches']}
                    for route in navigation.routes:
                        if route.url not in known:
                            child = ResearchCoverage(supplier=job['supplier'], category='', query='', route=route.url)
                            child.cursor.listing_url = route.url
                            checkpoint['branches'].append(child.model_dump(mode='json') | {'label': route.label, 'parent_url': branch.route})
                            known.add(route.url)
                    item['navigation_observed'] = True
                    await self.repository.checkpoint(job['id'], checkpoint, job['metrics'])
                while not cursor.exhausted:
                    if not cursor.listing_loaded:
                        page = await self.request(
                            job, lambda: provider.research_listing(branch), "pages_scanned"
                        )
                        branch.pages_scanned += 1
                        branch.listings_seen += len(page.urls)
                        branch.total_results_reported, branch.total_unit = page.total, page.total_unit
                        branch.pagination_exhausted = page.exhausted
                        cursor.pending_urls = [u for u in page.urls if u not in cursor.visited_urls]
                        if job["kind"] == "INCREMENTAL_CATALOG_SYNC":
                            known = await self.repository.source_urls_known(job["supplier"], cursor.pending_urls)
                            job["metrics"]["detail_pages_avoided"] = (
                                job["metrics"].get("detail_pages_avoided", 0) + len(known)
                            )
                            cursor.visited_urls.extend(u for u in cursor.pending_urls if u in known)
                            cursor.pending_urls = [u for u in cursor.pending_urls if u not in known]
                        cursor.next_url, cursor.listing_loaded = page.next_url, True
                    batch = []
                    completed_urls = []
                    while cursor.pending_urls:
                        url = cursor.pending_urls[0]
                        products = await self.request(
                            job, lambda: provider.research_products(url), "product_pages_opened"
                        )
                        for product in products:
                            if branch.route:
                                product.metadata.setdefault('supplier_category', item.get('label', ''))
                                product.metadata.setdefault('breadcrumbs', [item.get('label', '')])
                        for product in products:
                            for variant in product.metadata.pop("variant_urls", []):
                                variant = variant.get("url") if isinstance(variant, dict) else variant
                                if (
                                    isinstance(variant, str)
                                    and variant not in cursor.pending_urls
                                    and variant not in cursor.visited_urls
                                ):
                                    cursor.pending_urls.append(variant)
                        batch.extend(products)
                        branch.products_seen += len(products)
                        completed_urls.append(cursor.pending_urls.pop(0))
                        processed_in_slice += 1
                        # Publish bounded transactions. A crash before this boundary
                        # leaves the URLs pending in the durable checkpoint; replay is
                        # idempotent because offer IDs and CatalogSeen keys are stable.
                        flush = (
                            len(batch) >= self.settings.background_slice_products
                            or not cursor.pending_urls
                        )
                        if flush:
                            if batch:
                                await self.store(job, batch)
                            cursor.visited_urls.extend(completed_urls)
                            batch, completed_urls = [], []
                            item.update(branch.model_dump(mode="json"))
                            await self.repository.checkpoint(job["id"], checkpoint, job["metrics"])
                        if (processed_in_slice >= self.settings.background_slice_products
                                and await self.repository.higher_priority_pending(job.get("priority", 0))):
                            raise YieldJob()
                    if branch.pagination_exhausted:
                        cursor.exhausted = True
                    elif cursor.next_url and cursor.next_url != cursor.listing_url:
                        cursor.listing_url, cursor.page_number, cursor.listing_loaded = (
                            cursor.next_url,
                            cursor.page_number + 1,
                            False,
                        )
                    else:
                        raise SupplierError(
                            "PAGINATION_NOT_EXHAUSTED", "Cannot confirm pagination exhaustion"
                        )
                seen = branch.products_seen if branch.total_unit == "offers" else branch.listings_seen
                branch.status = (
                    CoverageStatus.COMPLETE
                    if branch.total_results_reported is None or seen >= branch.total_results_reported
                    else CoverageStatus.LIMITED
                )
            except asyncio.CancelledError:
                # A slow branch must not consume every execution slice forever.
                # Keep its cursor, then let the next slice visit another branch.
                branch.status = CoverageStatus.LIMITED
                checkpoint['branches'].remove(item)
                checkpoint['branches'].append(item)
                raise
            except SupplierError as exc:
                branch.error_code = exc.code
                branch.status = CoverageStatus.CAPTCHA if exc.code == 'SUPPLIER_CAPTCHA_REQUIRED' else CoverageStatus.FAILED
                if exc.code == 'SUPPLIER_CAPTCHA_REQUIRED':
                    raise
                # A broken branch cannot starve the other published catalog branches.
                continue
            finally:
                item.update(branch.model_dump(mode="json"))
                await self.repository.checkpoint(job["id"], checkpoint, job["metrics"])
