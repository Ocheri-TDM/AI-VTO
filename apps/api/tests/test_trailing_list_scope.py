"""Shared list qualifiers must use the same offer's colour and numeric stock."""

import pytest
from httpx import ASGITransport, AsyncClient
from test_product_grid import service

from app.database.conversations import ConversationRepository
from app.database.models import Base
from app.domain.models import Product, ProductColor
from app.main import create_app

QUERY = "Покажи мне Зонты ручки бутылки черного цвета 100 шт"
NAMES = {"umbrella": "Зонт-трость", "pen": "Ручка шариковая", "bottle": "Бутылка для воды"}
EXPECTED = {f"vto:tail-{category}-black100" for category in NAMES}


@pytest.fixture
async def trailing_grid(repository, settings):
    grid = service(repository, settings)
    await grid.index.ingest(
        [
            Product(
                id=f"vto:tail-{category}-{suffix}",
                supplier="vto",
                name=name,
                source_url=f"https://vto.kz/product/{category}-{suffix}/",
                original_price=1000,
                original_currency="KZT",
                stock_quantity=stock,
                colors=[ProductColor(original_color=colour)],
            )
            for category, name in NAMES.items()
            for suffix, colour, stock in [
                ("black100", "чёрный", 100),
                ("black99", "чёрный", 99),
                ("red500", "красный", 500),
                ("blackunknown", "чёрный", None),
            ]
        ]
    )
    chat = await ConversationRepository(repository.sessions).create("Trailing list qualifiers")
    return grid, chat


@pytest.mark.parametrize(
    "query",
    [
        QUERY,
        "Покажи Зонты ручки бутылки чёрного цвета 100 шт",
        "Зонты, ручки и бутылки чёрного цвета, 100 шт.",
        "Чёрные зонты ручки бутылки 100 шт",
        "100 шт. Зонты ручки бутылки чёрного цвета",
    ],
)
async def test_unqualified_list_shares_trailing_colour_and_quantity(trailing_grid, query):
    grid, chat = trailing_grid
    research = await grid.start(chat, query)
    assert research.intent.categories == list(NAMES)
    assert research.intent.colors == ["BLACK"]
    assert research.intent.quantity == 100
    assert all(b.colors == ["BLACK"] and b.quantity == 100 for b in research.intent.branches)
    assert set(research.pool_offer_ids) == EXPECTED
    response = await grid.grid_response(research)
    assert {p["id"] for p in response["products"]} == EXPECTED
    assert response["color_state"]["mode"] == "uniform"
    assert response["effective_filters"]["fields"]["colors"]["mode"] == "common"
    assert response["effective_filters"]["fields"]["colors"]["values"] == ["BLACK"]
    assert response["effective_filters"]["fields"]["minimum_quantity"]["mode"] == "common"
    assert response["effective_filters"]["fields"]["minimum_quantity"]["number"] == 100
    saved = await grid.repository.get(research.id)
    assert saved.intent == research.intent


@pytest.mark.parametrize(
    "query,colours,quantities",
    [
        ("Зонты, ручки и чёрные бутылки 100 шт", [[], [], ["BLACK"]], [None, None, 100]),
        ("Зонты, ручки и чёрные бутылки от 100 шт", [[], [], ["BLACK"]], [None, None, 100]),
        (
            "Красные зонты, синие ручки и чёрные бутылки 100 шт",
            [["RED"], ["BLUE"], ["BLACK"]],
            [None, None, 100],
        ),
        ("Зонты 100 шт, ручки 200 шт и бутылки чёрного цвета 300 шт", [[], [], ["BLACK"]], [100, 200, 300]),
    ],
)
async def test_explicit_local_attributes_are_not_promoted(trailing_grid, query, colours, quantities):
    grid, _ = trailing_grid
    intent = await grid.grid_plan(query)
    assert [b.colors for b in intent.branches] == colours
    assert [b.quantity for b in intent.branches] == quantities


@pytest.mark.parametrize(
    "query,categories,colour,quantity",
    [
        ("Кружки блокноты шопперы синего цвета 200 шт", ["mug", "notebook", "shopperbag"], "BLUE", 200),
        (
            "Ручки блокноты шопперы тёмно-синего цвета 250 шт",
            ["pen", "notebook", "shopperbag"],
            "DARK_BLUE",
            250,
        ),
        ("Бутылки и зонты белого цвета 200 штук", ["bottle", "umbrella"], "WHITE", 200),
    ],
)
async def test_other_lists_and_colours_share_the_same_rule(
    trailing_grid, query, categories, colour, quantity
):
    grid, _ = trailing_grid
    intent = await grid.grid_plan(query)
    assert intent.categories == categories
    assert intent.colors == [colour] and intent.quantity == quantity
    assert all(b.colors == [colour] and b.quantity == quantity for b in intent.branches)


@pytest.mark.parametrize("suffix", ["желательно чёрные, 100 шт", "покажи сначала чёрные, 100 шт"])
async def test_soft_colours_do_not_become_hard_filters(trailing_grid, suffix):
    grid, _ = trailing_grid
    intent = await grid.grid_plan("Зонты ручки бутылки " + suffix)
    assert not intent.colors and all(not b.colors for b in intent.branches)
    assert intent.quantity == 100 and all(b.quantity == 100 for b in intent.branches)


@pytest.mark.parametrize("bound,exclusive", [("больше", True), ("от", False)])
async def test_shared_quantity_preserves_strict_bounds(trailing_grid, bound, exclusive):
    grid, _ = trailing_grid
    intent = await grid.grid_plan("Зонты ручки бутылки чёрного цвета " + bound + " 100 шт")
    assert intent.quantity == 100
    assert all(
        b.quantity == 100 and b.constraints.quantity_min_exclusive is exclusive for b in intent.branches
    )


async def test_category_specific_suffix_keeps_its_scope(trailing_grid):
    grid, _ = trailing_grid
    intent = await grid.grid_plan("Зонты ручки бутылки 500 мл чёрного цвета 100 шт")
    assert [b.colors for b in intent.branches] == [[], [], ["BLACK"]]
    assert [b.quantity for b in intent.branches] == [None, None, 100]


async def test_workspace_repeat_after_another_colour_uses_new_shared_intent(settings):
    app = create_app(settings, [])
    async with app.state.sessions() as db:
        async with db.bind.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/chats", json={"project_name": "Shared trailing context"})
        project = (await client.get("/api/projects")).json()[0]["id"]
        endpoint = f"/api/projects/{project}/requests"
        previous = await client.post(endpoint, json={"query": "Красные зонты, ручки и бутылки"})
        assert previous.status_code in (200, 202)
        result = await client.post(endpoint, json={"query": QUERY})
        assert result.status_code in (200, 202)
        research = result.json()["research"]
        assert research["intent"]["colors"] == ["BLACK"] and research["intent"]["quantity"] == 100
        assert all(b["colors"] == ["BLACK"] and b["quantity"] == 100 for b in research["intent"]["branches"])
        shown = await client.post(endpoint, json={"query": "Покажи все категории"})
        assert shown.status_code in (200, 202)
        assert shown.json()["research"]["effective_filters"]["fields"]["colors"]["values"] == ["BLACK"]
        assert shown.json()["research"]["effective_filters"]["fields"]["minimum_quantity"]["number"] == 100
    await app.state.engine.dispose()
