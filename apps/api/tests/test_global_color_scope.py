"""Independent variant truth for compound attributes, branch scope and HUD."""

import pytest
from httpx import ASGITransport, AsyncClient
from test_product_grid import service

from app.database.conversations import ConversationRepository
from app.database.models import Base
from app.domain.catalog_identity import BrandRegistry, resolve_identity
from app.domain.models import Product, ProductColor
from app.domain.query_constraints import color_spans
from app.domain.research import ResearchFilters
from app.main import create_app

QUERY = "Темно синий цвет. 250 шт. ручка, ежедневник, шоппер, термокружка."
EXPLICIT = (
    "Тёмно-синие ручки от 250 шт, тёмно-синие ежедневники от 250 шт, "
    "тёмно-синие шопперы от 250 шт, тёмно-синие термокружки от 250 шт."
)
NAMES = {
    "pen": "Ручка шариковая",
    "notebook": "Ежедневник",
    "shopperbag": "Сумка-шоппер",
    "thermomug": "Термокружка",
}
# Color and stock deliberately belong to the same offer, not its siblings.
VARIANTS = [
    ("dark250", "тёмно-синий", 250),
    ("dark251", "тёмно-синий", 251),
    ("dark249", "тёмно-синий", 249),
    ("blue", "синий", 500),
    ("navy", "navy", 500),
    ("light", "голубой", 500),
    ("red", "красный", 500),
    ("unknown-color", "не опубликован", 500),
    ("unknown-stock", "тёмно-синий", None),
]
EXPECTED = {f"vto:scope-{c}-{v}" for c in NAMES for v in ("dark250", "dark251")}


def products():
    return [
        Product(
            id=f"vto:scope-{category}-{suffix}",
            supplier="vto",
            name=name,
            original_price=500,
            original_currency="KZT",
            stock_quantity=stock,
            colors=[ProductColor(original_color=color)],
            source_url=f"https://vto.kz/product/scope-{category}/",
            metadata={"parent_name": name},
        )
        for category, name in NAMES.items()
        for suffix, color, stock in VARIANTS
    ]


@pytest.fixture
async def grid(repository, settings):
    grid = service(repository, settings)
    await grid.index.ingest(products())
    chat = await ConversationRepository(repository.sessions).create("Color scope")
    return grid, chat


@pytest.mark.parametrize(
    "phrase",
    [
        "темно синий",
        "тёмно синий",
        "темно-синий",
        "тёмно-синий",
        "тёмно-синего цвета",
        "темно синего цвета",
        "ТЁМНО   СИНИЙ",
        "темно\u00a0синий",
        "тёмно‑синий",
        "тёмно–синий",
        "тёмно—синий",
    ],
)
async def test_complete_color_span_is_not_a_model(grid, phrase):
    g, _ = grid
    query = phrase + ". 250 шт. ручки, ежедневники, шопперы, термокружки."
    intent = await g.grid_plan(query)
    assert [b.category for b in intent.branches] == list(NAMES)
    assert intent.colors == ["DARK_BLUE"] and intent.quantity == 250
    assert not intent.models and not intent.brands
    for b in intent.branches:
        assert b.colors == ["DARK_BLUE"] and b.quantity == 250
        assert not b.models and not b.brands and b.origins["colors"].startswith("global:")
    spans = color_spans(phrase)
    assert len(spans) == 1 and spans[0].color == "DARK_BLUE"
    assert phrase[spans[0].start : spans[0].end] == spans[0].text


@pytest.mark.parametrize(
    "query",
    [
        QUERY,
        EXPLICIT,
        "Ручка, ежедневник, шоппер, термокружка. Цвет тёмно-синий. От 250 шт.",
        "250 шт. Цвет тёмно-синий. Ручки, ежедневники, шопперы, термокружки.",
        "Ручки, ежедневники, шопперы и термокружки. Всё тёмно-синее, минимум 250 штук.",
    ],
)
async def test_full_offer_truth_and_permutations(grid, query):
    g, chat = grid
    r = await g.start(chat, query)
    assert set(r.pool_offer_ids) == EXPECTED
    response = await g.grid_response(r)
    assert set(p["id"] for p in response["products"]) == EXPECTED
    assert response["color_state"]["mode"] == "uniform"
    assert response["color_state"]["source_values"] == ["DARK_BLUE"]
    assert response["view"]["filters"]["colors"] == []
    assert "Темно" not in response["effective_title"]
    assert (await g.repository.get(r.id)).intent == r.intent


