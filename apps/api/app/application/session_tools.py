"""Deterministic tools operating only on the chat's active, fresh result pool."""

import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from uuid import uuid4

from app.application.search import SearchService
from app.application.tools import (
    DetailsInput,
    FilterInput,
    RankInput,
    RestoreInput,
    SearchInput,
    SelectInput,
    SortInput,
    ToolInput,
    ToolRegistry,
    ToolResult,
)
from app.database.conversations import ConversationRepository
from app.domain.errors import SearchExpiredError
from app.domain.models import SearchIntent, SearchSnapshot, utcnow
from app.domain.search_state import RankedProduct, SearchCoverage, SearchState, ViewFilters, current_view


class SessionTools:
    def __init__(
        self,
        chat_id: str,
        snapshot: SearchSnapshot | None,
        search: SearchService,
        conversations: ConversationRepository,
        emit: Callable[[str, dict], Awaitable[None]],
    ):
        self.chat_id, self.snapshot = chat_id, snapshot
        self.search, self.repository, self.conversations = search, search.repository, conversations
        self.emit = emit
        self.registry = ToolRegistry()
        for name, schema, handler in (
            ("search_products", SearchInput, self.search_products),
            ("incremental_search", SearchInput, self.incremental_search),
            ("filter_products", FilterInput, self.filter_products),
            ("sort_products", SortInput, self.sort_products),
            ("rank_products", RankInput, self.rank_products),
            ("select_products", SelectInput, self.select_products),
            ("restore_products", RestoreInput, self.restore_products),
            ("refresh_search", ToolInput, self.refresh_search),
            ("get_search_summary", ToolInput, self.summary),
            ("get_product_details", DetailsInput, self.details),
        ):
            self.registry.register(name, schema, handler)

    def require(self) -> SearchSnapshot:
        if self.snapshot is None:
            raise ValueError("SEARCH_SESSION_REQUIRED")
        if self.snapshot.expires_at <= utcnow():
            raise SearchExpiredError(self.snapshot.id)
        return self.snapshot

    def state(self) -> SearchState:
        return SearchState.model_validate(self.require().state)

    def view(self) -> list[RankedProduct]:
        s = self.require()
        return current_view(s.products, s.intent, self.state())

    async def result(self, name: str, **kwargs) -> ToolResult:
        s = self.snapshot
        return ToolResult(
            name=name,
            search_session_id=s.id if s else None,
            visible_count=len(self.view()) if s else 0,
            selected_count=len(self.state().selected_ids) if s else 0,
            state_version=self.state().version if s else 0,
            **kwargs,
        )

    async def crawl(self, intent: SearchIntent, *, providers=None, refresh=False):
        service = (
            self.search
            if providers is None
            else SearchService(self.repository, providers, self.search.parser, self.search.settings)
        )
        snapshot = await service.start(intent.raw_query, intent=intent, retain_pool=True, refresh=refresh)
        cache_hit = snapshot.cache_hit
        last = {}
        while True:
            snapshot = await self.repository.get(snapshot.id)
            for status in snapshot.suppliers:
                if last.get(status.supplier) != status.status:
                    event = (
                        "supplier.completed"
                        if status.status in ("completed", "failed")
                        else "supplier.started"
                    )
                    await self.emit(event, {"supplier": status.supplier, "status": status.status})
                    last[status.supplier] = status.status
            if snapshot.status not in ("queued", "running"):
                break
            await asyncio.sleep(0.35)
        await service.wait(snapshot.id)
        snapshot.cache_hit = cache_hit
        return snapshot

    async def search_products(self, data: SearchInput):
        if self.snapshot:
            return await self.incremental_search(data)
        cached = await self.crawl(data.intent)
        # Shared cached observations are immutable to chats: clone before binding mutable state.
        self.snapshot = cached.model_copy(deep=True, update={"id": str(uuid4())})
        await self.repository.create(
            self.snapshot, hashlib.sha256(("chat:" + self.snapshot.id).encode()).hexdigest()
        )
        await self.repository.save(self.snapshot)
        await self.conversations.bind(self.chat_id, self.snapshot)
        return await self.result("search_products", searched_categories=data.intent.categories)

    async def incremental_search(self, data: SearchInput):
        if self.snapshot is None:
            return await self.search_products(data)
        s, state = self.require(), self.state()
        missing = SearchCoverage.model_validate(s.coverage).missing(
            data.intent, [p.supplier for p in self.search.providers]
        )
        additions = []
        # Group suppliers with identical missing categories; their searches still run in parallel.
        groups = {}
        for supplier, categories in missing.items():
            groups.setdefault(tuple(categories), []).append(supplier)
        for categories, suppliers in groups.items():
            intent = data.intent.model_copy(update={"categories": list(categories)})
            additions.append(
                await self.crawl(
                    intent, providers=[p for p in self.search.providers if p.supplier in suppliers]
                )
            )
        s.intent = data.intent
        state.intent_version += 1
        state.filters.categories = data.intent.categories or None
        state.filters.colors = data.intent.colors
        state.filters.quantity = data.intent.quantity
        state.filters.budget = data.intent.budget
        state.filters.excluded_categories = data.intent.excluded_categories
        state.filters.excluded_colors = data.intent.excluded_colors
        await self.conversations.change(s, state)
        for extra in additions:
            s.products = self.search.deduplication.group(s.products + extra.products)
            s.coverage = {"entries": s.coverage.get("entries", []) + extra.coverage.get("entries", [])}
            s.traces.extend(extra.traces)
            s.suppliers = extra.suppliers
            if extra.status in ("partial", "failed"):
                s.status = "partial"
        # Original session expiry is never extended by an incremental crawl.
        await self.repository.save(s)
        await self.conversations.bind(self.chat_id, s)
        return await self.result(
            "incremental_search", searched_categories=sorted({c for cs in missing.values() for c in cs})
        )

    async def filter_products(self, data: FilterInput):
        s, state = self.require(), self.state()
        if data.mode == "replace":
            state.filters = data.filters
        elif data.mode == "remove":
            state.filters.excluded_categories = list(
                set(state.filters.excluded_categories + (data.filters.categories or []))
            )
            state.filters.excluded_colors = list(
                set(state.filters.excluded_colors + (data.filters.colors or []))
            )
            self.validate_ids(data.filters.hidden_ids)
            state.filters.hidden_ids = list(set(state.filters.hidden_ids + data.filters.hidden_ids))
        else:
            merged = state.filters.model_dump()
            merged.update(
                {
                    key: value
                    for key, value in data.filters.model_dump(exclude_unset=True, exclude_none=True).items()
                    if value != []
                }
            )
            state.filters = ViewFilters.model_validate(merged)
        self.validate_ids(state.filters.hidden_ids)
        await self.conversations.change(s, state)
        return await self.result("filter_products")

    async def sort_products(self, data: SortInput):
        state = self.state()
        state.sorting = data.sorting
        await self.conversations.change(self.require(), state)
        return await self.result("sort_products")

    async def rank_products(self, data: RankInput):
        state = self.state()
        state.preferences = data.preferences
        state.sorting = "relevance"
        await self.conversations.change(self.require(), state)
        return await self.result("rank_products")

    def validate_ids(self, ids: list[str]) -> None:
        if not set(ids).issubset({p.id for p in self.require().products}):
            raise ValueError("UNKNOWN_PRODUCT_ID")

    async def select_products(self, data: SelectInput):
        state = self.state()
        ids = data.product_ids
        if ids is not None:
            self.validate_ids(ids)
        else:
            candidates = [
                x.product for x in self.view() if not data.categories or x.product.category in data.categories
            ]
            if data.per_category:
                counts, ids = {}, []
                for p in candidates:
                    if counts.get(p.category, 0) < data.count:
                        ids.append(p.id)
                        counts[p.category] = counts.get(p.category, 0) + 1
            else:
                ids = [p.id for p in candidates[: data.count]]
        if data.mode in ("add", "replace"):
            visible = {p.product.id for p in self.view()}
            if not set(ids).issubset(visible):
                raise ValueError("PRODUCT_NOT_VISIBLE")
        state.selected_ids = (
            []
            if data.mode == "clear"
            else [x for x in state.selected_ids if x not in ids]
            if data.mode == "remove"
            else list(dict.fromkeys(state.selected_ids + ids))
            if data.mode == "add"
            else list(dict.fromkeys(ids))
        )
        await self.conversations.change(self.require(), state)
        return await self.result("select_products")

    async def restore_products(self, data: RestoreInput):
        s, state = self.require(), self.state()
        if data.mode == "undo":
            await self.conversations.change(s, state, undo=True)
        else:
            if data.mode == "budget":
                state.filters.budget = None
            elif data.mode == "removed":
                visible = {x.product.id for x in self.view()}
                state.filters = ViewFilters(hidden_ids=list(visible))
            else:
                state.filters = ViewFilters(quantity=s.intent.quantity)
                state.sorting = "relevance"
            await self.conversations.change(s, state)
        await self.conversations.bind(self.chat_id, s)
        return await self.result("restore_products")

    async def refresh_search(self, _data: ToolInput):
        s = self.require()
        intent = s.intent.model_copy(deep=True)
        fresh = await self.crawl(intent, refresh=True)
        # Refresh creates a new pool and history boundary; undo never resurrects old stock.
        fresh.id = str(uuid4())
        await self.repository.create(fresh, hashlib.sha256(("chat:" + fresh.id).encode()).hexdigest())
        await self.repository.save(fresh)
        self.snapshot = fresh
        await self.conversations.bind(self.chat_id, fresh)
        return await self.result("refresh_search", searched_categories=intent.categories)

    async def summary(self, _data: ToolInput):
        return await self.result("get_search_summary")

    async def details(self, data: DetailsInput):
        self.validate_ids([data.product_id])
        p = next(p for p in self.require().products if p.id == data.product_id)
        return await self.result("get_product_details", note=f"{p.name}: {p.price_kzt} ₸")
