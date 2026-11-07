"""Exact-run HTTP/browser acceptance; secrets stay in the local ledger."""

import asyncio
import hashlib
import json
import secrets
import sys
from pathlib import Path

import httpx
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [
    str(ROOT / "apps/api"),
    str(ROOT / "apps/api/tests"),
    str(ROOT / "scripts"),
]
from app.config import Settings
from app.database.connection import create_database

LEDGER = ROOT / ".local/chat-workflow-ledger.json"
PUBLIC = ROOT / "docs/screenshots/chat-workflow"


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str), "utf-8"
    )


async def call(client, method, path, **kw):
    response = await client.request(method, path, **kw)
    assert response.status_code < 400, (
        method,
        path,
        response.status_code,
        response.text[:300],
    )
    return response.json() if response.content else None


async def prepare():
    assert not LEDGER.exists()
    settings = Settings()
    engine, sessions = create_database(settings.database_url)
    state = {"run": secrets.token_hex(8), "users": [], "projects": []}
    try:
        async with sessions() as db:
            await db.execute(text("SET TRANSACTION READ ONLY"))
            state["before"] = {}
            for table, projection, order in [
                (
                    "users",
                    "id,display_name,normalized_name,password_hash,created_at,updated_at,last_login_at",
                    "id",
                ),
                (
                    "projects",
                    "id,user_id,name,client_name,active_query_id,request_epoch,created_at",
                    "id",
                ),
                ("messages", "*", "id"),
                ("project_collection_items", "*", "project_id,offer_id"),
                ("presentation_documents", "*", "id"),
                ("presentation_revisions", "*", "id"),
            ]:
                state["before"][table] = dict(
                    (
                        await db.execute(
                            text(
                                f"SELECT count(*) count,md5(string_agg(row_to_json(t)::text,'' ORDER BY {order})) fingerprint FROM (SELECT {projection} FROM {table}) t"
                            )
                        )
                    )
                    .mappings()
                    .one()
                )
            state["before"]["shares"] = [
                dict(r)
                for r in (
                    await db.execute(
                        text(
                            "SELECT document_id,md5(encrypted_token) token_fingerprint,active,epoch,activated_at,expires_at,activation_source FROM commercial_offer_shares"
                        )
                    )
                ).mappings()
            ]
            state["before"]["catalog"] = await db.scalar(
                text("SELECT count(*) FROM supplier_offers")
            )
        state["key_hash"] = hashlib.sha256(
            (settings.presentation_storage / "keys/commercial-offers.key").read_bytes()
        ).hexdigest()
        save(LEDGER, state)
        async with httpx.AsyncClient(
            base_url="http://127.0.0.1:8000", timeout=120
        ) as client:
            for n in range(2):
                client.cookies.clear()
                name = "chat-workflow-" + state["run"] + "-" + str(n)
                password = secrets.token_urlsafe(24)
                user = await call(
                    client,
                    "POST",
                    "/api/auth/register",
                    json={"name": name, "password": password},
                )
                state["users"].append(
                    {
                        "id": user["id"],
                        "name": name,
                        "password": password,
                        "cookies": dict(client.cookies),
                    }
                )
                save(LEDGER, state)
            client.cookies.clear()
            client.cookies.update(state["users"][0]["cookies"])
            chat = await call(
                client,
                "POST",
                "/api/chats",
                json={"project_name": "Conversation reproduction"},
            )
            project = next(
                p
                for p in await call(client, "GET", "/api/projects")
                if p["chat_id"] == chat["id"]
            )
            state["projects"].append(project["id"])
            save(LEDGER, state)
            for query in ["Синие бутылки, 300 шт.", "Ручки и блокноты"]:
                await call(
                    client,
                    "POST",
                    f"/api/projects/{project['id']}/requests",
                    json={"query": query},
                )
            persisted = await call(client, "GET", f"/api/researches/chat/{chat['id']}")
            async with sessions() as db:
                count = await db.scalar(
                    text("SELECT count(*) FROM messages WHERE chat_id=:id"),
                    {"id": chat["id"]},
                )
            save(
                PUBLIC / "before.json",
                {
                    "research_messages": persisted["messages"],
                    "stored_chat_messages": count,
                    "active_research_message_count": len(persisted["messages"]),
                },
            )
            print(
                "Before: persisted chat messages",
                count,
                "vs active research",
                len(persisted["messages"]),
            )
    finally:
        await engine.dispose()


async def main():
    phase = sys.argv[1]
    if phase == "prepare":
        return await prepare()
    from quality.chat_workflow_acceptance import run

    await run(phase, ROOT, LEDGER, PUBLIC)


if __name__ == "__main__":
    asyncio.run(main())
