import asyncio

from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.database.models import Base, Project, ProjectCollection, ProjectCollectionItem, SupplierOffer
from app.domain.models import Product, ProductColor
from app.main import create_app


async def app_with_catalog(settings):
    settings.auth_required = True
    app = create_app(settings, [])
    async with app.state.engine.begin() as db:
        await db.run_sync(Base.metadata.create_all)
    await app.state.index.ingest(
        [
            Product(
                id=f"vto:{n}",
                supplier="vto",
                name=name,
                category=category,
                source_url="https://vto.kz/product/control/",
                original_currency="KZT",
                original_price=1000,
                colors=[ProductColor(original_color=color)],
            )
            for n, name, category, color in [
                (1, "Шариковая ручка А", "pen", "синий"),
                (2, "Шариковая ручка Б", "pen", "серый"),
                (3, "Рюкзак для ноутбука", "backpack", "синий"),
                (4, "Бутылка для воды", "bottle", "белый"),
            ]
        ]
    )
    return app


async def project(c, name):
    chat = (await c.post("/api/chats", json={"project_name": name})).json()
    return next(p["id"] for p in (await c.get("/api/projects")).json() if p["chat_id"] == chat["id"])


async def query(c, p, text):
    response = await c.post(f"/api/projects/{p}/requests", json={"query": text})
    assert response.status_code == 202, response.text
    return response.json()["research"]


async def test_collection_survives_search_filters_snapshot_undo_projects_and_foreign_user(settings):
    app = await app_with_catalog(settings)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await c.post(
            "/api/auth/register", json={"name": "collection-owner", "password": "collection-pass-123"}
        )
        p = await project(c, "A")
        base = f"/api/projects/{p}/collection"
        pens = await query(c, p, "ручки")
        for id in ["vto:1", "vto:2"]:
            added = await c.post(base + "/items", json={"offer_id": id, "source_research_id": pens["id"]})
            assert added.status_code == 200, added.text
        bag = await query(c, p, "рюкзаки")
        assert bag["view"]["selected_ids"] == ["vto:1", "vto:2"]
        await c.post(base + "/items", json={"offer_id": "vto:3", "source_research_id": bag["id"]})
        bottle = await query(c, p, "бутылки")
        await c.post(base + "/items", json={"offer_id": "vto:4", "source_research_id": bottle["id"]})
        before = (await c.get(base)).json()
        assert before["selected_ids"] == ["vto:1", "vto:2", "vto:3", "vto:4"]
        assert len(before["products"]) == 4
        again = (await c.post(base + "/items", json={"offer_id": "vto:1"})).json()
        assert again["version"] == before["version"] and again["selected_ids"] == before["selected_ids"]
        hidden = (
            await c.patch(
                f"/api/researches/{bottle['id']}/view",
                json={"version": bottle["view"]["version"], "filters": {"min_price": 999999}},
            )
        ).json()
        assert hidden["products"] == []
        doc = await c.post(f"/api/projects/{p}/presentations", json={"collection_version": before["version"]})
        assert doc.status_code == 201, doc.text
        doc = doc.json()
        assert [i["offer_id"] for i in doc["draft"]["items"]] == before["selected_ids"]
        await c.delete(base + "/items/vto:1")
        assert (await c.get(base)).json()["selected_ids"] == ["vto:2", "vto:3", "vto:4"]
        await c.patch(
            f"/api/researches/{bottle['id']}/view",
            json={"version": hidden["view"]["version"], "restore": "undo"},
        )
        assert (await c.get(base)).json()["selected_ids"] == ["vto:2", "vto:3", "vto:4"]
        assert len((await c.get("/api/presentations/" + doc["id"])).json()["draft"]["items"]) == 4
        assert (
            await c.post(f"/api/projects/{p}/presentations", json={"collection_version": before["version"]})
        ).status_code == 409
        second = await project(c, "B")
        assert (await c.get(f"/api/projects/{second}/collection")).json()["selected_ids"] == []
        assert len((await c.get(base)).json()["selected_ids"]) == 3
        assert (
            await c.post(base + "/clear", json={"version": before["version"], "confirmed": True})
        ).status_code == 409
        fresh = (await c.get(base)).json()
        assert (
            await c.post(base + "/clear", json={"version": fresh["version"], "confirmed": False})
        ).status_code == 422
        cleared = (
            await c.post(base + "/clear", json={"version": fresh["version"], "confirmed": True})
        ).json()
        assert cleared["selected_ids"] == []
        async with AsyncClient(transport=transport, base_url="http://test") as foreign:
            await foreign.post(
                "/api/auth/register", json={"name": "collection-foreign", "password": "collection-pass-456"}
            )
            assert (await foreign.get(base)).status_code == 404
            assert (await foreign.post(base + "/items", json={"offer_id": "vto:1"})).status_code == 404
        # Two tabs share one user, but add/remove operations never replace stale arrays.
        async with AsyncClient(transport=transport, base_url="http://test", cookies=c.cookies) as tab:
            results = await asyncio.gather(
                c.post(base + "/items", json={"offer_id": "vto:1"}),
                tab.post(base + "/items", json={"offer_id": "vto:2"}),
            )
            assert all(r.status_code == 200 for r in results)
        assert set((await c.get(base)).json()["selected_ids"]) == {"vto:1", "vto:2"}
        # Catalog removal must retain an actionable item, not silently delete selection.
        async with app.state.sessions.begin() as db:
            offer = await db.get(SupplierOffer, "vto:1")
            offer.lifecycle = "REMOVED"
        missing = (await c.get(base)).json()
        assert missing["unavailable_ids"] == ["vto:1"] and len(missing["products"]) == 2
        await c.delete(base + "/items/vto:1")
        assert (await c.get(base)).json()["selected_ids"] == ["vto:2"]
    await app.state.engine.dispose()


async def test_lazy_migration_seeds_only_active_selection_and_never_replays_history(settings):
    app = await app_with_catalog(settings)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.post(
            "/api/auth/register", json={"name": "collection-migration", "password": "collection-pass-123"}
        )
        p = await project(c, "Migration")
        await query(c, p, "ручки")
        bag = await query(c, p, "рюкзаки")
        async with app.state.sessions.begin() as db:
            from app.database.models import ResearchRecord

            records = (await db.scalars(select(ResearchRecord))).all()
            for r in records:
                value = dict(r.payload)
                value["view"] = value["view"] | {
                    "selected_ids": ["vto:3"] if r.id == bag["id"] else ["vto:1", "vto:2"]
                }
                r.payload = value
            await db.execute(delete(ProjectCollectionItem).where(ProjectCollectionItem.project_id == p))
            await db.execute(delete(ProjectCollection).where(ProjectCollection.project_id == p))
        migrated = (await c.get(f"/api/projects/{p}/collection")).json()
        assert migrated["selected_ids"] == ["vto:3"]
        owner = (await c.get("/api/auth/me")).json()["id"]
        await app.state.collections.mutate(p, owner, "clear", version=migrated["version"])
        assert (await c.get(f"/api/projects/{p}/collection")).json()["selected_ids"] == []
        async with app.state.sessions() as db:
            assert await db.get(Project, p)
    await app.state.engine.dispose()
