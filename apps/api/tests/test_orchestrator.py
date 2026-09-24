from datetime import timedelta
from decimal import Decimal

import pytest
from conftest import FakeProvider, make_product
from pydantic import ValidationError

from app.ai.basic import BasicIntentParser
from app.ai.commands import CommandParser
from app.ai.context import ContextBuilder
from app.ai.decision import AgentDecision
from app.ai.local import ModelUnavailable
from app.application.orchestrator import Orchestrator
from app.application.search import SearchService
from app.application.tools import SelectInput, ToolRegistry
from app.database.conversations import ConversationRepository
from app.domain.intent import CategoryResolver, ColorIntentResolver
from app.domain.models import Budget, SearchIntent, utcnow
from app.domain.search_state import CoverageEntry, SearchCoverage, SearchState


class CategoryProvider(FakeProvider):
    def __init__(self, supplier):
        super().__init__(supplier)
        self.categories = []

    async def search(self, query, filters):
        category = filters.categories[0]
        self.categories.append(category)
        names = {
            "backpack": "Рюкзак",
            "pen": "Ручка",
            "notebook": "Ежедневник",
            "bottle": "Бутылка",
            "thermomug": "Термокружка",
        }
        self.products = [
            make_product(
                id=f"{self.supplier}:{category}:{i}",
                supplier=self.supplier,
                name=f"{names[category]} Variant {i}, navy",
                original_currency="KZT",
                original_price=Decimal(4000 + i * 3000),
                stock_quantity=None if i == 4 else 299 if i == 3 else 500,
            )
            for i in range(5)
        ]
        return await super().search(query, filters)


@pytest.fixture
async def agent(repository, settings):
    providers = [CategoryProvider("gifts"), CategoryProvider("ucontay")]
    search = SearchService(repository, providers, BasicIntentParser(), settings)
    conversations = ConversationRepository(repository.sessions)
    instance = Orchestrator(search, conversations, settings)
    chat = await conversations.create()
    yield instance, chat, providers
    await search.close()


async def test_eight_required_dialogue_scenarios(agent):
    a, chat, providers = agent
    initial = await a.execute(chat, "Нужны темно-синие рюкзаки и ручки. Тираж 300.")
    assert initial.action == "SEARCH"
    assert initial.session.intent.quantity == 300
    assert set(initial.session.intent.categories) == {"backpack", "pen"}
    assert "DARK_BLUE" in initial.session.intent.colors
    assert len(initial.session.products) == 12
    assert all(p.categories == ["backpack", "pen"] for p in providers)
    session_id = initial.search_session_id
    filtered = await a.execute(chat, "Убери все дороже 10000")
    assert filtered.action == "FILTER_RESULTS"
    assert all(p.price_kzt <= 10000 for p in filtered.session.products)
    only = await a.execute(chat, "Оставь только рюкзаки")
    assert only.action == "FILTER_RESULTS"
    assert {p.category for p in only.session.products} == {"backpack"}
    expanded = await a.execute(chat, "Добавь ежедневники")
    assert expanded.search_session_id == session_id
    assert {p.category for p in expanded.session.products} == {"backpack", "notebook"}
    assert all(p.categories == ["backpack", "pen", "notebook"] for p in providers)
    darker = await a.execute(chat, "Сделай их более темно-синими")
    assert darker.action == "SORT_RESULTS"
    cheap = await a.execute(chat, "Покажи сначала самые дешевые")
    assert cheap.action == "SORT_RESULTS"
    assert [p.price_kzt for p in cheap.session.products] == sorted(
        p.price_kzt for p in cheap.session.products
    )
    undo = await a.execute(chat, "Верни как было")
    assert undo.action == "UNDO"
    snapshot = await a.search.repository.get(session_id)
    assert SearchState.model_validate(snapshot.state).sorting == "darker"
    selected = await a.execute(chat, "Выбери 5 лучших рюкзаков")
    assert selected.action == "SELECT_PRODUCTS"
    assert len(selected.selected_product_ids) == 5
    assert all("backpack" in p for p in selected.selected_product_ids)
    assert all(p.calls == 3 for p in providers)


async def test_pool_undo_restore_and_isolated_chat_cache(agent):
    a, chat, providers = agent
    first = await a.execute(chat, "Рюкзаки navy, тираж 300")
    await a.execute(chat, "Убери все дороже 5000")
    s = await a.search.repository.get(first.search_session_id)
    assert len(s.products) > 2
    restored = await a.execute(chat, "Верни дорогие")
    assert len(restored.session.products) == 6
    other = await a.conversations.create()
    other_result = await a.execute(other, "Рюкзаки navy, тираж 300")
    assert other_result.search_session_id != first.search_session_id
    assert all(p.calls == 1 for p in providers)
    for _ in range(12):
        await a.execute(chat, "Покажи сначала самые дешевые")
    for _ in range(10):
        assert (await a.execute(chat, "назад")).action == "UNDO"


