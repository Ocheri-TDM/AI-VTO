"""Effective controls have independent variant expectations and explicit edits."""

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from test_global_color_scope import EXPECTED, QUERY, products
from test_product_grid import service

from app.database.conversations import ConversationRepository
from app.database.models import Base
from app.domain.effective_filters import apply_filter_edits, effective_filter_state, effective_intent
from app.domain.models import Budget, Product, ProductColor
from app.domain.research import EffectiveFilterEdit, ProductBranch, ResearchIntent, ResearchSession
from app.main import create_app


@pytest.fixture
async def grid(repository, settings):
    g = service(repository, settings)
    await g.index.ingest(products())
    chat = await ConversationRepository(repository.sessions).create("Effective controls")
    return g, chat


def field(response, name):
    return response["effective_filters"]["fields"][name]


async def edit(g, r, field, action="set", **value):
    return await g.grid_change(
        r.id, version=r.view.version, effective_changes=[{"field": field, "action": action, **value}]
    )


async def test_control_values_do_not_copy_source_constraints(grid):
    g, chat = grid
    for query in [
        QUERY,
        "Тёмно-синие ручки от 250 шт, тёмно-синие ежедневники от 250 шт, тёмно-синие шопперы от 250 шт, тёмно-синие термокружки от 250 шт",
    ]:
        r = await g.start(chat, query)
        value = await g.grid_response(r)
        assert set(r.pool_offer_ids) == EXPECTED
        assert field(value, "colors")["values"] == ["DARK_BLUE"]
        assert field(value, "minimum_quantity")["number"] == 250
        assert all(
            field(value, f)["mode"] == "none"
            for f in ["brands", "materials", "sizes", "min_price", "max_price"]
        )
        assert not r.view.filters.colors and r.view.filters.minimum_quantity is None
        assert not r.view.effective_overrides


async def test_manual_set_clear_inherit_reset_and_undo_use_real_pool(grid):
    g, chat = grid
    r = await g.start(chat, QUERY)
    source = r.intent.model_dump(mode="json")
    r = await edit(g, r, "colors", values=["RED"])
    assert set(r.pool_offer_ids) == {
        f"vto:scope-{c}-red" for c in ["pen", "notebook", "shopperbag", "thermomug"]
    }
    assert r.intent.model_dump(mode="json") == source
    assert r.view.filters.colors == []
    assert field(await g.grid_response(r), "colors")["values"] == ["RED"]
    r = await edit(g, r, "minimum_quantity", number=501)
    assert not r.pool_offer_ids
    assert field(await g.grid_response(r), "minimum_quantity")["number"] == 501
    r = await edit(g, r, "minimum_quantity", "clear")
    assert len(r.pool_offer_ids) == 4
    assert field(await g.grid_response(r), "minimum_quantity")["mode"] == "none"
    r = await g.grid_change(r.id, restore="reset")
    assert set(r.pool_offer_ids) == EXPECTED and r.intent.model_dump(mode="json") == source
    r = await edit(g, r, "colors", "clear")
    assert field(await g.grid_response(r), "colors")["mode"] == "none"
    assert set(r.pool_offer_ids) == {
        f"vto:scope-{c}-{v}"
        for c in ["pen", "notebook", "shopperbag", "thermomug"]
        for v in ["dark250", "dark251", "blue", "navy", "light", "red", "unknown-color"]
    }
    r = await edit(g, r, "colors", "inherit")
    assert set(r.pool_offer_ids) == EXPECTED and not r.view.effective_overrides
    r = await g.grid_change(r.id, restore="undo")
    assert field(await g.grid_response(r), "colors")["mode"] == "none"
    assert (await g.repository.get(r.id)).intent.model_dump(mode="json") == source


async def test_hidden_zero_and_removed_branches_participate_correctly(grid):
    g, chat = grid
    await g.index.ingest(
        [
            Product(
                id="vto:bottle-blue",
                supplier="vto",
                name="Бутылка",
                stock_quantity=100,
                colors=[ProductColor(original_color="синий")],
                source_url="https://vto.kz/product/bottle/",
            )
        ]
    )
    r = await g.start(chat, "Красные ручки от 500 шт и синие бутылки от 100 шт.")
    assert set(r.pool_offer_ids) == {"vto:scope-pen-red", "vto:bottle-blue"}
    assert field(await g.grid_response(r), "colors")["mode"] == "mixed"
    assert field(await g.grid_response(r), "minimum_quantity")["number"] is None
    pen, bottle = [b.id for b in r.intent.branches]
    r = await edit(g, r, "minimum_quantity", number=501, branch_id=pen)
    assert r.pool_offer_ids == ["vto:bottle-blue"]
    assert field(await g.grid_response(r), "minimum_quantity")["mode"] == "mixed"
    r = await g.grid_change(r.id, branch_actions=[{"action": "hide", "branch_id": pen}])
    assert field(await g.grid_response(r), "colors")["mode"] == "mixed"
    r = await g.grid_change(r.id, branch_actions=[{"action": "remove", "branch_id": pen}])
    assert field(await g.grid_response(r), "colors")["values"] == ["BLUE"]
    assert field(await g.grid_response(r), "minimum_quantity")["number"] == 100
    r = await g.grid_change(r.id, restore="undo")
    assert [b.id for b in r.intent.branches] == [pen, bottle]
    assert field(await g.grid_response(r), "minimum_quantity")["mode"] == "mixed"
    r = await g.start(chat, "ручки и синие бутылки от 100 шт")
    assert field(await g.grid_response(r), "colors")["mode"] == "mixed"


