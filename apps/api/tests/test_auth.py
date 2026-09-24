from conftest import FakeProvider
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.database.models import Base, User
from app.main import create_app


async def test_register_login_logout_hash_and_user_isolation(settings):
    app = create_app(settings.model_copy(update={"auth_required": True}), [FakeProvider()])
    async with app.state.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as alice:
            registered = await alice.post(
                "/api/auth/register", json={"name": " Alice ", "password": "strong password"}
            )
            assert registered.status_code == 201
            assert (await alice.get("/api/auth/me")).json()["name"] == "Alice"
            chat_id = (await alice.post("/api/chats", json={"project_name": "Private"})).json()["id"]
            duplicate = await alice.post(
                "/api/auth/register", json={"name": "alice", "password": "another password"}
            )
            assert duplicate.status_code == 409
            assert (await alice.post("/api/auth/logout")).status_code == 204
            assert (await alice.get(f"/api/chats/{chat_id}")).status_code == 401
            assert (await alice.post(
                "/api/auth/login", json={"name": "ALICE", "password": "wrong password"}
            )).status_code == 401
            assert (await alice.post(
                "/api/auth/login", json={"name": "alice", "password": "strong password"}
            )).status_code == 200
            assert (await alice.get(f"/api/chats/{chat_id}")).status_code == 200
        async with AsyncClient(transport=transport, base_url="http://test") as bob:
            assert (await bob.post(
                "/api/auth/register", json={"name": "Bob", "password": "strong password"}
            )).status_code == 201
            assert (await bob.get(f"/api/chats/{chat_id}")).status_code == 404
            assert (await bob.post(
                f"/api/chats/{chat_id}/selection", json={"product_ids": []}
            )).status_code == 404
        async with app.state.sessions() as db:
            user = await db.scalar(select(User).where(User.normalized_name == "alice"))
            assert user and user.password_hash != "strong password"
            assert user.password_hash.startswith("$argon2id$")
