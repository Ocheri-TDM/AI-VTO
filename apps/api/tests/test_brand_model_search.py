"""Independent catalog truth, not expectations copied from the resolver output."""

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient
from test_product_grid import service

from app.database.conversations import ConversationRepository
from app.database.models import Base
from app.domain.catalog_identity import BrandRegistry, brand_key, model_evidence, resolve_identity
from app.domain.models import Product
from app.domain.research import ResearchFilters
from app.main import create_app


def product(id, name, brand=None, parent=None, model=None):
    return Product(
        id="vto:" + id,
        supplier="vto",
        name=name,
        brand=brand,
        source_url="https://vto.kz/product/" + id + "/",
        original_price=100,
        original_currency="KZT",
        metadata={"parent_name": parent or name, **({"model": model} if model else {})},
    )


def fixtures():
    return [
        product("tippo-" + str(n), "Ручка шариковая Tippo — " + color, None, "Ручка шариковая Tippo")
        for n, color in enumerate(["синяя", "чёрная", "красная", "белая", "зелёная", "оранжевая"])
    ] + [
        product("vortex", "Ручка шариковая VORTEX", "B1"),
        product("tippo-plus", "Ручка шариковая Tippomatic", "Beta"),
        product("alpha-pen", "Ручка шариковая Orbit", "Alpha"),
        product("alpha-bag", "Рюкзак Path", "Alpha"),
        product("beta-bottle", "Бутылка для воды River", "Beta"),
        product("beta-pen", "Ручка шариковая Orbit", "Beta"),
        product("unknown", "Ручка шариковая Plain", "Без бренда"),
        product("space", "Ручка шариковая «Deep Space»", "Alpha Studio"),
        product("space-extra", "Ручка шариковая Deep Spacex", "Beta"),
        product("for-you", "Ручка шариковая «For You»", "Beta"),
        product("cyrillic", "Ручка шариковая Галактика", "Beta"),
        product("structured", "Ручка шариковая Design 42", "Beta", model="Eclipse Pro"),
        product("nova-brand", "Ручка шариковая Vector", "Nova"),
        product("nova-model", "Ручка шариковая Nova", "Beta"),
        product("compat", "Футляр для ручек Alpha", "Beta"),
        product("nfkc", "Ручка шариковая Unicode", "Ｍｏｌｔｉ"),
        product("spaces", "Ручка шариковая Spacing", "  ALPHA   STUDIO  "),
        product("lamp", "Лампа Sterilizer B1", "Gamma"),
        product("lecce", "Ручка шариковая Sample", "Lecce Pen"),
    ]


@pytest.mark.parametrize(
    "query,expected",
    [
        ("Alpha", {"alpha-pen", "alpha-bag"}),
        ("ручки Alpha", {"alpha-pen"}),
        ("ручки B1", {"vortex"}),
        ("ручки Lecce Pen", {"lecce"}),
        ("Ручка шариковая Tippo", {"tippo-" + str(n) for n in range(6)}),
        ("ручки TippoX", set()),
        ("ручки модель Deep Space", {"space"}),
        ("модель For You", {"for-you"}),
        ("ручки ГАЛАКТИКА", {"cyrillic"}),
        ("ручки модель Eclipse Pro", {"structured"}),
        (
            "ручки бренд Alpha или Beta",
            {
                "alpha-pen",
                "beta-pen",
                "tippo-plus",
                "for-you",
                "cyrillic",
                "structured",
                "nova-model",
                "space-extra",
            },
        ),
        ("ручки бренда Alpha и бутылки бренда Beta", {"alpha-pen", "beta-bottle"}),
        ("ручки и рюкзаки бренда Alpha", {"alpha-pen", "alpha-bag"}),
        ("ручки бренда Missing и бутылки бренда Beta", {"beta-bottle"}),
        ("бренд Missing", set()),
        ("UnknownBrand729", set()),
        ("Nova", set()),
        ("бренд Nova", {"nova-brand"}),
        ("модель Nova", {"nova-model"}),
        ("ручки Molti", {"nfkc"}),
        ("бренд alpha studio", {"space", "spaces"}),
    ],
)
async def test_catalog_identity_requirements(repository, settings, query, expected):
    grid = service(repository, settings)
    await grid.index.ingest(fixtures())
    chat = await ConversationRepository(repository.sessions).create("Catalog identities")
    r = await grid.start(chat, query)
    value = await grid.grid_response(r)
    assert {p["id"] for p in value["products"]} == {"vto:" + id for id in expected}, value["intent"]
    if query == "Nova":
        assert value["identity_notices"]