def test_global_storage_and_other_common_fields_use_all_branches():
    intent = ResearchIntent(
        raw_query="global fixture",
        colors=["DARK_BLUE"],
        quantity=250,
        brands=["Alpha"],
        material="METAL",
        budget=Budget(min=100, max=800),
        branches=[
            ProductBranch(category="pen", label="Ручки"),
            ProductBranch(category="notebook", label="Ежедневники"),
        ],
    )
    for b in intent.branches:
        b.constraints.sizes = ["A5"]
    r = ResearchSession(chat_id="test", intent=intent)
    state = effective_filter_state(r)["fields"]
    assert state["colors"]["values"] == ["DARK_BLUE"] and state["minimum_quantity"]["number"] == 250
    assert state["brands"]["values"] == ["alpha"] and state["materials"]["values"] == ["METAL"]
    assert state["sizes"]["values"] == ["A5"]
    assert state["min_price"]["number"] == 100 and state["max_price"]["number"] == 800
    assert not intent.branches[0].colors  # Projection did not rewrite the source.
    intent.branches[1].colors = ["RED"]
    assert effective_filter_state(r)["fields"]["colors"]["mode"] == "mixed"


def test_clear_removes_stale_global_shorthands_but_retains_other_price_bound():
    r = ResearchSession(
        chat_id="test",
        intent=ResearchIntent(
            raw_query="fixture",
            material="METAL",
            budget=Budget(min=100, max=800),
            branches=[ProductBranch(category="pen", label="Ручки")],
        ),
    )
    apply_filter_edits(
        r, [{"field": "materials", "action": "clear"}, {"field": "min_price", "action": "clear"}]
    )
    intent = effective_intent(r)
    assert intent.material is None and intent.budget is None
    assert intent.branches[0].materials == [] and intent.branches[0].min_price is None
    assert intent.branches[0].max_price == 800
    # A later accepted chat revision cannot inherit the obsolete global METAL/100.
    r.intent = intent
    r.view.effective_overrides = []
    assert effective_intent(r).branches[0].materials == []
    assert effective_intent(r).branches[0].min_price is None


async def test_local_brand_change_and_exact_material_semantics(grid):
    g, chat = grid
    values = []
    for category, name in [("pen", "Ручка шариковая"), ("bottle", "Бутылка")]:
        for brand in ["Alpha", "Beta"]:
            values.append(
                Product(
                    id=f"vto:effective-{category}-{brand}",
                    name=name,
                    supplier="vto",
                    brand=brand,
                    material="сталь" if brand == "Alpha" else "сталь и дерево",
                    stock_quantity=400,
                    source_url=f"https://vto.kz/product/effective-{category}-{brand}/",
                )
            )
    await g.index.ingest(values)
    r = await g.start(chat, "бренд Alpha. ручки и бутылки")
    pen = next(b.id for b in r.intent.branches if b.category == "pen")
    r = await edit(g, r, "brands", values=["beta"], branch_id=pen)
    assert set(r.pool_offer_ids) == {"vto:effective-pen-Beta", "vto:effective-bottle-Alpha"}
    assert field(await g.grid_response(r), "brands")["mode"] == "mixed"
    r = await g.grid_change(r.id, restore="reset")
    assert set(r.pool_offer_ids) == {"vto:effective-pen-Alpha", "vto:effective-bottle-Alpha"}
    r = await g.start(chat, "металлические ручки")
    r = await edit(g, r, "materials", values=["сталь"])
    assert r.pool_offer_ids == ["vto:effective-pen-Alpha"]


@pytest.mark.parametrize(
    "value",
    [
        {"field": "minimum_quantity", "action": "set", "number": "Разный по категориям"},
        {"field": "minimum_quantity", "action": "set", "number": 250.5},
        {"field": "colors", "action": "set", "values": ["WIDENED_BLUE"]},
        {"field": "colors", "action": "clear", "values": ["RED"]},
        {"field": "minimum_quantity", "action": "set"},
    ],
)
def test_explicit_operations_reject_ambiguous_or_untyped_values(value):
    with pytest.raises(ValidationError):
        EffectiveFilterEdit.model_validate(value)


