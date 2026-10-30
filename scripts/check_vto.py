"""Real HTTP acceptance on the current VTO index; cleans up its exact test users."""

# The standalone command must add the API source directory before app imports.
# ruff: noqa: E402

import asyncio
import json
import secrets
import sys
import time
from pathlib import Path
from uuid import uuid4

import httpx
from sqlalchemy import delete

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))

from app.config import Settings
from app.database.connection import create_database
from app.database.models import User


async def account(client, label):
    response = await client.post("/api/auth/register", json={"name": "vto-check-" + uuid4().hex, "password": secrets.token_urlsafe(24)})
    response.raise_for_status()
    user = response.json()["id"]
    chat = await client.post("/api/chats", json={"project_name": "VTO acceptance " + label})
    chat.raise_for_status()
    chat_id = chat.json()["id"]
    projects = (await client.get("/api/projects")).json()
    return user, next(p["id"] for p in projects if p["chat_id"] == chat_id)


async def main():
    users, projects = [], []
    result = {}
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8000", timeout=90) as client:
        user, project = await account(client, "variants")
        users.append(user)
        projects.append(project)
        try:
            started = time.monotonic()
            response = await client.post(f"/api/projects/{project}/requests", json={"query": "Melango"})
            response.raise_for_status()
            payload = response.json()
            research = payload["research"]
            cards = research["products"]
            result["simple"] = {"mode": payload["mode"], "count": research["matching_count"],
                                 "ids": [p["id"] for p in cards], "seconds": round(time.monotonic() - started, 3)}
            assert payload["mode"] == "GRID"
            assert {"vto:1823107", "vto:1823108", "vto:1823109", "vto:1823110"} == {p["id"] for p in cards}
            rid = research["id"]
            version = research["view"]["version"]
            response = await client.patch(f"/api/researches/{rid}/view", json={"version": version, "filters": {"colors": ["BLUE"]}})
            response.raise_for_status()
            research = response.json()
            assert "vto:1823109" in [p["id"] for p in research["products"]]
            assert all(any(c["normalized_color"] == "BLUE" for c in p["colors"]) for p in research["products"])
            result["blue_filter"] = {"count": research["matching_count"], "id": research["products"][0]["id"]}
            response = await client.patch(f"/api/researches/{rid}/view", json={"version": research["view"]["version"], "selected_ids": ["vto:1823109"]})
            response.raise_for_status()
            research = response.json()
            persisted = (await client.get(f"/api/researches/{rid}")).json()
            assert persisted["view"]["selected_ids"] == ["vto:1823109"]
            result["selection_persisted"] = True
            response = await client.patch(f"/api/researches/{rid}/view", json={"version": research["view"]["version"], "restore": "undo"})
            response.raise_for_status()
            result["undo"] = response.json()["view"]["selected_ids"] == []
            response = await client.post(f"/api/projects/{project}/requests", json={"query": "Найди синие рюкзаки Melango от 300 шт"})
            response.raise_for_status()
            research = response.json()["research"]
            assert "vto:1823109" in [p["id"] for p in research["products"]]
            assert all(p["availability"]["availableNow"] >= 300 for p in research["products"])
            assert research["intent"]["quantity"] == 300
            result["quantity_filter"] = {"count": research["matching_count"], "quantity": research["intent"]["quantity"]}
            response = await client.post(f"/api/projects/{project}/requests", json={"query": "Собери 2 набора из рюкзаков и термокружек"})
            response.raise_for_status()
            payload = response.json()
            assert payload["mode"] == "GRID"
            grid = payload["research"]
            assert len(grid["intent"]["branches"]) == 2
            assert grid["base_total"] > 0 and grid["products"]
            assert all(p["id"].startswith("vto:") for p in grid["products"])
            result["multi_category"] = {"status": grid["status"], "total": grid["base_total"], "branches": 2}
        finally:
            for project_id in projects:
                response = await client.delete(f"/api/projects/{project_id}")
                response.raise_for_status()
            engine, sessions = create_database(Settings().database_url)
            async with sessions.begin() as db:
                await db.execute(delete(User).where(User.id.in_(users)))
            await engine.dispose()
            destination = ROOT / ".local/vto-acceptance.json"
            destination.write_text(json.dumps(result, ensure_ascii=False, indent=2), "utf-8")
            print(json.dumps({key: value for key, value in result.items() if key != "multi_category"}, ensure_ascii=True))



if __name__ == "__main__":
    asyncio.run(main())
