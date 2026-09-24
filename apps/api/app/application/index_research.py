"""User research consumes the index; enqueue is the only connection to crawling."""

import re
import time

from sqlalchemy import select

from app.application.index_query import IndexQueryService
from app.application.research import ResearchService
from app.database.models import CatalogSyncJob
from app.domain.research import ResearchJobStatus, ResearchSession
from app.domain.taxonomy import UniversalCategoryResolver


class IndexedResearchService(ResearchService):
    def __init__(self, repository, providers, settings, model, index):
        super().__init__(repository, providers, settings, model)
        self.index = index
        self.query_engine = IndexQueryService(index)
        self.last_update = 0

    async def start(self, chat_id, query, budget=None):
        started = time.perf_counter()
        intent = await self.planner.parse(query)
        parse_ms = (time.perf_counter() - started) * 1000
        research = ResearchSession(
            chat_id=chat_id, intent=intent, source_mode="index", job_status="COMPLETED"
        )
        if budget:
            research.budget = budget
        research.coverage = self.planner.plan(intent, research.budget)
        research.products, research.timings = await self.query_engine.search(intent)
        research.indexed_offer_ids = [p.id for p in research.products]
        await self.index.mark_usage(research.indexed_offer_ids, "searched")
        research.view.preferences = list(intent.soft_terms) + [k for k,v in intent.preferences.model_dump().items() if v]
        research.messages.append({"role": "user", "content": query})
        research.timings["intent_parse_ms"] = round(parse_ms, 2)
        if intent.budget:
            research.view.filters.min_price, research.view.filters.max_price = (
                intent.budget.min,
                intent.budget.max,
            )
        await self.repository.save(research)
        stale = [p.id for p in research.products if p.metadata.get("availability_stale")
                 or p.metadata.get("procurement_status") == "NEEDS_REFRESH"]
        if stale:
            await self.enqueue_availability(research, stale, quantity=intent.quantity is not None)
        if not research.products or research.timings.get("metadata_gap"):
            await self.enqueue_target(research)
        research.timings["response_ms"] = round((time.perf_counter() - started) * 1000, 2)
        await self.repository.save(research)
        return research

    async def enqueue_availability(self, research, offer_ids, quantity=False):
        """Queue bounded verification after the index response has been committed."""
        by_supplier = {}
        for product in research.products:
            if product.id in offer_ids:
                by_supplier.setdefault(product.supplier, []).append(product.id)
        for supplier, ids in by_supplier.items():
            due, _ = await self.index.adaptive_refresh_candidates(
                supplier, ids, self.settings.targeted_refresh_limit
            )
            if not due:
                continue
            job_id = await self.index.enqueue(
                supplier, "TARGETED_REFRESH",
                {"pending": [url for _, url, _, _ in due], "offer_ids": ids,
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
