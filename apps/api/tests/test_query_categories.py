"""Independent branch truth across the full pool, saved views and HTTP locks."""

import asyncio
from copy import deepcopy

import pytest
from httpx import ASGITransport, AsyncClient
from test_product_grid import service

from app.database.conversations import ConversationRepository
from app.database.models import Base
from app.domain.models import Product, ProductColor
from app.domain.research import ResearchFilters, ResearchSession
from app.main import create_app


def product(id, name, brand="Alpha", color="красный"):
    return Product(
        id="vto:" + id,
        supplier="vto",
        name=name,
        brand=brand,
        colors=[ProductColor(original_color=color)],
        stock_quantity=600,
        original_price=500,
        original_currency="KZT",
        material="металл",
        source_url="https://vto.kz/product/" + id + "/",
        metadata={"parent_name": name},
    )


def catalog():
    return (
        [product("pen-" + str(n), "Ручка шариковая Linear") for n in range(125)]
        + [product("bottle-" + str(n), "Бутылка Water", "Beta", "синий") for n in range(25)]
        + [product("bag-" + str(n), "Рюкзак Path", "Alpha", "синий") for n in range(5)]
        + [
            product("hybrid", "Ручка и карандаш 2 в 1"),
            product("pencil", "Карандаш деревянный Solo"),
            product("umbrella", "Зонт с ручкой"),
        ]
    )


@pytest.fixture
async def grid(repository, settings):
    value = service(repository, settings)
    await value.index.ingest(catalog())
    chat = await ConversationRepository(repository.sessions).create("Query branches")
    return value, chat


async def ids(grid, r):
    page = await grid.grid_response(r)
    result = [p["id"] for p in page["products"]]
    while page["next_cursor"]:
        page = await grid.grid_response(r, page["next_cursor"])
        result.extend(p["id"] for p in page["products"])
    assert len(result) == len(set(result))
    return set(result)


async def edit(grid, r, *actions, **kwargs):
    return await grid.grid_change(r.id, version=r.view.version, branch_actions=list(actions), **kwargs)


async def test_full_pool_hidden_counts_paging_and_selection(grid):
    g, chat = grid
    r = await g.start(chat, "ручки бутылки рюкзаки")
    assert [b.category for b in r.intent.branches] == ["pen", "bottle", "backpack"]
    initial = [b.model_dump(mode="json") for b in r.intent.branches]
    pen, bottle, bag = r.intent.branches
    selected = ["vto:pen-0", "vto:bottle-0", "vto:bag-0"]
    r = await g.grid_change(r.id, version=r.view.version, selected_ids=selected)
    first = await g.grid_response(r)
    assert first["base_total"] == 156 and len(first["products"]) == 100
    assert [b["count"] for b in first["query_categories"]] == [126, 25, 5]
    cursor = first["next_cursor"]
    r = await edit(g, r, {"action": "hide", "branch_id": bottle.id})
    value = await g.grid_response(r)
    assert value["visible_total"] == 131 and value["eligible_total"] == 156
    assert value["hidden_selected_count"] == 1 and value["view"]["selected_ids"] == selected
    assert [b["count"] for b in value["query_categories"]] == [126, 25, 5]
    assert r.intent.branches[1].model_dump(mode="json") == initial[1]
    assert not any(id.startswith("vto:bottle-") for id in await ids(g, r))
    with pytest.raises(ValueError, match="STALE_CURSOR"):
        await g.grid_response(r, cursor)
    r = await edit(g, r, {"action": "show", "branch_id": bottle.id})
    assert len(await ids(g, r)) == 156
    with pytest.raises(ValueError, match="STALE_CURSOR"):
        await g.grid_response(r, cursor)  # hide/show roundtrip still invalidates it
    r = await g.grid_change(r.id, version=r.view.version, filters=ResearchFilters(brands=["Alpha"]))
    r = await edit(g, r, *({"action": "hide", "branch_id": b.id} for b in r.intent.branches))
    value = await g.grid_response(r)
    assert value["result_state"] == "ALL_CATEGORIES_HIDDEN" and not value["products"]
    assert value["base_total"] == 156 and value["eligible_total"] == 131
    assert [b["count"] for b in value["query_categories"]] == [126, 0, 5]
    r = await edit(g, r, {"action": "show_all"})
    assert r.view.filters.brands == ["Alpha"] and len(await ids(g, r)) == 131
    reread = await g.repository.get(r.id)
    assert [b.id for b in reread.intent.branches] == [pen.id, bottle.id, bag.id]
    assert reread.view == r.view