async def test_http_persistence_revisions_history_and_collection(settings):
    app = create_app(settings, [])
    async with app.state.engine.begin() as db:
        await db.run_sync(Base.metadata.create_all)
    await app.state.index.ingest(products())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.post("/api/chats", json={"project_name": "Effective HTTP"})
        project = (await c.get("/api/projects")).json()[0]["id"]
        submitted = (await c.post(f"/api/projects/{project}/requests", json={"query": QUERY})).json()
        r = submitted["research"]
        source = r["intent"]
        ids = [b["id"] for b in r["query_categories"]]
        await c.post(f"/api/projects/{project}/collection/items", json={"offer_id": "vto:scope-pen-dark250"})
        collection = (await c.get(f"/api/projects/{project}/collection")).json()
        path = f"/api/researches/{r['id']}/view"
        r = (
            await c.patch(
                path,
                json={
                    "version": r["view"]["version"],
                    "effective_changes": [
                        {"field": "colors", "action": "set", "values": ["RED"]},
                        {"field": "minimum_quantity", "action": "set", "number": 500},
                    ],
                },
            )
        ).json()
        assert field(r, "colors")["values"] == ["RED"] and field(r, "minimum_quantity")["number"] == 500
        assert len(r["products"]) == 4 and [b["id"] for b in r["query_categories"]] == ids
        saved = await app.state.research.repository.get(r["id"])
        assert saved.intent.model_dump(mode="json") == source
        reread = (await c.get(f"/api/researches/{r['id']}")).json()
        assert reread["effective_filters"] == r["effective_filters"]
        bad = await c.patch(
            path,
            json={
                "version": r["view"]["version"] - 1,
                "effective_changes": [{"field": "colors", "action": "clear"}],
            },
        )
        assert bad.status_code == 409
        r = (await c.patch(path, json={"version": r["view"]["version"], "restore": "reset"})).json()
        assert field(r, "minimum_quantity")["number"] == 250 and field(r, "colors")["values"] == ["DARK_BLUE"]
        await c.post(f"/api/projects/{project}/requests", json={"query": "ручки"})
        activated = await c.post(
            f"/api/projects/{project}/queries/{submitted['query']['id']}/activate", json={}
        )
        assert (
            activated.status_code == 200
            and field(activated.json()["research"], "minimum_quantity")["number"] == 250
        )
        assert (await c.get(f"/api/projects/{project}/collection")).json() == collection
    await app.state.engine.dispose()


@pytest.mark.parametrize(
    "field_name,value,expected",
    [
        ("brands", {"values": ["beta"]}, {"vto:fields-b"}),
        ("materials", {"values": ["дерево"]}, {"vto:fields-b"}),
        ("sizes", {"values": ["A6"]}, {"vto:fields-b"}),
        ("min_price", {"number": 150.5}, {"vto:fields-b"}),
        ("max_price", {"number": 150}, {"vto:fields-a"}),
    ],
)
async def test_other_effective_controls_change_real_eligible_offers(grid, field_name, value, expected):
    g, chat = grid
    await g.index.ingest(
        [
            Product(
                id="vto:fields-" + key,
                supplier="vto",
                name="Зонт контрольный",
                brand=brand,
                material=material,
                original_price=price,
                original_currency="KZT",
                metadata={"size": size},
                source_url="https://vto.kz/product/fields-" + key + "/",
            )
            for key, brand, material, size, price in [
                ("a", "Alpha", "сталь", "A5", 100),
                ("b", "Beta", "дерево", "A6", 200),
            ]
        ]
    )
    r = await g.start(chat, "зонты")
    assert set(r.pool_offer_ids) == {"vto:fields-a", "vto:fields-b"}
    r = await edit(g, r, field_name, **value)
    assert set(r.pool_offer_ids) == expected
    state = field(await g.grid_response(r), field_name)
    assert state["mode"] == "common"
    assert state["values"] == value["values"] if "values" in value else state["number"] == value["number"]
    r = await edit(g, r, field_name, "clear")
    assert set(r.pool_offer_ids) == {"vto:fields-a", "vto:fields-b"}


async def test_chat_refinement_after_manual_edit_preserves_only_effective_context(settings):
    app = create_app(settings, [])
    async with app.state.engine.begin() as db:
        await db.run_sync(Base.metadata.create_all)
    await app.state.index.ingest(products())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.post("/api/chats", json={"project_name": "Effective refinement"})
        project = (await c.get("/api/projects")).json()[0]["id"]
        path = f"/api/projects/{project}/requests"
        r = (await c.post(path, json={"query": QUERY})).json()["research"]
        changed = await c.patch(
            f"/api/researches/{r['id']}/view",
            json={
                "version": r["view"]["version"],
                "effective_changes": [{"field": "minimum_quantity", "action": "set", "number": 500}],
            },
        )
        assert changed.status_code == 200
        refined = await c.post(path, json={"query": "теперь красные"})
        assert refined.status_code in (200, 202)
        r = refined.json()["research"]
        assert field(r, "colors")["values"] == ["RED"] and field(r, "minimum_quantity")["number"] == 500
        assert not r["view"]["effective_overrides"]
        assert {p["id"] for p in r["products"]} == {
            f"vto:scope-{category}-red" for category in ["pen", "notebook", "shopperbag", "thermomug"]
        }
        reset = (
            await c.patch(
                f"/api/researches/{r['id']}/view", json={"version": r["view"]["version"], "restore": "reset"}
            )
        ).json()
        assert field(reset, "minimum_quantity")["number"] == 500
    await app.state.engine.dispose()
