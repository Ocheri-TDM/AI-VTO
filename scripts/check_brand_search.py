"""Brand/model main-API acceptance. Only recorded disposable IDs may be removed."""

import argparse
import asyncio
import json
import secrets
import sys
import time
from pathlib import Path
from uuid import uuid4

import httpx
from sqlalchemy import delete, select, text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))
from app.config import Settings
from app.database.connection import create_database
from app.database.models import ResearchRecord, User

LEDGER = ROOT / ".local/brand-search-acceptance.json"
QUERIES = [
    "Ручка шариковая Tippo",
    "Molti",
    "ручки Molti",
    "чёрные рюкзаки Molti до 10000 тенге",
    "Рюкзак Melango",
    "ручки и рюкзаки бренда Molti",
    "бренд UnknownBrand729",
]


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "phase", choices=["prepare", "baseline", "run", "extended", "cleanup"]
    )
    args = parser.parse_args()
    state = (
        json.loads(LEDGER.read_text("utf-8"))
        if LEDGER.exists()
        else {"run": uuid4().hex, "users": [], "projects": [], "evidence": {}}
    )

    def persist():
        LEDGER.write_text(json.dumps(state, ensure_ascii=False, indent=2), "utf-8")

    engine, sessions = create_database(Settings().database_url)

    async def fingerprint(db):
        return dict(
            (
                await db.execute(
                    text(
                        "SELECT count(*) offers,md5(string_agg(o.id||o.product_id||o.source_url||coalesce(o.payload::text,'')||coalesce(p.payload::text,'')||coalesce(p.observed_at::text,''),'' ORDER BY o.id)) fingerprint FROM supplier_offers o LEFT JOIN product_observations p ON p.offer_id=o.id"
                    )
                )
            )
            .mappings()
            .one()
        )

    async def call(c, method, path, **kw):
        r = await c.request(method, path, **kw)
        r.raise_for_status()
        return None if r.status_code == 204 else r.json()

    async def record(c, research, label, action=None, seconds=None):
        ids = [p["id"] for p in research["products"]]
        cursor = research["next_cursor"]
        while cursor:
            page = await call(
                c,
                "GET",
                f"/api/researches/{research['id']}/products",
                params={"cursor": cursor, "limit": 100},
            )
            ids.extend(p["id"] for p in page["products"])
            cursor = page["next_cursor"]
        assert len(ids) == len(set(ids)) == research["filtered_total"]
        async with sessions() as db:
            persisted = (
                await db.execute(
                    select(ResearchRecord.payload).where(
                        ResearchRecord.id == research["id"]
                    )
                )
            ).scalar_one()
        item = {
            "query": label,
            "action": action,
            "intent": research["intent"],
            "view": research["view"],
            "base_total": research["base_total"],
            "total": research["filtered_total"],
            "offer_ids": ids,
            "research_id": research["id"],
            "seconds": seconds,
            "notices": research.get("identity_notices"),
            "funnel": persisted.get("timings", {}).get("identity_funnel"),
            "persisted_intent": persisted["intent"],
        }
        assert item["intent"] == item["persisted_intent"]
        state["evidence"].setdefault(args.phase, {})[label] = item
        persist()
        return research, set(ids)

    async def submit(c, project, query):
        started = time.perf_counter()
        response = await c.post(
            f"/api/projects/{project}/requests", json={"query": query}
        )
        response.raise_for_status()
        result = response.json()
        state.setdefault("query_ids", {})[query] = (
            result.get("query", {}).get("id") if result.get("query") else None
        )
        return await record(
            c,
            result["research"],
            query,
            result["route"]["action"],
            round(time.perf_counter() - started, 4),
        )

    clients = []
    try:
        if args.phase == "prepare":
            assert not state["users"], "Existing run must be cleaned first"
            async with sessions() as db:
                state["catalog_before"] = await fingerprint(db)
                state["documents_before"] = [
                    dict(r)
                    for r in (
                        await db.execute(
                            text(
                                "SELECT id,md5(draft::text) fingerprint FROM presentation_documents ORDER BY id"
                            )
                        )
                    )
                    .mappings()
                    .all()
                ]
            for n in range(2):
                c = httpx.AsyncClient(base_url="http://127.0.0.1:8000", timeout=180)
                clients.append(c)
                u = await call(
                    c,
                    "POST",
                    "/api/auth/register",
                    json={
                        "name": f"brand-{state['run']}-{n}",
                        "password": secrets.token_urlsafe(24),
                    },
                )
                state["users"].append({"id": u["id"], "cookies": dict(c.cookies)})
                persist()
                chat = await call(
                    c,
                    "POST",
                    "/api/chats",
                    json={"project_name": "Brand acceptance " + state["run"][:8]},
                )
                p = next(
                    v
                    for v in await call(c, "GET", "/api/projects")
                    if v["chat_id"] == chat["id"]
                )
                state["projects"].append(p["id"])
                persist()
            print("Two isolated users/projects prepared")
        else:
            clients = [
                httpx.AsyncClient(
                    base_url="http://127.0.0.1:8000", timeout=180, cookies=u["cookies"]
                )
                for u in state["users"]
            ]
            if args.phase == "cleanup":
                for p in state.get("browser_projects", []):
                    response = await clients[0].delete("/api/projects/" + p)
                    assert response.status_code in (204, 404)
                for c, p in zip(clients, state["projects"], strict=True):
                    response = await c.delete("/api/projects/" + p)
                    assert response.status_code in (204, 404)
                async with sessions.begin() as db:
                    await db.execute(
                        delete(User).where(
                            User.id.in_([u["id"] for u in state["users"]])
                        )
                    )
                LEDGER.unlink()
                print("Only recorded disposable users/projects removed")
                return
            c = clients[0]
            if args.phase == "extended":
                state["evidence"]["extended"] = {}
                state["brand_or"] = []
                persist()
                project = state["projects"][0]
                async with sessions() as db:
                    molti = set(
                        await db.scalars(
                            text(
                                "SELECT id FROM supplier_offers WHERE payload->>'brand'='Molti' AND lifecycle<>'REMOVED'"
                            )
                        )
                    )
                    tippo = set(
                        await db.scalars(
                            text(
                                "SELECT id FROM supplier_offers WHERE payload->'metadata'->>'parent_name'='Ручка шариковая Tippo'"
                            )
                        )
                    )
                r, ids = await submit(c, project, "Molti")
                assert ids == molti and r["intent"]["categories"] == []
                r, ids = await submit(c, project, "ROLY")
                assert len(ids) > 100
                r, ids = await submit(c, project, "Ручка шариковая Tippo")
                assert ids == tippo and len(ids) == 6
                for brand in ["Molti", "B1"]:
                    _, expected = await submit(c, project, "ручки " + brand)
                    r, _ = await submit(c, project, "ручки")
                    r = await call(
                        c,
                        "PATCH",
                        f"/api/researches/{r['id']}/view",
                        json={
                            "version": r["view"]["version"],
                            "filters": r["view"]["filters"]
                            | {"brands": [brand.casefold()]},
                        },
                    )
                    _, actual = await record(c, r, "HUD ручки " + brand)
                    assert actual == expected
                for q in ["ручки B1", "ручки Lecce Pen"]:
                    _, pool = await submit(c, project, q)
                    assert pool, "Both real brands must contribute candidates"
                    state.setdefault("brand_or", []).append(sorted(pool))
                _, combined = await submit(c, project, "ручки бренда B1 или Lecce Pen")
                assert combined == set().union(
                    *(set(p) for p in state["brand_or"][-2:])
                )
                _, pens = await submit(c, project, "ручки B1")
                _, bags = await submit(c, project, "рюкзаки Molti")
                _, local = await submit(
                    c, project, "ручки бренда B1 и рюкзаки бренда Molti"
                )
                assert local == pens | bags
                r, _ = await submit(c, project, "бренд UnknownBrand729")
                assert r["filtered_total"] == 0 and r["identity_notices"]
                r, _ = await submit(c, project, "UnknownBrand729")
                assert r["filtered_total"] == 0 and r["identity_notices"]
                r, _ = await submit(c, project, "ручка модель UnknownModel729")
                assert r["filtered_total"] == 0 and r["identity_notices"]
                r, melango = await submit(c, project, "Рюкзак Melango")
                assert len(melango) == 4
                await call(
                    c,
                    "POST",
                    f"/api/projects/{project}/collection/items",
                    json={"offer_id": "vto:1823109"},
                )
                _, vortex = await submit(c, project, "ручка VORTEX")
                assert len(vortex) == 2 and not vortex & tippo
                await submit(c, project, "ручки и рюкзаки")
                r, constrained = await submit(c, project, "Оставь только Molti")
                assert constrained == bags and r["view"]["filters"]["brands"] == [
                    "molti"
                ]
                _, changed = await submit(c, project, "Теперь другой бренд — B1")
                assert changed == pens
                _, undone = await submit(c, project, "назад")
                assert undone == constrained
                await submit(c, project, "Бренд не важен")
                r, excluded = await submit(c, project, "Без Molti")
                assert not excluded & molti and excluded
                r, _ = await submit(c, project, "Рюкзаки только Molti")
                assert r["view"]["filters"]["branch_brands"] == {"backpack": ["molti"]}
                r, _ = await submit(c, project, "Теперь найди бутылки")
                assert (
                    not r["intent"]["brands"]
                    and not r["view"]["filters"]["excluded_brands"]
                )
                await submit(c, project, "ручки")
                r, models = await submit(c, project, "Модель Tippo")
                assert models == tippo
                reread = await call(c, "GET", f"/api/researches/{r['id']}")
                assert reread["view"] == r["view"]
                r, _ = await submit(c, project, "рюкзаки бренда Molti")
                r = await call(
                    c,
                    "PATCH",
                    f"/api/researches/{r['id']}/view",
                    json={"version": r["view"]["version"], "restore": "reset"},
                )
                assert {p["id"] for p in r["products"]} == bags and r["intent"][
                    "brands"
                ] == ["Molti"]
                _, all_bags = await submit(c, project, "Бренд не важен")
                assert len(all_bags) > len(bags)
                query_id = state["query_ids"]["Рюкзак Melango"]
                restored = await call(
                    c, "POST", f"/api/projects/{project}/queries/{query_id}/activate"
                )
                _, restored_ids = await record(
                    c, restored["research"], "History Melango"
                )
                assert restored_ids == melango
                r1, r2 = await asyncio.gather(
                    submit(c, project, "Ручка шариковая Tippo"),
                    submit(clients[1], state["projects"][1], "Molti"),
                )
                assert r1[1] == tippo and r2[1] == molti
                collection = await call(c, "GET", f"/api/projects/{project}/collection")
                assert collection["selected_ids"] == ["vto:1823109"]
                for path in [
                    f"/api/projects/{project}/queries",
                    f"/api/researches/{r1[0]['id']}",
                    f"/api/projects/{project}/collection",
                ]:
                    response = await clients[1].get(path)
                    assert response.status_code == 404, (path, response.status_code)
                state["checks"] = {
                    "full_id_equivalence": True,
                    "paging_over_100": True,
                    "brand_or": True,
                    "local_scope": True,
                    "unknown_explicit": True,
                    "model_family": True,
                    "followups": True,
                    "undo": True,
                    "history": True,
                    "reload": True,
                    "collection": True,
                    "concurrent_users": True,
                    "ownership": True,
                }
                async with sessions() as db:
                    state["catalog_after"] = await fingerprint(db)
                    state["documents_after"] = [
                        dict(r)
                        for r in (
                            await db.execute(
                                text(
                                    "SELECT id,md5(draft::text) fingerprint FROM presentation_documents ORDER BY id"
                                )
                            )
                        )
                        .mappings()
                        .all()
                    ]
                assert state["catalog_before"] == state["catalog_after"]
                assert state["documents_before"] == state["documents_after"]
                persist()
                print(
                    "Extended HTTP/SQL acceptance passed; catalog and document fingerprints unchanged"
                )
                return
            for query in QUERIES:
                started = time.perf_counter()
                response = await c.post(
                    f"/api/projects/{state['projects'][0]}/requests",
                    json={"query": query},
                )
                response.raise_for_status()
                body = response.json()
                research = body["research"]
                ids = [p["id"] for p in research["products"]]
                cursor = research["next_cursor"]
                while cursor:
                    page = await call(
                        c,
                        "GET",
                        f"/api/researches/{research['id']}/products",
                        params={"cursor": cursor, "limit": 100},
                    )
                    ids.extend(p["id"] for p in page["products"])
                    cursor = page["next_cursor"]
                item = {
                    "query": query,
                    "action": body["route"]["action"],
                    "intent": research["intent"],
                    "view": research["view"],
                    "base_total": research["base_total"],
                    "total": research["filtered_total"],
                    "offer_ids": ids,
                    "first_products": [
                        {k: p.get(k) for k in ("id", "name", "brand")}
                        for p in research["products"][:6]
                    ],
                    "seconds": round(time.perf_counter() - started, 4),
                    "headers": {
                        k: v for k, v in response.headers.items() if k.startswith("x-")
                    },
                    "research_id": research["id"],
                }
                state["evidence"].setdefault(args.phase, {})[query] = item
                persist()
                print(
                    query.encode("ascii", "backslashreplace").decode(),
                    item["action"],
                    item["total"],
                    item["seconds"],
                    flush=True,
                )
            async with sessions() as db:
                state["catalog_after"] = await fingerprint(db)
            persist()
    finally:
        for c in clients:
            await c.aclose()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
