from conftest import FakeProvider
from httpx import ASGITransport, AsyncClient

from app.database.models import Base
from app.main import create_app


async def test_api_search_details_filter_refresh_and_validation(settings):
    provider = FakeProvider()
    app = create_app(settings, [provider])
    async with app.state.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get("/api/health")).status_code == 200
            assert (await client.post("/api/searches", json={"query": " "})).status_code == 422
            assert (await client.post("/api/searches", json={"query": "ручки тираж 0"})).status_code == 422
            response = await client.post("/api/searches", json={"query": "термокружка тираж 300"})
            assert response.status_code == 202
            session_id = response.json()["id"]
            await app.state.search.wait(session_id)
            result = (await client.get(f"/api/searches/{session_id}")).json()
            assert result["status"] == "completed"
            card = result["products"][0]
            assert card["price_kzt"] == 553
            assert (
                not {"supplier", "stock_quantity", "source_url", "metadata", "original_price"} & card.keys()
            )
            detail = await client.get(f"/api/searches/{session_id}/products/{card['id']}")
            assert detail.json()["stock_quantity"] == 300
            filtered = await client.post(f"/api/searches/{session_id}/filter", json={"max_price_kzt": 500})
            assert filtered.json()["products"] == [] and provider.calls == 1
            original = (await client.get(f"/api/searches/{session_id}")).json()
            assert len(original["products"]) == 1  # filters never destroy source snapshot
            cached = await client.post("/api/searches", json={"query": "термокружка тираж 300"})
            assert cached.status_code == 200 and cached.json()["cache_hit"]
            refreshed = await client.post(f"/api/searches/{session_id}/refresh")
            assert refreshed.status_code == 202 and refreshed.json()["id"] != session_id
            await app.state.search.wait(refreshed.json()["id"])
            assert provider.calls == 2
            assert (await client.get("/openapi.json")).status_code == 200


async def test_database_connection_failure_is_controlled(settings):
    app = create_app(settings, [FakeProvider()])

    async def disconnected(_session_id):
        raise ConnectionRefusedError("private connection information")

    app.state.repository.get = disconnected
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/searches/00000000-0000-0000-0000-000000000000")
        assert response.status_code == 503
        assert "private" not in response.text
    await app.state.engine.dispose()
