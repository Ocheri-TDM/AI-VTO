"""Live grid HTTP acceptance; deletes only its exact recorded users/projects."""

# ruff: noqa: E402
import argparse
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


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--baseline", action="store_true")
    args = parser.parse_args()
    users, projects, result = [], [], {}
    last_sql = None
    async with httpx.AsyncClient(base_url=args.base, timeout=180) as client:

        async def call(method, path, **kwargs):
            nonlocal last_sql
            response = await client.request(method, path, **kwargs)
            response.raise_for_status()
            last_sql = response.headers.get("X-SQL-Queries")
            return response

        async def submit(query, label):
            started = time.perf_counter()
            response = await call("POST", f"/api/projects/{projects[0]}/requests", json={"query": query})
            value = response.json()
            r = value.get("research", {})
            result[label] = {
                "seconds": round(time.perf_counter() - started, 3),
                "bytes": len(response.content),
                "mode": value["mode"],
                "base_total": r.get("base_total", r.get("pool_count")),
                "filtered_total": r.get("filtered_total", r.get("matching_count")),
                "timings": r.get("timings"),
                "http_sql_queries": last_sql,
            }
            return value

        async def change(r, **changes):
            response = await call(
                "PATCH", f"/api/researches/{r['id']}/view", json={"version": r["view"]["version"], **changes}
            )
            return response.json()

        try:
            account = await call(
                "POST",
                "/api/auth/register",
                json={"name": "grid-check-" + uuid4().hex, "password": secrets.token_urlsafe(24)},
            )
            users.append(account.json()["id"])
            await call("POST", "/api/chats", json={"project_name": "Grid HTTP acceptance"})
            projects.append((await call("GET", "/api/projects")).json()[0]["id"])
            payload = await submit("ручки, термокружки, ежедневники, рюкзаки", "multi")
            if args.baseline:
                hard = payload.get("hard_request")
                if hard:
                    for _ in range(180):
                        value = (await call("GET", f"/api/hard-requests/{hard['id']}")).json()
                        if value["status"] not in ("RUNNING", "PENDING", "QUEUED"):
                            result["assembly_status"] = value["status"]
                            break
                        await asyncio.sleep(1)
                return
            r = payload["research"]
            assert (
                payload["mode"] == "GRID"
                and len(r["intent"]["branches"]) == 4
                and "hard_request" not in payload
            )
            old = (await call("GET", f"/api/projects/{projects[0]}/queries")).json()[0]["id"]
            if r["next_cursor"]:
                started = time.perf_counter()
                response = await call(
                    "GET", f"/api/researches/{r['id']}/products", params={"cursor": r["next_cursor"]}
                )
                result["page2"] = {
                    "seconds": round(time.perf_counter() - started, 3),
                    "bytes": len(response.content),
                    "count": len(response.json()["products"]),
                }
                assert not {p["id"] for p in r["products"]} & {p["id"] for p in response.json()["products"]}
            r = (
                await submit(
                    "Чёрные ручки, красные термокружки и синие рюкзаки. От 300 шт.", "local_constraints"
                )
            )["research"]
            for item in r["products"]:
                expected = {"pen": "BLACK", "thermomug": "RED", "backpack": "BLUE"}[item["branch_category"]]
                assert expected in {c["normalized_color"] for c in item["colors"]}
                assert item["availability"]["availableNow"] >= 300
            r = (await submit("Теперь найди рюкзаки", "backpacks"))["research"]
            original = r["intent"]["raw_query"]
            assert any(b["value"] == "molti" for b in r["facets"]["brand"])
            selected = r["products"][0]["id"]
            r = await change(r, selected_ids=[selected])
            started = time.perf_counter()
            r = await change(r, filters=r["view"]["filters"] | {"brands": ["molti"]})
            result["brand_filter"] = {
                "seconds": round(time.perf_counter() - started, 3),
                "bytes": len(json.dumps(r).encode()),
                "total": r["filtered_total"],
                "timings": r["timings"],
                "http_sql_queries": last_sql,
            }
            assert r["intent"]["raw_query"] == original and all(
                p["brand"].strip().casefold() == "molti" for p in r["products"]
            )
            if r["products"]:
                item = r["products"][0]
                filters = r["view"]["filters"] | {
                    "colors": [c["normalized_color"] for c in item["colors"]],
                    "max_price": item["price_kzt"],
                }
                started = time.perf_counter()
                r = await change(r, filters=filters)
                result["brand_color_price"] = {
                    "seconds": round(time.perf_counter() - started, 3),
                    "bytes": len(json.dumps(r).encode()),
                    "total": r["filtered_total"],
                    "timings": r["timings"],
                    "http_sql_queries": last_sql,
                }
            restored = (await call("GET", f"/api/researches/chat/{r['chat_id']}")).json()
            assert restored["view"] == r["view"] and selected in restored["view"]["selected_ids"]
            r = await change(r, restore="reset")
            assert r["filtered_total"] == r["base_total"]
            await submit("Теперь найди ручки", "pens")
            r = (await submit("Оставь только Molti", "chat_brand"))["research"]
            assert r["view"]["filters"]["brands"] == ["molti"]
            r = (await submit("Собери 10 наборов из ручек и кружек", "old_wording"))["research"]
            assert all(b["quantity"] is None for b in r["intent"]["branches"])
            r = (
                await submit("Подбери подарки для сотрудников в дороге, без одежды, до 7000 тенге", "purpose")
            )["research"]
            assert r["intent"]["purpose"]
            assert all(p["price_kzt"] is not None and p["price_kzt"] <= 7000 for p in r["products"])
            r = (await call("POST", f"/api/projects/{projects[0]}/queries/{old}/activate")).json()["research"]
            assert len(r["intent"]["branches"]) == 4
            result["checks"] = {
                "selection_restore": True,
                "history": True,
                "no_case": True,
                "local_constraints": True,
                "pagination": True,
            }

            async def concurrent(query):
                async with httpx.AsyncClient(base_url=args.base, timeout=180) as other:
                    response = await other.post(
                        "/api/auth/register",
                        json={
                            "name": "grid-concurrent-" + uuid4().hex,
                            "password": secrets.token_urlsafe(24),
                        },
                    )
                    response.raise_for_status()
                    uid = response.json()["id"]
                    pid = None
                    try:
                        response = await other.post(
                            "/api/chats", json={"project_name": "Grid concurrent acceptance"}
                        )
                        response.raise_for_status()
                        response = await other.get("/api/projects")
                        response.raise_for_status()
                        pid = response.json()[0]["id"]
                        started = time.perf_counter()
                        response = await other.post(f"/api/projects/{pid}/requests", json={"query": query})
                        response.raise_for_status()
                        seconds = round(time.perf_counter() - started, 3)
                        assert response.json()["mode"] == "GRID"
                        foreign = await other.get(f"/api/projects/{projects[0]}/queries")
                        assert foreign.status_code == 404
                        return {
                            "seconds": seconds,
                            "http": response.status_code,
                            "foreign_project": foreign.status_code,
                        }
                    finally:
                        if pid:
                            response = await other.delete(f"/api/projects/{pid}")
                            response.raise_for_status()
                        engine, sessions = create_database(Settings().database_url)
                        async with sessions.begin() as db:
                            await db.execute(delete(User).where(User.id == uid))
                        await engine.dispose()

            result["concurrent"] = await asyncio.gather(
                *(concurrent(q) for q in ["ручки", "рюкзаки", "термокружки"])
            )
        finally:
            for project in projects:
                await call("DELETE", f"/api/projects/{project}")
            engine, sessions = create_database(Settings().database_url)
            async with sessions.begin() as db:
                await db.execute(delete(User).where(User.id.in_(users)))
            await engine.dispose()
            destination = ROOT / ".local" / ("grid-before.json" if args.baseline else "grid-after.json")
            destination.write_text(json.dumps(result, ensure_ascii=False, indent=2), "utf-8")
            print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    asyncio.run(main())
