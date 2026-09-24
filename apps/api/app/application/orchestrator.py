import asyncio
import re
from collections import Counter, defaultdict

from pydantic import BaseModel, Field, ValidationError, field_serializer

from app.ai.commands import CommandParser
from app.ai.context import ContextBuilder
from app.ai.decision import AgentAction as A
from app.ai.decision import AgentDecision
from app.ai.local import ModelUnavailable
from app.api.schemas import ProductCard, SearchResponse
from app.application.search import SearchBusyError
from app.application.session_tools import SessionTools
from app.application.tools import SelectInput
from app.domain.errors import SearchExpiredError
from app.domain.intent import CategoryResolver, ColorIntentResolver
from app.domain.models import utcnow
from app.domain.search_state import SearchState
from app.observability import log_event


class ChatResponse(BaseModel):
    assistant_message: str
    search_session_id: str | None = None
    action: str
    result_summary: dict = Field(default_factory=dict)
    session: SearchResponse | None = None
    selected_product_ids: list[str] = Field(default_factory=list)
    state_version: int = 0
    debug: dict | None = None
    active_state: SearchState = Field(default_factory=SearchState)
    selected_products: list[ProductCard] = Field(default_factory=list)
    pool_count: int = 0
    facets: dict[str, list[str]] = Field(default_factory=dict)
    can_undo: bool = False

    @field_serializer("active_state")
    def serialize_state(self, value: SearchState):
        # Null clears a filter; keep it explicit even in exclude_none SSE responses.
        return value.model_dump(mode="json")


async def no_event(_name, _data):
    pass


