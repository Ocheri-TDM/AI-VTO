"""Durable, resumable worker. HTTP/SSE readers never own its task lifetime."""
import asyncio
import time
from contextlib import suppress
from datetime import timedelta

from app.application.research_analysis import remember, undo
from app.application.research_planner import ResearchPlanner
from app.domain.errors import CurrencyError, SupplierError
from app.domain.intent import CategoryResolver
from app.domain.models import utcnow
from app.domain.research import (
    CoverageStatus,
    ResearchBudget,
    ResearchJobStatus,
    ResearchSession,
    ResearchView,
)
from app.domain.services import ColorNormalizer, CurrencyService, normalize_text
from app.observability import log_event


class ResearchService:
    def __init__(self, repository, providers, settings, model=None):
        self.repository, self.settings, self.model = repository, settings, model
        self.providers = {p.supplier: p for p in providers}
        self.planner = ResearchPlanner()
        self.tasks = {}
        self.job_ids = {}
        self.followups = {}
        self.closed = False
        self.active = {}
        self.locks = {}
        self.request_gate = asyncio.Semaphore(settings.research_supplier_concurrency)

    def lock(self, identifier):
        return self.locks.setdefault(identifier, asyncio.Lock())

    async def get(self, identifier):
        return self.active.get(identifier) or await self.repository.get(identifier)

    async def start(self, chat_id, query, budget=None):
        intent = await self.planner.parse(query)
        previous = await self.repository.latest(chat_id)
        if previous and normalize_text(previous.intent.raw_query) == normalize_text(query) \
                and previous.intent.model_dump(exclude={'raw_query'}) == intent.model_dump(exclude={'raw_query'}) \
                and utcnow() < previous.created_at + timedelta(seconds=self.settings.cache_ttl_seconds):
            return await self.get(previous.id)
        budget = budget or ResearchBudget(max_seconds=self.settings.research_max_seconds)
        research = ResearchSession(chat_id=chat_id, intent=intent, budget=budget,
                                   coverage=self.planner.plan(intent, budget))
        if intent.budget:
            research.view.filters.min_price = intent.budget.min
            research.view.filters.max_price = intent.budget.max
        if intent.sorting in ('relevance', 'price_asc', 'price_desc'):
            research.view.sorting = intent.sorting
        research.messages.append({'role': 'user', 'content': query})
        await self.repository.save(research)
        await self.resume(research.id)
        return await self.get(research.id)

    async def resume(self, identifier, branch_ids=None):
        async with self.lock(identifier):
            if identifier in self.tasks and not self.tasks[identifier].done():
                if branch_ids:
                    self.followups.setdefault(identifier, set()).update(branch_ids)
                return await self.get(identifier)
            research = await self.get(identifier)
            if not research:
                raise KeyError(identifier)
            self.active[identifier] = research
            research.job_status = ResearchJobStatus.QUEUED
            await self.repository.save(research)
            job_id = await self.repository.job(identifier)
            self.job_ids[identifier] = job_id
            self.tasks[identifier] = asyncio.create_task(self._run(research, job_id, branch_ids), name=f'research-{identifier}')
            def follow_up(_task):
                branches = self.followups.pop(identifier, None)
                if branches and not self.closed and research.job_status != ResearchJobStatus.CANCELLED:
                    asyncio.create_task(self.resume(identifier, branches))
            self.tasks[identifier].add_done_callback(follow_up)
            return research

    async def _commit(self, research):
        async with self.lock(research.id):
            await self.repository.save(research)

    async def _run(self, research, job_id, branch_ids=None):
        deadline = time.monotonic() + research.budget.max_seconds
        research.job_status = ResearchJobStatus.RUNNING
        await self.repository.job_status(job_id, research.job_status)
        await self._commit(research)
        route_budget = research.budget.max_supplier_routes
        remaining = [b for b in research.coverage if b.status != CoverageStatus.COMPLETE
                     and (branch_ids is None or b.id in branch_ids)]
        # Round-robin category/query branches per supplier so all six sources get a turn.
        by_supplier = {s: [b for b in remaining if b.supplier == s] for s in self.providers}
        selected = []
        while any(by_supplier.values()) and len(selected) < route_budget:
            for branches in by_supplier.values():
                if branches and len(selected) < route_budget:
                    selected.append(branches.pop(0))
        categories = list(dict.fromkeys(b.category for b in selected))[:research.budget.max_category_branches]
        selected = [b for b in selected if b.category in categories]

        async def supplier_work(supplier):
            for branch in [b for b in selected if b.supplier == supplier]:
                if time.monotonic() >= deadline:
                    break
                try:
                    remaining_time = max(.01, deadline-time.monotonic())
                    slice_seconds = min(remaining_time, self.settings.research_branch_slice_seconds) \
                        if len(research.intent.categories) > 1 else remaining_time
                    async with asyncio.timeout(slice_seconds):
                        await self._branch(research, branch, self.providers[supplier])
                except TimeoutError:
                    branch.status = CoverageStatus.LIMITED
                    branch.error_code = 'RESEARCH_TIME_BUDGET' if time.monotonic() >= deadline else 'RESEARCH_BRANCH_SLICE'
                except SupplierError as exc:
                    branch.status = (CoverageStatus.CAPTCHA if exc.code == 'SUPPLIER_CAPTCHA_REQUIRED'
                                     else CoverageStatus.TIMEOUT if 'TIMEOUT' in exc.code else CoverageStatus.FAILED)
                    branch.error_code = exc.code
                    log_event('research_supplier_error', supplier=supplier, code=exc.code)
                except asyncio.CancelledError:
                    branch.status = CoverageStatus.CANCELLED
                    raise
                except Exception as exc:
                    branch.status = CoverageStatus.FAILED
                    branch.error_code = 'SUPPLIER_PARSING_ERROR'
                    log_event('research_supplier_error', supplier=supplier, error_type=type(exc).__name__)
                finally:
                    branch.completed_at = utcnow()
                    await self._commit(research)
                if branch.status == CoverageStatus.CAPTCHA:
                    break
        try:
            await asyncio.gather(*(supplier_work(s) for s in self.providers))
            research.job_status = (ResearchJobStatus.COMPLETED if all(
                b.status == CoverageStatus.COMPLETE for b in research.coverage) else ResearchJobStatus.PARTIAL)
        except asyncio.CancelledError:
            research.job_status = ResearchJobStatus.CANCELLED
        finally:
            await self._commit(research)
            await self.repository.job_status(job_id, research.job_status)
            self.active.pop(research.id, None)

    async def _request(self, call, supplier=None):
        for attempt in range(self.settings.provider_retries + 1):
            delay = self.settings.research_provider_delays.get(supplier, self.settings.research_request_delay_seconds)
            await asyncio.sleep(max(.2, min(30, delay)) * (2 ** attempt))
            try:
                async with self.request_gate:
                    return await call()
            except SupplierError as exc:
                if not exc.retryable or attempt == self.settings.provider_retries:
                    raise

    async def _branch(self, research, branch, provider):
        branch.started_at = branch.started_at or utcnow()
        branch.status, branch.error_code = CoverageStatus.LIMITED, None
        cursor = branch.cursor
        cursor.pending_urls.extend(u for u in cursor.failed_urls if u not in cursor.pending_urls)
        cursor.failed_urls = []
        failed_urls = cursor.failed_urls
        branch.parsing_errors = 0
        currency = CurrencyService(self.settings.rub_kzt_rate)
        while not cursor.exhausted:
            if not cursor.listing_loaded:
                page = await self._request(lambda: provider.research_listing(branch), provider.supplier)
                cursor.pending_urls = [u for u in page.urls if u not in cursor.visited_urls]
                cursor.next_url = page.next_url
                cursor.listing_loaded = True
                branch.pages_scanned += 1
                branch.listings_seen += len(page.urls)
                branch.total_results_reported = page.total or branch.total_results_reported
                branch.total_unit = page.total_unit
                branch.pagination_exhausted = page.exhausted
                await self._commit(research)
            while cursor.pending_urls:
                url = cursor.pending_urls[0]
                # On failure keep the URL pending. A continuation retries precisely this observation.
                try:
                    products = await self._request(lambda: provider.research_products(url), provider.supplier)
                except SupplierError as exc:
                    if exc.code == 'SUPPLIER_CAPTCHA_REQUIRED':
                        raise
                    failed_urls.append(cursor.pending_urls.pop(0))
                    branch.parsing_errors += 1
                    branch.error_code = exc.code
                    await self._commit(research)
                    continue
                existing = {p.id: p for p in research.products}
                for product in products:
                    product.colors = [ColorNormalizer().normalize(c.original_color) for c in product.colors]
                    attrs = product.metadata.get('attributes', {})
                    for field, names in {'material': ('Материал', 'Материал товара'),
                                         'brand': ('Бренд',), 'dimensions': ('Размер', 'Размер товара'),
                                         'capacity': ('Объем в литрах', 'Вместимость')}.items():
                        if not getattr(product, field):
                            value = next((attrs[n] for n in names if isinstance(attrs.get(n), str)), None)
                            if value:
                                setattr(product, field, value)
                    for field in ('stock_quantity', 'original_price', 'original_currency', 'material', 'brand', 'capacity'):
                        value = product.model_dump(mode='json')[field]
                        if value is not None and field not in product.evidence:
                            product.evidence[field] = {'value': value, 'source_url': product.source_url,
                                                       'source': 'supplier_observation'}
                    for variant in product.metadata.pop('variant_urls', []):
                        if isinstance(variant, dict):
                            variant = variant.get('url')
                        if not isinstance(variant, str):
                            continue
                        if variant not in cursor.visited_urls and variant not in cursor.pending_urls:
                            cursor.pending_urls.append(variant)
                    branch.products_seen += 1
                    categories = CategoryResolver().classify(product.name)
                    valid = branch.category in categories
                    if branch.category == 'lanyard':
                        valid = any(t in product.name.casefold() for t in ('ланъярд', 'бейдж'))
                    if branch.category == 'accessory' and not categories:
                        valid = any(t in product.name.casefold() for t in ('держатель', 'аксессуар'))
                    if research.intent.quantity is not None:
                        valid = valid and product.stock_quantity is not None and product.stock_quantity >= research.intent.quantity
                    if research.intent.colors:
                        valid = valid and any(c.normalized_color in research.intent.colors for c in product.colors)
                    try:
                        if research.intent.budget and (product.original_price is None or not product.original_currency):
                            valid = False
                        elif product.original_price is not None and product.original_currency:
                            product.price_kzt = currency.to_kzt(product.original_price, product.original_currency)
                    except CurrencyError:
                        valid = False
                    if valid:
                        product.category = branch.category
                        product.metadata['color_match'] = ('exact' if any(c.normalized_color in research.intent.primary_colors
                            for c in product.colors) else 'close') if research.intent.colors else 'exact'
                        existing[product.id] = product
                        branch.products_validated += 1
                    else:
                        branch.products_rejected += 1
                        if product.id in existing:
                            product.metadata['research_invalid'] = True
                            existing[product.id] = product
                research.products = list(existing.values())
                cursor.visited_urls.append(cursor.pending_urls.pop(0))
                await self._commit(research)
            if branch.pagination_exhausted:
                cursor.exhausted = True
            elif cursor.next_url and cursor.next_url != cursor.listing_url:
                cursor.listing_url = cursor.next_url
                cursor.page_number += 1
                cursor.listing_loaded = False
            else:
                raise SupplierError('SUPPLIER_PAGINATION_LOOP', 'Pagination failed to advance')
        if failed_urls:
            cursor.pending_urls = list(failed_urls)
            cursor.failed_urls = []
            cursor.exhausted = False
            branch.status = CoverageStatus.LIMITED
        elif branch.total_results_reported is not None and (
            branch.products_seen if branch.total_unit == 'offers' else branch.listings_seen
        ) < branch.total_results_reported:
            branch.status = CoverageStatus.LIMITED
            branch.error_code = 'SUPPLIER_TOTAL_COUNT_MISMATCH'
        else:
            branch.status = CoverageStatus.COMPLETE

    async def cancel(self, identifier):
        self.followups.pop(identifier, None)
        task = self.tasks.get(identifier)
        if task and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            research = await self.get(identifier)
            if research.job_status in (ResearchJobStatus.QUEUED, ResearchJobStatus.RUNNING):
                research.job_status = ResearchJobStatus.CANCELLED
                await self._commit(research)
                await self.repository.job_status(self.job_ids[identifier], research.job_status)
                self.active.pop(identifier, None)
        return await self.get(identifier)

    async def close(self):
        self.closed = True
        for task in self.tasks.values():
            task.cancel()
        for task in self.tasks.values():
            with suppress(asyncio.CancelledError):
                await task

    async def change(self, identifier, *, filters=None, sorting=None, selected_ids=None, restore=None, version=None):
        async with self.lock(identifier):
            research = await self.get(identifier)
            if research is None:
                raise KeyError(identifier)
            if version is not None and version != research.view.version:
                raise ValueError('STATE_CONFLICT')
            known = {p.id for p in research.products}
            if selected_ids is not None and not set(selected_ids).issubset(known):
                raise ValueError('UNKNOWN_PRODUCT')
            if restore == 'undo':
                undo(research)
            else:
                remember(research, restore or 'Изменение подборки')
                if restore == 'reset':
                    research.view = ResearchView(version=research.view.version,
                                                 selected_ids=research.view.selected_ids)
                if filters is not None:
                    research.view.filters = filters
                if sorting is not None:
                    research.view.sorting = sorting
                if selected_ids is not None:
                    research.view.selected_ids = selected_ids
            await self.repository.save(research)
            return research