async def test_multifunction_survives_in_first_visible_matching_branch(grid):
    g, chat = grid
    r = await g.start(chat, "ручки, карандаши")
    assert len(await ids(g, r)) == 127
    r = await edit(g, r, {"action": "hide", "branch_id": r.intent.branches[0].id})
    value = await g.grid_response(r)
    assert {p["id"] for p in value["products"]} == {"vto:pencil", "vto:hybrid"}
    hybrid = next(p for p in value["products"] if p["id"] == "vto:hybrid")
    assert hybrid["branch_category"] == "pencil" and hybrid["matched_categories"] == ["pencil"]
    assert "vto:umbrella" not in await ids(g, r)


async def test_remove_restore_uses_history_preserves_other_edits_and_position(grid):
    g, chat = grid
    r = await g.start(chat, "красные ручки, синие бутылки и синие рюкзаки от 300 шт.")
    old = deepcopy(r.intent)
    r = await g.grid_change(r.id, selected_ids=["vto:bag-0"])
    r = await edit(g, r, {"action": "remove", "branch_id": r.intent.branches[-1].id})
    value = await g.grid_response(r)
    assert [b["concept"] for b in value["query_categories"]] == ["pen", "bottle"]
    assert "Рюкзак" not in value["effective_title"] and value["hidden_selected_count"] == 1
    assert value["intent"]["raw_query"] == old.raw_query
    token = value["category_notice"]["id"]
    r = await g.grid_change(r.id, version=r.view.version, filters=ResearchFilters(brands=["Alpha"]))
    r = await edit(g, r, {"action": "restore", "removal_id": token})
    assert r.intent.branches == old.branches and r.view.filters.brands == ["Alpha"]
    assert r.view.selected_ids == ["vto:bag-0"] and not (await g.grid_response(r))["category_notice"]
    # Restoring B while A is absent must insert before C, not after it.
    r = await edit(g, r, {"action": "remove", "branch_id": old.branches[1].id})
    token_b = (await g.grid_response(r))["category_notice"]["id"]
    r = await edit(g, r, {"action": "remove", "branch_id": old.branches[0].id})
    token_a = (await g.grid_response(r))["category_notice"]["id"]
    r = await edit(g, r, {"action": "restore", "removal_id": token_b})
    assert [b.id for b in r.intent.branches] == [old.branches[1].id, old.branches[2].id]
    r = await edit(g, r, {"action": "restore", "removal_id": token_a})
    assert r.intent.branches == old.branches


async def test_last_branch_differs_from_unrestricted_brand_and_new_search(grid):
    g, chat = grid
    r = await g.start(chat, "ручки Alpha")
    r = await edit(g, r, {"action": "remove", "branch_id": r.intent.branches[0].id})
    value = await g.grid_response(r)
    assert value["result_state"] == "NO_QUERY_CATEGORIES" and value["base_total"] == 0
    assert not value["categories_unrestricted"] and value["intent"]["brands"] == ["Alpha"]
    r = await g.grid_change(r.id, restore="undo")
    assert len(await ids(g, r)) == 126
    brand = await g.start(chat, "Alpha")
    value = await g.grid_response(brand)
    assert value["categories_unrestricted"] and not value["query_categories"]
    r = await edit(g, r, {"action": "hide", "branch_id": r.intent.branches[0].id})
    fresh = await g.start(chat, "зонты")
    assert not fresh.view.hidden_branch_ids and len(await ids(g, fresh)) == 1