async def test_hallucinated_ids_rejected_and_no_browser(agent):
    a, chat, providers = agent
    await a.execute(chat, "Рюкзаки navy, тираж 300")
    bad = await a.execute(chat, "выбор", selection=SelectInput(product_ids=["invented"]))
    assert bad.action == "NO_ACTION" and bad.selected_product_ids == []
    assert all(p.calls == 1 for p in providers)
    with pytest.raises(ValidationError):
        AgentDecision.model_validate({"action": "SEARCH", "products": [{"price": 1}]})
    with pytest.raises(ValueError, match="UNKNOWN_TOOL"):
        await ToolRegistry().call("shell", {"command": "echo secret"})


async def test_model_failure_fallback_and_compact_context(agent):
    a, chat, providers = agent

    class BrokenModel:
        async def decide(self, *_):
            raise ModelUnavailable()

    a.model = BrokenModel()
    result = await a.execute(chat, "Рюкзаки navy, тираж 300")
    assert result.action == "SEARCH"
    result = await a.execute(chat, "Только до 10 тысяч")
    assert result.action == "FILTER_RESULTS"
    s = await a.search.repository.get(result.search_session_id)
    context = ContextBuilder().build(await a.conversations.get(chat), s)
    assert len(context["session"]["products"]) <= 24
    assert all(
        "metadata" not in p and "images" not in p and "supplier" not in p
        for p in context["session"]["products"]
    )
    assert all(p.calls == 1 for p in providers)


def test_resolvers_budget_and_coverage():
    resolver = CategoryResolver()
    assert resolver.resolve("термос и записная книжка") == ["thermomug", "notebook"]
    assert "powerbank" in resolver.resolve("Мерч для IT конференции")
    assert not resolver.matches("Коробка складная под бутылку, темно-синяя", ["bottle"])
    assert "LIGHT_BLUE" in ColorIntentResolver().from_text("синий").acceptable
    assert "LIGHT_BLUE" not in ColorIntentResolver().from_text("темно-синий").acceptable
    assert SearchIntent(raw_query="x", budget=10000).budget == Budget(max=10000)
    with pytest.raises(ValidationError):
        Budget(min=100, max=10)
    coverage = SearchCoverage(
        entries=[
            CoverageEntry(supplier="gifts", category="backpack", quantity=1, colors=[], status="limited")
        ]
    )
    intent = SearchIntent(raw_query="x", quantity=300, categories=["backpack", "notebook"])
    assert coverage.missing(intent, ["gifts"]) == {"gifts": ["notebook"]}
    assert coverage.missing(intent, ["gifts"], utcnow() + timedelta(hours=1)) == {
        "gifts": ["backpack", "notebook"]
    }
    assert CommandParser().decide("Найди такие же").action == "ASK_CLARIFICATION"


async def test_incremental_repeat_does_not_recrawl(agent):
    a, chat, providers = agent
    await a.execute(chat, "Рюкзаки navy, тираж 300")
    await a.execute(chat, "Добавь бутылки")
    await a.execute(chat, "Добавь бутылки")
    assert all(p.categories == ["backpack", "bottle"] for p in providers)


async def test_bounded_model_loop_and_message_level_undo(agent):
    from app.ai.decision import NextStep
    from app.domain.models import SoftPreferences

    a, chat, providers = agent
    first = await a.execute(chat, "Рюкзаки navy, тираж 300")

    class Model:
        calls = 0

        async def decide(self, *_):
            return AgentDecision(
                action="SELECT_PRODUCTS",
                preferences=SoftPreferences(premium=True),
                selection=SelectInput(count=2),
            )

        async def next_step(self, *_):
            self.calls += 1
            return NextStep(
                done=False, decision=AgentDecision(action="SELECT_PRODUCTS", selection=SelectInput(count=3))
            )

    model = Model()
    a.model = model
    selected = await a.execute(chat, "Выбери 2 премиальных, затем покажи подборку")
    assert len(selected.selected_product_ids) == 2
    assert model.calls == 1
    a.model = None
    await a.execute(chat, "назад")
    snapshot = await a.search.repository.get(first.search_session_id)
    assert not SearchState.model_validate(snapshot.state).preferences.premium
    assert not SearchState.model_validate(snapshot.state).selected_ids
    assert all(p.calls == 1 for p in providers)


async def test_tool_count_limit_and_foreign_session_fallback(agent):
    from app.domain.models import SoftPreferences

    a, chat, providers = agent
    await a.execute(chat, "Рюкзаки navy, тираж 300")
    a.settings.max_tool_calls_per_message = 1

    class Model:
        async def decide(self, *_):
            return AgentDecision(
                action="SELECT_PRODUCTS",
                preferences=SoftPreferences(premium=True),
                selection=SelectInput(count=2),
            )

    a.model = Model()
    result = await a.execute(chat, "Выбери премиальные")
    assert result.action == "NO_ACTION" and not result.selected_product_ids
    assert all(p.calls == 1 for p in providers)
