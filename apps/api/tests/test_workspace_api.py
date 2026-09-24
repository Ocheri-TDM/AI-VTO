from datetime import timedelta

from conftest import FakeProvider
from httpx import ASGITransport, AsyncClient

from app.database.models import Base, SearchSession
from app.domain.models import utcnow
from app.main import create_app


async def test_workspace_authority_history_projects_and_expiry(settings):
    provider = FakeProvider()
    app = create_app(settings, [provider])
    async with app.state.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            chat = (await client.post("/api/chats", json={"project_name": "Halyk Tech Gifts"})).json()["id"]
            projects = (await client.get("/api/projects")).json()
            project = next(p for p in projects if p["chat_id"] == chat)
            assert project["name"] == "Halyk Tech Gifts"
            base = f"/api/chats/{chat}"
            result = (
                await client.post(base + "/messages?legacy=true", json={"message": "Термокружки navy, тираж 300"})
            ).json()
            assert result["pool_count"] >= len(result["session"]["products"])
            assert not result["can_undo"]
            product = result["session"]["products"][0]
            result = (await client.post(base + "/selection", json={"product_ids": [product["id"]]})).json()
            assert result["can_undo"]
            filters = result["active_state"]["filters"]
            filters["budget"] = {"max": 0}
            patch = {"state_version": result["state_version"], "filters": filters, "sorting": "price_desc"}
            result = (await client.patch(base + "/workspace", json=patch)).json()
            assert result["session"]["products"] == []
            assert result["selected_products"][0]["id"] == product["id"]
            assert not {"supplier", "stock_quantity", "metadata"}.intersection(result["selected_products"][0])
            assert result["active_state"]["sorting"] == "price_desc"
            assert (await client.patch(base + "/workspace", json=patch)).status_code == 409
            result = (
                await client.patch(
                    base + "/workspace", json={"state_version": result["state_version"], "restore": "undo"}
                )
            ).json()
            assert result["session"]["products"] and result["active_state"]["sorting"] == "relevance"
            filters = result["active_state"]["filters"]
            filters["selection"] = "unselected"
            result = (
                await client.patch(
                    base + "/workspace", json={"state_version": result["state_version"], "filters": filters}
                )
            ).json()
            assert product["id"] not in {p["id"] for p in result["session"]["products"]}
            restored = (await client.get(base)).json()
            assert restored["current"]["active_state"] == result["active_state"]
            assert provider.calls == 1
            assert (
                await client.patch(
                    f"/api/projects/{project['id']}", json={"name": "Updated", "client_name": "Halyk"}
                )
            ).status_code == 200
            assert (await client.get(base)).json()["project"]["name"] == "Updated"
            async with app.state.conversations.sessions.begin() as db:
                record = await db.get(SearchSession, result["search_session_id"])
                record.expires_at = utcnow() - timedelta(seconds=1)
            assert (await client.get(base)).json()["expired"]
            assert (
                await client.patch(
                    base + "/workspace", json={"state_version": result["state_version"], "restore": "undo"}
                )
            ).status_code == 410
            assert (await client.post(base + "/selection", json={"mode": "clear"})).status_code == 410
            assert (await client.delete(f"/api/projects/{project['id']}")).status_code == 204
            assert (await client.get(base)).status_code == 404