class Orchestrator:
    async def execute_research(self, research_id: str, message: str):
        """Research shares persisted conversations, execution audit and model transport.

        One validated refinement action per message; browser work is queued separately.
        """
        from app.application.research_refinement import ResearchRefinementEngine
        research = await self.research.get(research_id)
        if research is None:
            raise KeyError(research_id)
        async with self.locks[research.chat_id]:
            execution = await self.conversations.begin(research.chat_id, message)
            try:
                async with asyncio.timeout(self.settings.agent_timeout_seconds):
                    value, decision, answer = await ResearchRefinementEngine(self.research).apply(research_id, message)
                await self.conversations.finish(execution, answer, decision.model_dump(mode='json'),
                    [{'tool': decision.action.value, 'research_id': research_id}], None)
                return value, decision, answer
            except Exception:
                await self.conversations.finish(execution, 'Не удалось изменить исследование. Подборка сохранена.',
                                                {}, [], None, status='failed')
                raise

    def __init__(self, search, conversations, settings, model=None):
        self.search, self.conversations, self.settings, self.model = search, conversations, settings, model
        self.context = ContextBuilder()
        self.fallback = CommandParser()
        self.locks = defaultdict(asyncio.Lock)

    async def snapshot(self, chat):
        try:
            return (
                await self.search.repository.get(chat["search_session_id"])
                if chat["search_session_id"]
                else None
            )
        except SearchExpiredError:
            return None

    def validate_command(self, decision, message, snapshot, last_intent):
        """Reject semantic mismatches for the small, explicit fallback grammar.

        Natural-language model decisions never authorize unrelated side effects.
        Complex requests continue through the model; this checks obvious commands.
        """
        if not snapshot:
            if decision.action == A.SELECT_PRODUCTS:
                raise ValueError("SELECTION_REQUIRES_SESSION")
            return
        text = message.casefold().replace("ё", "е")
        if not re.search(
            r"^(?:убери|оставь|только|дешевле|до |верни|назад|отмени|сброс|добавь|выбери|покажи сначала|сделай их более)",
            text,
        ):
            return
        expected = self.fallback.decide(message, snapshot, last_intent)
        if expected.action == A.SHOW_RESULTS:
            return
        if decision.action != expected.action:
            raise ValueError("COMMAND_ACTION_MISMATCH")
        if expected.action == A.EXPAND_RESULTS:
            if decision.intent is None or set(decision.intent.categories) != set(expected.intent.categories):
                raise ValueError("EXPANSION_CATEGORY_MISMATCH")
            if (
                decision.intent.quantity != expected.intent.quantity
                or decision.intent.budget != expected.intent.budget
            ):
                raise ValueError("EXPANSION_CONSTRAINT_MISMATCH")
        for field in ("filters", "sorting", "selection", "restore_mode"):
            value = getattr(expected, field)
            if value is None or field == "restore_mode" and expected.action != A.RESTORE_RESULTS:
                continue
            actual = getattr(decision, field)
            if hasattr(value, "model_dump"):
                wanted = value.model_dump(exclude_unset=True, exclude_none=True)
                received = actual.model_dump(exclude_none=True) if actual else {}
                if any(received.get(k) != v for k, v in wanted.items()):
                    raise ValueError("COMMAND_ARGUMENT_MISMATCH")
            elif actual != value:
                raise ValueError("COMMAND_ARGUMENT_MISMATCH")

    def response(self, tools, action, text, debug=None):
        if tools.snapshot and tools.snapshot.expires_at <= utcnow():
            tools.snapshot = None
        snapshot = tools.snapshot
        state = SearchState.model_validate(snapshot.state) if snapshot else SearchState()
        view = tools.view() if snapshot else []
        session = (
            SearchResponse.from_snapshot(snapshot.model_copy(update={"products": [x.product for x in view]}))
            if snapshot
            else None
        )
        return ChatResponse(
            assistant_message=text,
            search_session_id=snapshot.id if snapshot else None,
            action=action,
            session=session,
            selected_product_ids=state.selected_ids,
            state_version=state.version,
            active_state=state,
            selected_products=[
                ProductCard.model_validate(p.model_dump())
                for p in snapshot.products
                if p.id in state.selected_ids
            ]
            if snapshot
            else [],
            pool_count=len(snapshot.products) if snapshot else 0,
            facets={
                "categories": sorted({p.category for p in snapshot.products if p.category}),
                "colors": sorted({c.normalized_color.value for p in snapshot.products for c in p.colors}),
            }
            if snapshot
            else {},
            result_summary={
                "visible_count": len(view),
                "selected_count": len(state.selected_ids),
                "categories": dict(Counter(x.product.category for x in view)),
                "scores": {x.product.id: {"score": x.score, "match_reasons": x.match_reasons} for x in view},
            },
            debug=debug if self.settings.debug_agent else None,
        )

    def plan(self, d: AgentDecision, tools, message):
        if d.search_session_id and (not tools.snapshot or d.search_session_id != tools.snapshot.id):
            raise ValueError("FOREIGN_SEARCH_SESSION")
        if d.action == A.REFRESH_SEARCH and not any(x in message.casefold() for x in ("обнов", "refresh")):
            raise ValueError("EXPLICIT_REFRESH_REQUIRED")
        if d.intent:
            if tools.snapshot and d.action == A.EXPAND_RESULTS:
                state = tools.state()
                previous = tools.snapshot.intent.model_dump(mode="json")
                previous.update(
                    {
                        "categories": state.filters.categories or tools.snapshot.intent.categories,
                        "colors": state.filters.colors or [],
                        "quantity": state.filters.quantity or tools.snapshot.intent.quantity,
                        "budget": state.filters.budget.model_dump() if state.filters.budget else None,
                        "excluded_categories": state.filters.excluded_categories,
                        "excluded_colors": state.filters.excluded_colors,
                    }
                )
                updates = d.intent.model_dump(mode="json", exclude_unset=True, exclude_none=True)
                updates = {key: value for key, value in updates.items() if value != []}
                updates["categories"] = list(dict.fromkeys(previous["categories"] + d.intent.categories))
                previous.update(updates)
                d.intent = type(d.intent).model_validate(previous)
            resolver = CategoryResolver()
            d.intent.categories = (
                resolver.resolve(d.intent.raw_query, d.intent.categories)
                if not d.intent.categories
                else d.intent.categories
            )
            if any(x not in resolver.patterns for x in d.intent.categories):
                raise ValueError("UNSUPPORTED_CATEGORY")
            d.intent.colors = (
                ColorIntentResolver().resolve(d.intent.colors, d.intent.excluded_colors).acceptable
            )

        def dump(x):
            return x.model_dump(mode="json", exclude_unset=True)

        if d.action in (A.SEARCH, A.EXPAND_RESULTS, A.CHANGE_INTENT):
            if d.intent is None:
                raise ValueError("INTENT_REQUIRED")
            return [
                ("incremental_search" if tools.snapshot else "search_products", {"intent": dump(d.intent)})
            ]
        if d.action in (A.FILTER_RESULTS, A.REMOVE_RESULTS):
            if d.filters is None:
                raise ValueError("FILTERS_REQUIRED")
            return [
                (
                    "filter_products",
                    {
                        "filters": dump(d.filters),
                        "mode": "remove" if d.action == A.REMOVE_RESULTS else "merge",
                    },
                )
            ]
        if d.action == A.SORT_RESULTS:
            return (
                [("rank_products", {"preferences": dump(d.preferences)})]
                if d.preferences and any(d.preferences.model_dump().values())
                else [("sort_products", {"sorting": d.sorting or "relevance"})]
            )
        if d.action == A.SELECT_PRODUCTS:
            calls = (
                [("rank_products", {"preferences": dump(d.preferences)})]
                if d.preferences and any(d.preferences.model_dump().values())
                else []
            )
            return calls + [("select_products", dump(d.selection or SelectInput()))]
        if d.action == A.REFRESH_SEARCH:
            return [("refresh_search", {})]
        if d.action == A.SHOW_PRODUCT_DETAILS:
            if not d.product_id:
                raise ValueError("PRODUCT_ID_REQUIRED")
            return [("get_product_details", {"product_id": d.product_id})]
        if d.action in (A.UNDO, A.RESTORE_RESULTS, A.RESET_FILTERS):
            return [
                (
                    "restore_products",
                    {
                        "mode": "undo"
                        if d.action == A.UNDO
                        else "reset"
                        if d.action == A.RESET_FILTERS
                        else d.restore_mode
                    },
                )
            ]
        return [] if d.action in (A.ASK_CLARIFICATION, A.NO_ACTION) else [("get_search_summary", {})]

    async def execute(self, chat_id, message, *, emit=no_event, selection: SelectInput | None = None):
        lock = self.locks[chat_id]
        if lock.locked():
            raise ValueError("CHAT_BUSY")
        async with lock:
            chat = await self.conversations.get(chat_id)
            if chat is None:
                raise LookupError("CHAT_NOT_FOUND")
            snapshot = await self.snapshot(chat)
            tools = SessionTools(chat_id, snapshot, self.search, self.conversations, emit)
            starting_version = tools.state().version if snapshot else 0
            execution = await self.conversations.begin(chat_id, message)
            calls, decision, fallback_reason = [], None, None
            await emit("agent.started", {"status": "Анализирую запрос"})
            try:
                async with asyncio.timeout(self.settings.agent_timeout_seconds):
                    context = self.context.build(chat, snapshot)
                    if selection:
                        decision = AgentDecision(action=A.SELECT_PRODUCTS, selection=selection)
                    elif self.model:
                        try:
                            decision = await self.model.decide(context, message)
                            self.validate_command(decision, message, snapshot, chat.get("last_intent"))
                            self.plan(decision, tools, message)
                        except (ModelUnavailable, ValidationError, ValueError):
                            fallback_reason = "MODEL_FALLBACK"
                    if decision is None or fallback_reason:
                        decision = self.fallback.decide(message, snapshot, chat.get("last_intent"))
                    # Expired filters must not silently launch a new broad search.
                    if (
                        snapshot is None
                        and chat.get("last_intent")
                        and not any(x in message.casefold() for x in ("обнов", "найди", "нужн", "подбери"))
                    ):
                        decision = AgentDecision(
                            action=A.ASK_CLARIFICATION,
                            user_response_hint="Срок хранения подборки истёк. Напишите «обнови поиск», чтобы проверить товары заново.",
                        )
                    await emit("intent.parsed", {"action": decision.action.value})
                    initial = decision
                    seen = set()
                    for step in range(self.settings.max_tool_calls_per_message):
                        plan = self.plan(decision, tools, message)
                        if len(calls) + len(plan) > self.settings.max_tool_calls_per_message:
                            raise ValueError("TOOL_LIMIT")
                        for name, arguments in plan:
                            # Never repeat an action in this message, even if the model loops.
                            if name in seen:
                                # A repeated model suggestion does not repeat the side effect.
                                continue
                            seen.add(name)
                            await emit("tool.started", {"tool": name})
                            result = await tools.registry.call(name, arguments)
                            calls.append(result.model_dump(mode="json"))
                            await emit("results.updated", result.model_dump(mode="json"))
                        compound = re.search(
                            r"затем|потом|после этого|и выбери|и отсортируй", message.casefold()
                        )
                        if (
                            not self.model
                            or fallback_reason
                            or selection
                            or not plan
                            or step >= 1
                            or not compound
                        ):
                            break
                        try:
                            updated = self.context.build(
                                await self.conversations.get(chat_id), tools.snapshot
                            )
                            follow = await self.model.next_step(updated, calls)
                        except ModelUnavailable:
                            break
                        if follow.done or follow.decision is None:
                            break
                        if follow.decision.action == A.SELECT_PRODUCTS and not re.search(
                            r"выбер|выбра|оставь \d+ вариант", message.casefold()
                        ):
                            break
                        # A follow-up may only finish local view work, never launch another crawl.
                        if follow.decision.action in (
                            A.SEARCH,
                            A.EXPAND_RESULTS,
                            A.CHANGE_INTENT,
                            A.REFRESH_SEARCH,
                            A.UNDO,
                        ):
                            break
                        decision = follow.decision
                    decision = initial
                    if snapshot and tools.snapshot.id == snapshot.id and decision.action != A.UNDO:
                        await self.conversations.collapse_history(snapshot.id, starting_version)
                    text = self.render(tools, decision)
                    if decision.action == A.SHOW_PRODUCT_DETAILS and calls:
                        text = calls[-1]["note"]
                    response = self.response(
                        tools,
                        decision.action.value,
                        text,
                        {
                            "decision": decision.model_dump(mode="json"),
                            "tools_called": calls,
                            "fallback": fallback_reason,
                            "search_coverage": tools.snapshot.coverage if tools.snapshot else {},
                            "cache_hit": tools.snapshot.cache_hit if tools.snapshot else False,
                            "state_version": tools.state().version if tools.snapshot else 0,
                        },
                    )
                    await self.conversations.finish(
                        execution, text, decision.model_dump(mode="json"), calls, response.search_session_id
                    )
                    response.can_undo = await self.conversations.can_undo(response.search_session_id)
                    await emit("agent.completed", response.model_dump(mode="json", exclude_none=True))
                    return response
            except asyncio.CancelledError:
                await self.conversations.finish(
                    execution,
                    "Выполнение прервано. Сохранённые изменения доступны в диалоге.",
                    decision.model_dump(mode="json") if decision else {},
                    calls,
                    tools.snapshot.id if tools.snapshot else None,
                    status="interrupted",
                )
                raise
            except (TimeoutError, ValueError, ValidationError, SearchBusyError, SearchExpiredError) as exc:
                code = "AGENT_TIMEOUT" if isinstance(exc, TimeoutError) else "INVALID_AGENT_ACTION"
                log_event("agent_error", code=code)
                text = "Не удалось выполнить действие. " + (
                    "Превышено время ожидания; попробуйте ещё раз."
                    if isinstance(exc, TimeoutError)
                    else "Проверьте запрос или выбранные товары."
                )
                response = self.response(tools, "NO_ACTION", text, {"error_code": code})
                await self.conversations.finish(
                    execution,
                    text,
                    decision.model_dump(mode="json") if decision else {},
                    calls,
                    response.search_session_id,
                    status="failed",
                )
                response.can_undo = await self.conversations.can_undo(response.search_session_id)
                await emit("agent.error", response.model_dump(mode="json", exclude_none=True))
                return response

    def render(self, tools, decision):
        if decision.action == A.ASK_CLARIFICATION:
            # Model hints cannot make product claims; clarification uses a fixed server template.
            return (
                decision.user_response_hint
                if decision.user_response_hint.startswith("Срок хранения")
                else "Уточните, какие товары или образец вы имеете в виду."
            )
        if not tools.snapshot:
            return "Опишите, какие корпоративные подарки нужно подобрать."
        count, selected = len(tools.view()), len(tools.state().selected_ids)
        prefix = {
            A.FILTER_RESULTS: "Применил фильтры.",
            A.REMOVE_RESULTS: "Скрыл указанные позиции.",
            A.SORT_RESULTS: "Изменил порядок вариантов.",
            A.UNDO: "Восстановил предыдущее состояние.",
            A.RESTORE_RESULTS: "Восстановил варианты.",
            A.RESET_FILTERS: "Сбросил фильтры.",
            A.SELECT_PRODUCTS: f"Выбрано товаров: {selected}.",
            A.EXPAND_RESULTS: "Дополнил подборку.",
            A.REFRESH_SEARCH: "Повторно проверил товары.",
        }.get(decision.action, "Подборка готова.")
        text = f"{prefix} В текущей выборке {count} вариантов. Цены указаны в тенге."
        if count:
            quantity = tools.state().filters.quantity or tools.snapshot.intent.quantity
            if quantity:
                text += f" Каждый вариант проходит по тиражу {quantity} шт."
        if any(s.status == "failed" or s.warnings for s in tools.snapshot.suppliers):
            text += " Проверена часть результатов: у поставщиков есть ошибки или ограничения обхода."
        return text