async def test_unknown_zero_and_model_provenance_and_duplicate_branches(grid):
    g, chat = grid
    r = await g.start(chat, "ручки, UnicornCategory729, рюкзаки")
    value = await g.grid_response(r)
    unresolved = [b for b in value["query_categories"] if not b["resolved"]]
    assert len(unresolved) == 1 and unresolved[0]["label"] == "Категория не распознана"
    assert "UnicornCategory729" in unresolved[0]["source_text"]
    known_zero = await g.start(chat, "носки")
    assert (await g.grid_response(known_zero))["query_categories"][0]["resolved"]
    r = await g.start(chat, "Ручка шариковая Linear")
    value = await g.grid_response(r)
    assert len(value["query_categories"]) == 1 and value["intent"]["models"] == ["Linear"]
    await g.index.ingest([product("ocean", "Ручка шариковая Ocean", "Beta", "синий")])
    r = await g.start(chat, "красные ручки модели Linear и синие ручки модели Ocean")
    assert len(r.intent.branches) == 2
    assert r.intent.branches[0].colors == ["RED"] and "BLUE" in r.intent.branches[1].colors
    assert "RED" not in r.intent.branches[1].colors
    assert [b.models for b in r.intent.branches] == [["Linear"], ["Ocean"]]
    assert len({b.id for b in r.intent.branches}) == 2
    assert len(await ids(g, r)) == 126
    r = await edit(g, r, {"action": "hide", "branch_id": r.intent.branches[0].id})
    assert await ids(g, r) == {"vto:ocean"}
    r = await g.start(chat, "ручки, ручки")
    assert len(r.intent.branches) == 1


def test_legacy_branch_ids_are_stable_without_schema_reset():
    payload = {
        "id": "old-research",
        "chat_id": "old-chat",
        "intent": {"raw_query": "ручки", "branches": [{"category": "pen", "label": "pen"}]},
    }
    a = ResearchSession.model_validate(payload)
    b = ResearchSession.model_validate(deepcopy(payload))
    assert a.intent.branches[0].id == b.intent.branches[0].id


async def test_invalid_batch_is_atomic_and_stale_version_rejected(grid):
    g, chat = grid
    r = await g.start(chat, "ручки бутылки")
    with pytest.raises(ValueError, match="UNKNOWN_BRANCH"):
        await edit(
            g,
            r,
            {"action": "hide", "branch_id": r.intent.branches[0].id},
            {"action": "remove", "branch_id": "missing"},
        )
    fresh = await g.repository.get(r.id)
    assert fresh.view == r.view and fresh.history == r.history
    r = await edit(g, r, {"action": "hide", "branch_id": r.intent.branches[0].id})
    with pytest.raises(ValueError, match="STATE_CONFLICT"):
        await g.grid_change(r.id, version=0, branch_actions=[{"action": "show_all"}])


async def test_chip_actions_do_not_resolve_query_or_call_model(grid):
    g, chat = grid
    r = await g.start(chat, "ручки бутылки")

    async def forbidden(*args, **kwargs):
        raise AssertionError("A known branch action must not parse a query or contact a model")

    g.grid_identity = forbidden
    r = await edit(g, r, {"action": "hide", "branch_id": r.intent.branches[0].id})
    r = await edit(g, r, {"action": "show_all"})
    r = await edit(g, r, {"action": "remove", "branch_id": r.intent.branches[1].id})
    notice = (await g.grid_response(r))["category_notice"]
    r = await edit(g, r, {"action": "restore", "removal_id": notice["id"]})
    assert len(r.intent.branches) == 2


async def test_context_restore_after_undo_preserves_removal_snapshot(grid):
    g, chat = grid
    r = await g.start(chat, "ручки бутылки")
    r = await edit(g, r, {"action": "remove", "branch_id": r.intent.branches[1].id})
    notice = (await g.grid_response(r))["category_notice"]
    removal_snapshot = deepcopy(r.history[-1])
    r = await edit(g, r, {"action": "restore", "removal_id": notice["id"]})
    assert r.history[-2] == removal_snapshot
    r = await g.grid_change(r.id, version=r.view.version, restore="undo")
    assert (await g.grid_response(r))["category_notice"]["id"] == notice["id"]
    r = await edit(g, r, {"action": "restore", "removal_id": notice["id"]})
    assert len(r.intent.branches) == 2


