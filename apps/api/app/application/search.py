import asyncio
import hashlib
from datetime import timedelta
from time import perf_counter

from app.ai.base import ModelProvider
from app.application.cache import cache_key
from app.application.planner import SearchPlanner
from app.config import Settings
from app.database.repository import SearchRepository
from app.domain.errors import CurrencyError, SupplierError
from app.domain.intent import CategoryResolver
from app.domain.models import (
    ActionType,
    BrowserAction,
    Product,
    SearchIntent,
    SearchSnapshot,
    SupplierStatus,
    utcnow,
)
from app.domain.search_state import CoverageEntry, SearchCoverage, SearchState, ViewFilters
from app.domain.services import (
    AvailabilityService,
    ColorNormalizer,
    CurrencyService,
    DeduplicationService,
)
from app.observability import log_event
from app.providers.base import SupplierProvider


class SearchBusyError(Exception):
    pass


class SearchService:
    def __init__(
        self,
        repository: SearchRepository,
        providers: list[SupplierProvider],
        parser: ModelProvider,
        settings: Settings,
    ):
        self.repository = repository
        self.providers = providers
        self.parser = parser
        self.settings = settings
        self.currency = CurrencyService(settings.rub_kzt_rate)
        self.colors = ColorNormalizer()
        self.availability = AvailabilityService()
        self.categories = CategoryResolver()
        self.deduplication = DeduplicationService()
        self.planner = SearchPlanner()
        self._start_lock = asyncio.Lock()
        self._jobs: dict[str, asyncio.Task] = {}

    async def start(
        self,
        query: str,
        *,
        refresh: bool = False,
        intent: SearchIntent | None = None,
        retain_pool: bool = False,
    ) -> SearchSnapshot:
        parsed = intent or await self.parser.parse_intent(query)
        key = cache_key(
            parsed,
            [p.supplier for p in self.providers],
            str(self.currency.rate) if self.currency.rate else None,
            (self.settings.max_pages_per_query, self.settings.max_products_per_query),
        )
        if retain_pool:
            key = hashlib.sha256((key + ":pool-v2").encode()).hexdigest()
        # Single-worker MVP: prevent simultaneous equivalent requests from creating duplicate crawls.
        async with self._start_lock:
            if not refresh:
                cached = await self.repository.find_cached(key)
                if cached:
                    log_event("search_cache_hit", session_id=cached.id)
                    return cached
            if len(self._jobs) >= self.settings.max_active_searches:
                raise SearchBusyError("Поиск занят. Повторите запрос немного позже.")
            snapshot = SearchSnapshot(
                intent=parsed,
                expires_at=utcnow() + timedelta(seconds=self.settings.cache_ttl_seconds),
                suppliers=[SupplierStatus(supplier=p.supplier) for p in self.providers],
                state=SearchState(
                    filters=ViewFilters(
                        categories=parsed.categories or None,
                        colors=parsed.colors,
                        budget=parsed.budget,
                        quantity=parsed.quantity,
                        excluded_categories=parsed.excluded_categories,
                        excluded_colors=parsed.excluded_colors,
                    ),
                    preferences=parsed.preferences,
                    sorting=parsed.sorting,
                ).model_dump(mode="json")
                if retain_pool
                else {},
            )
            await self.repository.create(snapshot, key)
            job = asyncio.create_task(self._run(snapshot), name=f"search:{snapshot.id}")
            self._jobs[snapshot.id] = job
            job.add_done_callback(lambda _task: self._jobs.pop(snapshot.id, None))
            return snapshot.model_copy(deep=True)

    def normalize(self, product: Product, intent: SearchIntent) -> Product | None:
        item = product.model_copy(deep=True)
        if not self.categories.matches(item.name, intent.categories):
            return None
        inferred = self.categories.classify(item.name)
        if inferred:
            item.category = inferred[0]
        item.colors = [self.colors.normalize(color.original_color) for color in item.colors]
        item.available = self.availability.accepts(item, intent.quantity or 1)
        if not item.available or not self.colors.matches(item.colors, intent.colors):
            return None
        if item.original_price is None or item.original_currency is None:
            return None
        item.price_kzt = self.currency.to_kzt(item.original_price, item.original_currency)
        if intent.budget is not None:
            if intent.budget.max is not None and item.price_kzt > intent.budget.max:
                return None
            if intent.budget.min is not None and item.price_kzt < intent.budget.min:
                return None
        return item

    async def _run(self, snapshot: SearchSnapshot) -> None:
        write_lock = asyncio.Lock()

        async def persist() -> None:
            async with write_lock:
                await self.repository.save(snapshot.model_copy(deep=True))

        try:
            snapshot.status = "running"
            await persist()
            await asyncio.gather(
                *[
                    self._supplier(provider, snapshot, status, persist)
                    for provider, status in zip(self.providers, snapshot.suppliers, strict=True)
                ]
            )
            snapshot.products = self.deduplication.group(snapshot.products)
            # Deterministic ranking boundary; later replace with relevance model.
            snapshot.products.sort(
                key=lambda p: (p.price_kzt if p.price_kzt is not None else 10**15, p.name, p.id)
            )
            failed = sum(s.status == "failed" for s in snapshot.suppliers)
            snapshot.status = (
                "completed"
                if failed == 0
                else ("partial" if failed < len(snapshot.suppliers) or snapshot.products else "failed")
            )
            await persist()
            log_event(
                "search_completed",
                session_id=snapshot.id,
                status=snapshot.status,
                products_accepted=len(snapshot.products),
            )
        except asyncio.CancelledError:
            await self._mark_interrupted(snapshot)
            raise
        except Exception as exc:
            log_event("search_error", session_id=snapshot.id, error_type=type(exc).__name__)
            await self._mark_interrupted(snapshot)

    async def _mark_interrupted(self, snapshot: SearchSnapshot) -> None:
        for status in snapshot.suppliers:
            if status.status in ("pending", "running"):
                status.status = "failed"
                status.error_code = "SEARCH_INTERRUPTED"
                status.message = "Поиск прерван. Обновите подборку."
        snapshot.status = "partial" if snapshot.products else "failed"
        try:
            await self.repository.save(snapshot)
        except Exception as exc:
            log_event("search_persist_error", session_id=snapshot.id, error_type=type(exc).__name__)

    async def _supplier(
        self, provider: SupplierProvider, snapshot: SearchSnapshot, status: SupplierStatus, persist
    ) -> None:
        started = perf_counter()
        status.status = "running"
        await persist()
        log_event("supplier_search_started", session_id=snapshot.id, supplier=provider.supplier)
        seen: set[str] = set()
        try:
            async with asyncio.timeout(self.settings.provider_timeout_seconds):
                for task in self.planner.plan(snapshot.intent):
                    result = None
                    coverage = CoverageEntry(
                        supplier=provider.supplier,
                        category=task.filters.categories[0] if task.filters.categories else "*",
                        colors=[],
                        quantity=1 if snapshot.state else (snapshot.intent.quantity or 1),
                        status="failed",
                    )
                    entries = SearchCoverage.model_validate(snapshot.coverage)
                    entries.entries.append(coverage)
                    snapshot.coverage = entries.model_dump(mode="json")
                    for attempt in range(self.settings.provider_retries + 1):
                        try:
                            result = await provider.search(task.query, task.filters)
                            break
                        except SupplierError as exc:
                            if not exc.retryable or attempt == self.settings.provider_retries:
                                raise
                            log_event(
                                "supplier_retry",
                                supplier=provider.supplier,
                                code=exc.code,
                                attempt=attempt + 1,
                            )
                            await asyncio.sleep(0.5 * (2**attempt))
                    assert result is not None
                    coverage.status = "limited" if result.warnings else "completed"
                    coverage.pages_scanned = result.pages_scanned
                    entries = SearchCoverage.model_validate(snapshot.coverage)
                    entries.entries = [
                        coverage
                        if e.supplier == coverage.supplier
                        and e.category == coverage.category
                        and e.searched_at == coverage.searched_at
                        else e
                        for e in entries.entries
                    ]
                    snapshot.coverage = entries.model_dump(mode="json")
                    status.pages_scanned += result.pages_scanned
                    status.warnings.extend(result.warnings)
                    snapshot.traces.extend(result.traces)
                    for product in result.products:
                        if product.id in seen:
                            continue
                        seen.add(product.id)
                        status.products_discovered += 1
                        try:
                            normalization_intent = (
                                snapshot.intent.model_copy(
                                    update={"quantity": 1, "colors": [], "budget": None}
                                )
                                if snapshot.state
                                else snapshot.intent
                            )
                            accepted = self.normalize(product, normalization_intent)
                        except CurrencyError as exc:
                            accepted = None
                            message = str(exc)
                            if message not in status.warnings:
                                status.warnings.append(message)
                            status.error_code = "CURRENCY_CONVERSION_ERROR"
                            log_event("currency_conversion_error", supplier=provider.supplier)
                        if accepted:
                            snapshot.products.append(accepted)
                            status.products_accepted += 1
                        else:
                            status.products_rejected += 1
                    await persist()
            status.status = "completed"
            if status.error_code == "CURRENCY_CONVERSION_ERROR":
                status.message = "Часть цен не удалось перевести в тенге. Проверьте настройку курса."
        except TimeoutError:
            status.status = "failed"
            status.error_code = "SUPPLIER_TIMEOUT"
            status.message = "Поставщик не ответил за отведённое время."
        except SupplierError as exc:
            status.status = "failed"
            status.error_code, status.message = exc.code, exc.message
        except Exception as exc:
            status.status = "failed"
            status.error_code = "SUPPLIER_PARSING_ERROR"
            status.message = "Не удалось прочитать данные поставщика."
            log_event("supplier_error", supplier=provider.supplier, error_type=type(exc).__name__)
        finally:
            status.warnings = list(dict.fromkeys(status.warnings))
            if status.status == "failed":
                snapshot.traces.append(
                    BrowserAction(
                        action=ActionType.ERROR,
                        supplier=provider.supplier,
                        details={"code": status.error_code},
                    )
                )
            status.duration_ms = round((perf_counter() - started) * 1000)
            log_event(
                "supplier_search_completed",
                session_id=snapshot.id,
                **status.model_dump(exclude={"message", "warnings"}),
            )
            await persist()

    async def wait(self, session_id: str) -> None:
        if task := self._jobs.get(session_id):
            await task

    async def close(self) -> None:
        tasks = list(self._jobs.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
