"""User research consumes the index; enqueue is the only connection to crawling."""

import asyncio
import re
import time

from sqlalchemy import select

from app.application.index_query import IndexQueryService
from app.application.product_grid import ProductGridMixin
from app.application.research import ResearchService
from app.database.models import CatalogSyncJob
from app.domain.research import ResearchJobStatus, ResearchSession
from app.domain.taxonomy import UniversalCategoryResolver
from app.observability import log_event


class IndexedResearchService(ProductGridMixin, ResearchService):
    def __init__(self, repository, providers, settings, model, index, query_understanding=None):
        super().__init__(repository, providers, settings, model)
        self.index = index
        self.query_engine = IndexQueryService(index)
        self.last_update = 0
        self.query_understanding = query_understanding
        self._usage_pending: set[str] = set()
        self._usage_task: asyncio.Task | None = None

    def schedule_usage_mark(self, offer_ids: list[str]) -> None:
        """Coalesce hot-result signals so concurrent searches never serialize
        identical multi-thousand-row updates on the HTTP path.
        """
        self._usage_pending.update(offer_ids)
        if self._usage_task is None or self._usage_task.done():
            self._usage_task = asyncio.create_task(self._flush_usage(), name="index-usage-batch")

    async def _flush_usage(self) -> None:
        await asyncio.sleep(0.05)
        while self._usage_pending:
            ids = list(self._usage_pending)
            self._usage_pending.clear()
            started = time.perf_counter()
            try:
                await self.index.mark_usage(ids, "searched")
                log_event(
                    "index_usage_batch", offers=len(ids),
                    duration_ms=round((time.perf_counter() - started) * 1000, 2),
                )
            except Exception as exc:
                log_event("index_usage_batch_error", offers=len(ids), error_type=type(exc).__name__)

    async def close(self):
        if self._usage_task and not self._usage_task.done():
            self._usage_task.cancel()
            await asyncio.gather(self._usage_task, return_exceptions=True)
        await super().close()

    async def start(self, chat_id, query, budget=None):
        return await self.grid_start(chat_id, query, budget)

    async def legacy_start(self, chat_id, query, budget=None):
        started = time.perf_counter()
        loop_probe = time.perf_counter()
        await asyncio.sleep(0)
        initial_event_loop_lag_ms = (time.perf_counter() - loop_probe) * 1000
        understanding_started = time.perf_counter()
        understanding = (
            await self.query_understanding.understand(query)
            if self.query_understanding
            else None
        )
        query_understanding_ms = (time.perf_counter() - understanding_started) * 1000
        planner_started = time.perf_counter()
        intent = await self.planner.parse(
            understanding.search_query if understanding else query
        )
        deterministic_planning_ms = (time.perf_counter() - planner_started) * 1000
        if understanding:
            intent.raw_query = understanding.original_query
            intent.original_query = understanding.original_query
            intent.normalized_query = understanding.normalized_query
            intent.query_understanding = understanding.model_dump(mode="json")
        parse_ms = (time.perf_counter() - started) * 1000
        research = ResearchSession(
            chat_id=chat_id, intent=intent, source_mode="index", job_status="COMPLETED"
        )
        if budget:
            research.budget = budget
        coverage_started = time.perf_counter()
        research.coverage = self.planner.plan(intent, research.budget)
        coverage_planning_ms = (time.perf_counter() - coverage_started) * 1000
        research.products, research.timings = await self.query_engine.search(intent)
        research.indexed_offer_ids = [p.id for p in research.products]
        research.view.preferences = list(intent.soft_terms) + [k for k,v in intent.preferences.model_dump().items() if v]
        research.messages.append({"role": "user", "content": query})
        research.timings["intent_parse_ms"] = round(parse_ms, 2)
        if understanding:
            research.timings["query_normalization_ms"] = understanding.latency_ms
        if intent.budget:
            research.view.filters.min_price, research.view.filters.max_price = (
                intent.budget.min,
                intent.budget.max,
            )
        persistence_started = time.perf_counter()
        await self.repository.save(research)
        initial_persistence_ms = (time.perf_counter() - persistence_started) * 1000
        enqueue_started = time.perf_counter()
        stale = [p.id for p in research.products if p.metadata.get("availability_stale")
                 or p.metadata.get("procurement_status") == "NEEDS_REFRESH"]
        if stale:
            await self.enqueue_availability(research, stale, quantity=intent.quantity is not None)
        if not research.products or research.timings.get("metadata_gap"):
            await self.enqueue_target(research)
        background_enqueue_ms = (time.perf_counter() - enqueue_started) * 1000
        self.schedule_usage_mark(research.indexed_offer_ids)
        research.timings.update({
            "initial_event_loop_lag_ms": round(initial_event_loop_lag_ms, 2),
            "query_understanding_stage_ms": round(query_understanding_ms, 2),
            "deterministic_planning_ms": round(deterministic_planning_ms, 2),
            "coverage_planning_ms": round(coverage_planning_ms, 2),
            "hydrated_products": len(research.products),
            "mark_usage_ms": 0.0,
            "mark_usage_async": True,
            "initial_persistence_ms": round(initial_persistence_ms, 2),
            "background_enqueue_ms": round(background_enqueue_ms, 2),
        })
        research.timings["response_ms"] = round((time.perf_counter() - started) * 1000, 2)
        await self.repository.save(research)
        return research

    async def enqueue_availability(self, research, offer_ids, quantity=False):
        """Queue bounded verification after the index response has been committed."""
        requested = set(offer_ids)
        by_supplier: dict[str, list] = {}
        for product in research.products:
            if product.id in requested:
                by_supplier.setdefault(product.supplier, []).append(product)
        for supplier, products in by_supplier.items():
            # The caller already selected stale/uncertain observations. Use the
            # hydrated source URLs and cap the background checkpoint directly;
            # re-querying thousands of offers only to choose a small refresh
            # slice delayed purpose requests and raced the usage marker.
            selected = products[:self.settings.targeted_refresh_limit]
            if not selected:
                continue
            job_id = await self.index.enqueue(
                supplier, "TARGETED_REFRESH",
                {"pending": [product.source_url for product in selected],
                 "offer_ids": [product.id for product in selected],
                 "reason": "quantity_verification" if quantity else "hot_result"},
                priority=100 if quantity else 80,
            )
            if job_id not in research.background_job_ids:
                research.background_job_ids.append(job_id)
        await self.repository.save(research)

    async def enqueue_target(self, research, term=None, categories=None):
        term = term or research.intent.material
        branches = self.planner.plan(research.intent, research.budget)
        for branch in branches:
            if categories and branch.category not in categories:
                continue
            query = branch.query if not term else branch.query + " " + term
            graph = await self.index.category_route(branch.supplier, query)
            checkpoint = {"query": query, "category": branch.category}
            if graph:
                checkpoint.update({"route": graph["route"], "label": graph["label"],
                                   "category_graph_hit": True})
            identifier = await self.index.enqueue(
                branch.supplier,
                "TARGETED_RESEARCH",
                checkpoint,
                priority=100,
            )
            if identifier not in research.background_job_ids:
                research.background_job_ids.append(identifier)
        research.job_status = ResearchJobStatus.QUEUED
        await self.repository.save(research)

    async def resume(self, identifier, branch_ids=None):
        research = await self.get(identifier)
        await self.enqueue_target(research)
        return research

    async def refresh_index(self, identifier):
        research = await self.get(identifier)
        for supplier in self.providers:
            ids = [p.id for p in research.products if p.supplier == supplier]
            due, _ = await self.index.adaptive_refresh_candidates(
                supplier, ids, self.settings.targeted_refresh_limit
            )
            job_id = await self.index.enqueue(
                supplier, "TARGETED_REFRESH",
                {"pending": [url for _, url, _, _ in due], "offer_ids": ids,
                 "reason": "research_refresh"}, priority=100,
            )
            if job_id not in research.background_job_ids:
                research.background_job_ids.append(job_id)
        research.job_status = ResearchJobStatus.QUEUED
        await self.repository.save(research)
        return research

    async def cancel(self, identifier):
        research = await self.get(identifier)
        for job_id in research.background_job_ids:
            await self.index.cancel(job_id)
        research.job_status = ResearchJobStatus.CANCELLED
        await self.repository.save(research)
        return research

    async def sync(self, identifier):
        snapshot = await self.repository.get(identifier)
        if snapshot and snapshot.unified:
            return snapshot
        async with self.lock(identifier):
            research = await self.repository.get(identifier)
            if not research or research.source_mode != "index":
                return research
            fresh, timings = await self.query_engine.search(research.intent)
            # Retain previous observations/history; new strict results define current availability.
            pool = {p.id: p for p in research.products}
            zero_result_target = not pool
            for p in pool.values():
                p.metadata["research_invalid"] = True
            for p in fresh:
                if p.id in pool or zero_result_target:
                    pool[p.id] = p
            research.products = list(pool.values())
            research.indexed_offer_ids = [p.id for p in fresh if p.id in pool]
            research.pending_offer_ids = [] if zero_result_target else [p.id for p in fresh if p.id not in pool]
            research.timings.update(timings)
            async with self.index.sessions() as db:
                jobs = (
                    await db.scalars(
                        select(CatalogSyncJob).where(CatalogSyncJob.id.in_(research.background_job_ids))
                    )
                ).all()
            running = any(j.status in ("PENDING", "RUNNING") for j in jobs)
            research.job_status = ResearchJobStatus.RUNNING if running else ResearchJobStatus.COMPLETED
            if not running and any(j.status == 'FAILED' for j in jobs):
                research.job_status = ResearchJobStatus.PARTIAL
            await self.repository.save(research)
            return research

    async def accept_matches(self, identifier):
        snapshot = await self.repository.get(identifier)
        if snapshot and snapshot.unified:
            async with self.repository.mutation_lock(identifier):
                snapshot = await self.repository.get(identifier)
                await self.grid_pool(snapshot)
                snapshot.view.version += 1
                await self.repository.save(snapshot)
                return snapshot
        async with self.lock(identifier):
            research = await self.repository.get(identifier)
            fresh, _ = await self.query_engine.search(research.intent)
            pool = {p.id: p for p in research.products}
            for product in fresh:
                pool[product.id] = product
            research.products = list(pool.values())
            research.indexed_offer_ids = [p.id for p in fresh]
            research.pending_offer_ids = []
            await self.repository.save(research)
            return research

    async def change(self, identifier, **changes):
        snapshot = await self.repository.get(identifier)
        if snapshot and snapshot.unified:
            return await self.grid_change(identifier, **changes)
        before = await self.get(identifier)
        previous = set(before.view.selected_ids) if before else set()
        research = await super().change(identifier, **changes)
        selected = set(research.view.selected_ids) - previous
        if selected:
            await self.index.mark_usage(list(selected), "selected")
        return research

    async def on_index_update(self, _job):
        # Active clients pull bounded deltas. Do not hydrate every historical session
        # whenever one supplier page is indexed.
        self.last_update = time.monotonic()

    def independent_query(self, message, research):
        text = message.casefold()
        if re.search(
            r"^(?:только|оставь|убери|без|до |более|похож|выбер|верни|отмен|обнов|продолж|добав|покажи все)",
            text,
        ):
            return False
        categories = UniversalCategoryResolver().resolve(message)
        return bool(
            re.search(r"\d+\s*(?:шт|человек)", text)
            or re.search(r"мерч|конференц|подарки для", text)
            or categories
            and categories != research.intent.categories
        )