async def test_local_colors_override_and_contradiction(grid):
    g, chat = grid
    r = await g.start(chat, "Красные ручки и синие термокружки")
    assert [b.colors for b in r.intent.branches] == [["RED"], ["BLUE"]]
    assert (await g.grid_response(r))["color_state"]["source_mode"] == "mixed"
    r = await g.start(chat, "Ручки, ежедневники и шопперы. Всё тёмно-синее, но ручки красные.")
    assert [b.category for b in r.intent.branches] == ["pen", "notebook", "shopperbag"]
    assert [b.colors for b in r.intent.branches] == [["RED"], ["DARK_BLUE"], ["DARK_BLUE"]]
    assert not any(b.models for b in r.intent.branches)
    with pytest.raises(ValueError, match="несовместимые"):
        await g.grid_plan("Ручки одновременно только красные и только синие")


async def test_zero_filter_reset_hide_remove_undo_and_paging(grid):
    g, chat = grid
    # More than one page: expected IDs are fixture constants, never parser output.
    extras = [products()[0].model_copy(update={"id": f"vto:scope-extra-{n:03}"}) for n in range(110)]
    await g.index.ingest(extras)
    r = await g.start(chat, QUERY)
    expected = EXPECTED | {f"vto:scope-extra-{n:03}" for n in range(110)}
    page = await g.grid_response(r)
    actual = {p["id"] for p in page["products"]}
    while page["next_cursor"]:
        page = await g.grid_response(r, page["next_cursor"])
        actual.update(p["id"] for p in page["products"])
    assert actual == expected and len(actual) == 118
    original_ids = [b.id for b in r.intent.branches]
    r = await g.grid_change(r.id, filters=ResearchFilters(colors=["RED"]))
    value = await g.grid_response(r)
    assert value["filtered_total"] == 0 and value["color_state"]["mode"] == "conflict"
    assert value["color_state"]["source_values"] == ["DARK_BLUE"]
    r = await g.grid_change(r.id, restore="reset")
    assert set(r.pool_offer_ids) == expected and r.intent.colors == ["DARK_BLUE"]
    r = await g.grid_change(r.id, branch_actions=[{"action": "hide", "branch_id": original_ids[0]}])
    assert (await g.grid_response(r))["color_state"]["source_values"] == ["DARK_BLUE"]
    r = await g.grid_change(r.id, branch_actions=[{"action": "remove", "branch_id": original_ids[1]}])
    r = await g.grid_change(r.id, restore="undo")
    assert [b.id for b in r.intent.branches] == original_ids


def test_confirmed_identity_keeps_color_words():
    registry = BrandRegistry(["Blue Lake"])
    assert resolve_identity("бренд Blue Lake", registry).brands == ["Blue Lake"]
    assert resolve_identity("ручки модель Blue Lake", registry).models == ["Blue Lake"]
    assert not resolve_identity("ручки модель Blue Lake", registry).attribute_query.strip().endswith("Lake")


async def test_http_context_color_replacement_and_collection(settings):
    app = create_app(settings, [])
    async with app.state.engine.begin() as db:
        await db.run_sync(Base.metadata.create_all)
    await app.state.index.ingest(products())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.post("/api/chats", json={"project_name": "Color HTTP"})
        project = (await c.get("/api/projects")).json()[0]["id"]
        endpoint = f"/api/projects/{project}/requests"
        await c.post(endpoint, json={"query": "красные ручки"})
        result = (await c.post(endpoint, json={"query": QUERY})).json()
        assert result["route"]["action"] == "NEW_SEARCH"
        r = result["research"]
        assert {p["id"] for p in r["products"]} == EXPECTED
        await c.post(f"/api/projects/{project}/collection/items", json={"offer_id": "vto:scope-pen-dark250"})
        before = (await c.get(f"/api/projects/{project}/collection")).json()
        r = (await c.post(endpoint, json={"query": "теперь красные"})).json()["research"]
        assert r["intent"]["colors"] == ["RED"]
        assert all(b["colors"] == ["RED"] for b in r["intent"]["branches"])
        assert r["view"]["filters"]["colors"] == []
        assert len(r["products"]) == 4
        r = (await c.post(endpoint, json={"query": "цвет не важен"})).json()["research"]
        assert all(not b["colors"] for b in r["intent"]["branches"])
        assert r["color_state"]["mode"] == "none"
        assert (await c.get(f"/api/projects/{project}/collection")).json() == before
        r = (await c.post(endpoint, json={"query": "бутылки"})).json()["research"]
        assert not r["intent"]["colors"]
    await app.state.engine.dispose()