def test_normalization_is_conservative_and_model_boundaries_are_literal():
    registry = BrandRegistry(["Alpha", "Alpha Studio", "A-B", "AB", "ＭＯＬＴＩ", "Molti"])
    assert brand_key("  ALPHA\u00a0 Studio ") == brand_key("Alpha Studio")
    assert brand_key("A-B") != brand_key("AB")
    assert len(registry.spans("alpha studio")) == 1
    assert not registry.spans("alphabet")
    assert model_evidence(("Tippo",), "Ручка Tippomatic", "")["accepted"] is False
    assert model_evidence(("Deep Space",), "Ручка Deep Spacex", "")["accepted"] is False
    assert resolve_identity("бренд A-B", registry).brands == ["A-B"]
    unicode_registry = BrandRegistry(["Groß", "Вымпел", "Happy gifts"])
    assert resolve_identity("ручки Groß", unicode_registry).cleaned == "ручки"
    assert resolve_identity("ручки GROSS", unicode_registry).brands == ["Groß"]
    assert resolve_identity("ВЫМПЕЛ", unicode_registry).brands == ["Вымпел"]
    assert resolve_identity("Happy   gifts", unicode_registry).brands == ["Happy gifts"]
    assert resolve_identity("белые футболки из хлопка", registry).models == []
    assert resolve_identity("кожаные рюкзаки", registry).models == []


async def test_brand_and_hud_use_all_ids_and_immediate_catalog_changes(repository, settings):
    grid = service(repository, settings)
    await grid.index.ingest(
        [product(str(n), f"Ручка шариковая Item {n}", "Alpha" if n < 125 else "Beta") for n in range(140)]
    )
    chat = await ConversationRepository(repository.sessions).create("Catalog identity paging")
    source = await grid.start(chat, "ручки Alpha")
    baseline = await grid.start(chat, "ручки")
    baseline = await grid.change(baseline.id, filters=ResearchFilters(brands=["ALPHA"]), version=0)

    async def all_ids(r):
        value = await grid.grid_response(r)
        ids = [p["id"] for p in value["products"]]
        while value["next_cursor"]:
            value = await grid.grid_response(r, value["next_cursor"])
            ids.extend(p["id"] for p in value["products"])
        return set(ids)

    assert await all_ids(source) == await all_ids(baseline) == {"vto:" + str(n) for n in range(125)}
    source = await grid.change(source.id, restore="reset", version=source.view.version)
    assert len(await all_ids(source)) == 125
    await grid.index.ingest([product("new", "Бутылка New", "New Brand")])
    assert await all_ids(await grid.start(chat, "бренд New Brand")) == {"vto:new"}
    without = await grid.start(chat, "ручки без Alpha")
    assert await all_ids(without) == {"vto:" + str(n) for n in range(125, 140)}


@pytest.fixture
async def identity_app(settings):
    settings.auth_required = True
    app = create_app(settings, [])
    async with app.state.engine.begin() as db:
        await db.run_sync(Base.metadata.create_all)
    await app.state.index.ingest(fixtures())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post(
            "/api/auth/register", json={"name": "identity-owner", "password": "identity-password-123"}
        )
        await client.post("/api/chats", json={"project_name": "Identity"})
        project = (await client.get("/api/projects")).json()[0]["id"]
        yield app, client, project
    await app.state.engine.dispose()


async def test_followups_new_brand_replacement_scope_undo_and_collection(identity_app):
    app, c, p = identity_app

    async def submit(q):
        response = await c.post(f"/api/projects/{p}/requests", json={"query": q})
        assert response.status_code == 202, response.text
        return response.json()

    def ids(result):
        return {p["id"] for p in result["research"]["products"]}

    assert ids(await submit("ручки"))
    assert (await submit("Alpha"))["route"]["action"] == "NEW_SEARCH"
    assert ids(await submit("ручки и рюкзаки бренда Alpha")) == {"vto:alpha-pen", "vto:alpha-bag"}
    await c.post(f"/api/projects/{p}/collection/items", json={"offer_id": "vto:alpha-bag"})
    changed = await submit("Теперь другой бренд — Beta")
    assert "vto:beta-pen" in ids(changed) and "vto:alpha-pen" not in ids(changed)
    assert changed["research"]["intent"]["brands"] == []
    assert "vto:alpha-pen" in ids(await submit("назад"))
    assert "vto:unknown" in ids(await submit("Бренд не важен"))
    constrained = await submit("Рюкзаки только Alpha")
    assert constrained["research"]["view"]["filters"]["branch_brands"] == {"backpack": ["alpha"]}
    assert "vto:tippo-0" in ids(constrained)  # no leaked brand into the pen branch
    fresh = await submit("Теперь найди бутылки")
    assert ids(fresh) == {"vto:beta-bottle"} and fresh["research"]["intent"]["brands"] == []
    await submit("ручки")
    model = await submit("Модель Tippo")
    assert ids(model) == {"vto:tippo-" + str(n) for n in range(6)}
    assert (await c.get("/api/researches/" + model["research"]["id"])).json()["view"]["filters"][
        "models"
    ] == ["Tippo"]
    collection = (await c.get(f"/api/projects/{p}/collection")).json()
    assert collection["selected_ids"] == ["vto:alpha-bag"]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as other:
        await other.post(
            "/api/auth/register", json={"name": "identity-foreign", "password": "identity-password-456"}
        )
        responses = await asyncio.gather(
            other.get("/api/researches/" + model["research"]["id"]),
            other.get(f"/api/projects/{p}/collection"),
        )
        assert [r.status_code for r in responses] == [404, 404]