async def test_leading_brand_and_model_constraints_are_local_to_their_position(grid):
    g, chat = grid
    r = await g.start(chat, "красные ручки Alpha, синие бутылки и синие рюкзаки")
    assert r.intent.brands == [] and r.intent.branches[0].brands == ["Alpha"]
    assert not r.intent.branches[1].brands and len(await ids(g, r)) == 156
    before = deepcopy(r.intent.branches)
    r = await edit(g, r, {"action": "hide", "branch_id": r.intent.branches[1].id})
    assert r.intent.branches == before and len(await ids(g, r)) == 131
    r = await g.start(chat, "ручки модели Linear, бутылки")
    assert r.intent.models == [] and r.intent.branches[0].models == ["Linear"]
    assert len(await ids(g, r)) == 150


async def test_open_catalog_category_with_no_eligible_stock_is_resolved(grid):
    g, chat = grid
    flask = product("flask", "Фляжка металлическая")
    flask.stock_quantity = flask.total_quantity = 0
    flask.metadata["supplier_category"] = "фляжки"
    await g.index.ingest([flask])
    r = await g.start(chat, "фляжки от 100 шт.")
    value = await g.grid_response(r)
    assert value["matching_count"] == 0 and value["query_categories"][0]["resolved"]


async def test_http_commands_concurrent_tabs_ownership_and_collection(settings):
    settings.auth_required = True
    app = create_app(settings, [])
    async with app.state.engine.begin() as db:
        await db.run_sync(Base.metadata.create_all)
    await app.state.index.ingest(catalog())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.post(
            "/api/auth/register", json={"name": "categories-owner", "password": "categories-password-123"}
        )
        await c.post("/api/chats", json={"project_name": "Categories"})
        p = (await c.get("/api/projects")).json()[0]["id"]

        async def submit(query):
            response = await c.post(f"/api/projects/{p}/requests", json={"query": query})
            assert response.status_code == 202, response.text
            return response.json()["research"]

        r = await submit("ручки бутылки рюкзаки")
        await c.post(f"/api/projects/{p}/collection/items", json={"offer_id": "vto:bag-0"})
        original = r["query_categories"]
        r = await submit("Скрой бутылки")
        assert r["view"]["hidden_branch_ids"] == [original[1]["id"]]
        r = await submit("Покажи бутылки снова")
        assert not r["view"]["hidden_branch_ids"]
        r = await submit("Убери бутылки из запроса")
        assert len(r["query_categories"]) == 2
        r = await submit("Покажи все категории")
        assert len(r["query_categories"]) == 2
        r = await submit("Добавь ежедневники")
        r = await submit("Добавь ежедневники")
        assert len(r["query_categories"]) == 3
        assert not r["query_categories"][-1]["hidden"]
        url = f"/api/researches/{r['id']}/view"

        async def hide(id):
            return await c.patch(
                url,
                json={
                    "version": r["view"]["version"],
                    "branch_actions": [{"action": "hide", "branch_id": id}],
                },
            )

        responses = await asyncio.gather(
            hide(r["query_categories"][0]["id"]), hide(r["query_categories"][1]["id"])
        )
        assert sorted(response.status_code for response in responses) == [200, 409]
        fresh = (await c.get(f"/api/researches/{r['id']}")).json()
        assert len(fresh["view"]["hidden_branch_ids"]) == 1
        collection = (await c.get(f"/api/projects/{p}/collection")).json()
        assert collection["selected_ids"] == ["vto:bag-0"]
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as foreign:
            await foreign.post(
                "/api/auth/register",
                json={"name": "categories-foreign", "password": "categories-password-456"},
            )
            assert (
                await foreign.patch(
                    url,
                    json={"version": fresh["view"]["version"], "branch_actions": [{"action": "show_all"}]},
                )
            ).status_code == 404
    await app.state.engine.dispose()
