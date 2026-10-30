import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.database.models import Base, CatalogSyncJob, HardRequestRecord
from app.domain.models import Product, ProductColor
from app.main import create_app


async def test_project_grid_filters_history_undo_ownership_and_branch_mutations(settings):
    settings.auth_required = True
    app = create_app(settings, [])
    async with app.state.engine.begin() as db:
        await db.run_sync(Base.metadata.create_all)
    await app.state.index.ingest(
        [
            Product(
                id=f"vto:{i}",
                supplier="vto",
                name=name,
                brand=brand,
                material="металл",
                original_price=500,
                original_currency="KZT",
                stock_quantity=stock,
                colors=[ProductColor(original_color=color)],
                source_url=f"https://vto.kz/product/p-{i}/",
                metadata={"vto_product_id": i},
            )
            for i, name, brand, color, stock in [
                (1, "Ручка шариковая", "Molti", "чёрный", 500),
                (2, "Термокружка", "Brand B", "красный", 400),
                (3, "Рюкзак", "Molti", "синий", 100),
                (4, "Ручка и вечный карандаш 2 в 1", "Molti", "красный", 500),
            ]
        ]
    )
    transport = ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=transport, base_url="http://test") as c,
    ):
        await c.post(
            "/api/auth/register", json={"name": "grid-http-owner", "password": "grid-http-password-123"}
        )
        chat = (await c.post("/api/chats", json={"project_name": "Grid test"})).json()["id"]
        project = (await c.get("/api/projects")).json()[0]["id"]
        endpoint = f"/api/projects/{project}/requests"
        response = await c.post(
            endpoint, json={"query": "Чёрные ручки, красные термокружки и синие рюкзаки. От 300 шт."}
        )
        assert response.status_code == 202, response.text
        payload = response.json()
        assert payload["mode"] == "GRID" and "hard_request" not in payload
        r = payload["research"]
        assert [p["id"] for p in r["products"]] == ["vto:1", "vto:2"]
        assert r["empty_branches"]
        rid = r["id"]
        filtered = (await c.post(endpoint, json={"query": "Оставь только Molti"})).json()["research"]
        assert filtered["id"] == rid and filtered["intent"]["raw_query"] == r["intent"]["raw_query"]
        assert filtered["filtered_total"] == 1
        assert filtered["view"]["filters"]["origins"]["brands"] == "chat"
        reset = (
            await c.patch(
                f"/api/researches/{rid}/view",
                json={"version": filtered["view"]["version"], "restore": "reset"},
            )
        ).json()
        assert reset["filtered_total"] == 2 and reset["intent"]["branches"][0]["quantity"] == 300
        assert (await c.get("/api/researches/chat/" + chat)).json()["id"] == rid
        changed = (await c.post(endpoint, json={"query": "Ручки сделай красными"})).json()["research"]
        assert changed["intent"]["branches"][0]["colors"] == ["RED"]
        assert changed["intent"]["branches"][1]["colors"] == ["RED"]
        assert [p["id"] for p in changed["products"]] == ["vto:4", "vto:2"], changed
        undo = (await c.post(endpoint, json={"query": "Отмени"})).json()["research"]
        assert undo["intent"]["branches"][0]["colors"] == ["BLACK"]
        new = (await c.post(endpoint, json={"query": "Теперь найди зонты"})).json()["research"]
        assert new["id"] != rid and not new["view"]["selected_ids"]
        history = (await c.get(f"/api/projects/{project}/queries")).json()
        old = next(q for q in history if q["research_session_id"] == rid)
        reopened = (await c.post(f"/api/projects/{project}/queries/{old['id']}/activate")).json()["research"]
        assert reopened["id"] == rid
        assert (await c.get("/api/researches/chat/" + chat)).json()["id"] == rid
        assert (await c.post("/api/hard-requests/retired/rebuild")).status_code == 410
        async with AsyncClient(transport=transport, base_url="http://test") as foreign:
            await foreign.post(
                "/api/auth/register", json={"name": "grid-http-foreign", "password": "grid-http-password-456"}
            )
            assert (await foreign.get(f"/api/researches/{rid}")).status_code == 404
            assert (await foreign.post(endpoint, json={"query": "ручки"})).status_code == 404
        async with app.state.sessions() as db:
            assert await db.scalar(select(func.count()).select_from(HardRequestRecord)) == 0
            assert await db.scalar(select(func.count()).select_from(CatalogSyncJob)) == 0


async def test_stale_cursor_and_stale_view_rejected(repository, settings):
    from app.application.index_research import IndexedResearchService
    from app.database.conversations import ConversationRepository
    from app.database.index import IndexRepository
    from app.database.research import ResearchRepository

    chat = await ConversationRepository(repository.sessions).create("Cursor")
    service = IndexedResearchService(
        ResearchRepository(repository.sessions),
        [],
        settings,
        None,
        IndexRepository(repository.sessions, settings),
    )
    await service.index.ingest(
        [
            Product(
                id=f"vto:{i}", supplier="vto", name="Ручка шариковая", source_url="https://vto.kz/product/p/"
            )
            for i in range(101)
        ]
    )
    r = await service.start(chat, "ручки")
    cursor = (await service.grid_response(r))["next_cursor"]
    changed = await service.change(r.id, sorting="price_asc", version=0)
    with pytest.raises(ValueError, match="STALE_CURSOR"):
        await service.grid_response(changed, cursor)
    with pytest.raises(ValueError, match="STATE_CONFLICT"):
        await service.change(r.id, version=0, restore="reset")
