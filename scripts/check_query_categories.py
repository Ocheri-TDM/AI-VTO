"""Main API/PostgreSQL acceptance; deletes only recorded disposable resources."""

import argparse
import asyncio
import json
import secrets
import sys
from pathlib import Path
from uuid import uuid4

import httpx
from sqlalchemy import delete, select, text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))
from app.config import Settings
from app.database.connection import create_database
from app.database.models import ResearchRecord, User

LEDGER = ROOT / ".local/query-categories-acceptance.json"
QUERY = "ручки, бутылки, рюкзаки"


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=["prepare", "http", "restart", "cleanup"])
    phase = parser.parse_args().phase
    state = (
        json.loads(LEDGER.read_text("utf-8"))
        if LEDGER.exists()
        else {"run": uuid4().hex, "users": [], "projects": [], "evidence": {}}
    )

    def persist():
        LEDGER.write_text(json.dumps(state, ensure_ascii=False, indent=2), "utf-8")

    engine, sessions = create_database(Settings().database_url)

    async def data():
        async with sessions() as db:
            offers = dict(
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
            docs = [
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
            return {"catalog": offers, "documents": docs}

    async def call(c, method, path, **kw):
        r = await c.request(method, path, **kw)
        r.raise_for_status()
        return r.json() if r.status_code != 204 else None

    async def all_products(c, r):
        products = list(r["products"])
        cursor = r["next_cursor"]
        while cursor:
            page = await call(
                c,
                "GET",
                f"/api/researches/{r['id']}/products",
                params={"cursor": cursor, "limit": 100},
            )
            products += page["products"]
            cursor = page["next_cursor"]
        assert len(products) == len({p["id"] for p in products}) == r["matching_count"]
        return products

    async def record(c, label, r):
        products = await all_products(c, r)
        async with sessions() as db:
            stored = await db.scalar(
                select(ResearchRecord.payload).where(ResearchRecord.id == r["id"])
            )
        assert (
            stored["intent"] == r["intent"]
            and stored["view"]["hidden_branch_ids"] == r["view"]["hidden_branch_ids"]
        )
        state["evidence"][label] = {
            "research_id": r["id"],
            "query_categories": r.get("query_categories"),
            "intent": r["intent"],
            "view": r["view"],
            "revision": r["revision"],
            "state": r.get("result_state"),
            "base_total": r["base_total"],
            "eligible_total": r.get("eligible_total"),
            "visible_total": r["matching_count"],
            "offer_ids": [p["id"] for p in products],
            "title": r.get("effective_title"),
        }
        persist()
        return products

    clients = []
    try:
        if phase == "prepare":
            assert not state["users"], "Clean existing run first"
            state["before"] = await data()
            persist()
            for n in range(2):
                c = httpx.AsyncClient(base_url="http://127.0.0.1:8000", timeout=180)
                clients.append(c)
                u = await call(
                    c,
                    "POST",
                    "/api/auth/register",
                    json={
                        "name": f"categories-{state['run']}-{n}",
                        "password": secrets.token_urlsafe(24),
                    },
                )
                state["users"].append({"id": u["id"], "cookies": dict(c.cookies)})
                persist()
                chat = await call(
                    c,
                    "POST",
                    "/api/chats",
                    json={"project_name": f"Categories API {n} " + state["run"][:8]},
                )
                p = next(
                    p
                    for p in await call(c, "GET", "/api/projects")
                    if p["chat_id"] == chat["id"]
                )
                state["projects"].append(
                    {"id": p["id"], "chat_id": chat["id"], "user": n, "name": p["name"]}
                )
                persist()
            c = clients[0]
            chat = await call(
                c,
                "POST",
                "/api/chats",
                json={"project_name": "Categories Browser " + state["run"][:8]},
            )
            p = next(
                p
                for p in await call(c, "GET", "/api/projects")
                if p["chat_id"] == chat["id"]
            )
            state["browser_project"] = {
                "id": p["id"],
                "chat_id": chat["id"],
                "user": 0,
                "name": p["name"],
            }
            persist()
            r = (
                await call(
                    c,
                    "POST",
                    f"/api/projects/{state['projects'][0]['id']}/requests",
                    json={"query": QUERY},
                )
            )["research"]
            state["baseline"] = {
                "categories": [b["category"] for b in r["intent"]["branches"]],
                "offer_ids": [p["id"] for p in await all_products(c, r)],
                "count": r["matching_count"],
            }
            persist()
            print(
                "Prepared two isolated users/three projects; baseline count",
                r["matching_count"],
            )
            return
        clients = [
            httpx.AsyncClient(
                base_url="http://127.0.0.1:8000", timeout=180, cookies=u["cookies"]
            )
            for u in state["users"]
        ]
        c = clients[0]
        project = state["projects"][0]["id"]
        if phase == "cleanup":
            for p in (
                state["projects"]
                + [state["browser_project"]]
                + state.get("extra_projects", [])
            ):
                response = await clients[p["user"]].delete("/api/projects/" + p["id"])
                assert response.status_code in (204, 404)
            async with sessions.begin() as db:
                await db.execute(
                    delete(User).where(User.id.in_([u["id"] for u in state["users"]]))
                )
            assert await data() == state["before"]
            LEDGER.unlink()
            print(
                "Only recorded disposable resources removed; catalog and documents unchanged"
            )
            return

        async def submit(q):
            return (
                await call(
                    c, "POST", f"/api/projects/{project}/requests", json={"query": q}
                )
            )["research"]

        async def change(r, actions=None, **kw):
            body = {"version": r["view"]["version"], **kw}
            if actions is not None:
                body["branch_actions"] = actions
            return await call(c, "PATCH", f"/api/researches/{r['id']}/view", json=body)

        if phase == "restart":
            expected = state["restart_expected"]
            r = await call(c, "GET", "/api/researches/" + expected["id"])
            assert (
                await call(
                    c, "GET", "/api/researches/chat/" + state["projects"][0]["chat_id"]
                )
            )["id"] == expected["id"]
            assert r["intent"] == expected["intent"] and r["view"] == expected["view"]
            assert (await call(c, "GET", f"/api/projects/{project}/collection"))[
                "selected_ids"
            ] == state["selected_ids"]
            await record(c, "API restart", r)
            state["restart_passed"] = True
            persist()
            print("API restart restored branches, filters, hidden state and collection")
            return
        r = await submit(QUERY)
        products = await record(c, "01 initial", r)
        assert [b["concept"] for b in r["query_categories"]] == [
            "pen",
            "bottle",
            "backpack",
        ]
        assert set(state["baseline"]["offer_ids"]) == {p["id"] for p in products}
        selected = [
            next(p["id"] for p in products if concept in p["matched_categories"])
            for concept in ["pen", "bottle", "backpack"]
        ]
        for id in selected:
            await call(
                c,
                "POST",
                f"/api/projects/{project}/collection/items",
                json={"offer_id": id},
            )
        state["selected_ids"] = selected
        persist()
        original = r["query_categories"]
        bottle = original[1]
        bag = original[2]
        cursor = r["next_cursor"]
        r = await change(r, [{"action": "hide", "branch_id": bottle["id"]}])
        actual = await record(c, "04 hide bottles", r)
        expected = {
            p["id"]
            for p in products
            if any(cat != "bottle" for cat in p["matched_categories"])
        }
        assert {p["id"] for p in actual} == expected
        assert (
            r["query_categories"][1]["count"] == bottle["count"]
            and r["hidden_selected_count"] == 1
        )
        stale = await c.get(
            f"/api/researches/{r['id']}/products", params={"cursor": cursor}
        )
        assert stale.status_code == 409
        r = await change(r, [{"action": "show", "branch_id": bottle["id"]}])
        await record(c, "05 show bottles", r)
        assert r["query_categories"] == original
        r = await change(r, [{"action": "remove", "branch_id": bag["id"]}])
        await record(c, "06 remove backpacks", r)
        assert [b["concept"] for b in r["query_categories"]] == ["pen", "bottle"] and r[
            "hidden_selected_count"
        ] == 1
        assert (
            "Рюкзак" not in r["effective_title"] and r["intent"]["raw_query"] == QUERY
        )
        r = await change(
            r, [{"action": "restore", "removal_id": r["category_notice"]["id"]}]
        )
        await record(c, "07 contextual restore", r)
        assert r["query_categories"] == original
        r = await change(r, filters=r["view"]["filters"] | {"colors": ["BLACK"]})
        await record(c, "08 color filter", r)
        filtered = r["matching_count"]
        assert filtered > 0
        r = await change(
            r, [{"action": "hide", "branch_id": b["id"]} for b in r["query_categories"]]
        )
        await record(c, "09 all hidden", r)
        assert (
            r["result_state"] == "ALL_CATEGORIES_HIDDEN"
            and not r["products"]
            and r["eligible_total"] == filtered
        )
        r = await change(r, [{"action": "show_all"}])
        await record(c, "10 show all", r)
        assert (
            r["view"]["filters"]["colors"] == ["BLACK"]
            and r["matching_count"] == filtered
        )
        r = await change(r, [{"action": "hide", "branch_id": bottle["id"]}])
        reread = await call(c, "GET", f"/api/researches/{r['id']}")
        assert reread["view"] == r["view"]
        await record(c, "11 reload hidden and filter", r)
        state["restart_expected"] = {k: r[k] for k in ["id", "intent", "view"]}
        persist()
        new = await submit("зонты")
        await record(c, "12 new umbrella search", new)
        assert (
            len(new["query_categories"]) == 1
            and new["query_categories"][0]["concept"] == "umbrella"
            and not new["view"]["hidden_branch_ids"]
        )
        assert (await call(c, "GET", f"/api/projects/{project}/collection"))[
            "selected_ids"
        ] == selected
        for query, label in [
            ("Ручка шариковая Tippo", "model Tippo"),
            ("Molti", "brand only"),
            ("ручки, НеопознанныйТовар729", "unresolved position"),
            ("носки", "known empty category"),
            (
                "чёрные ручки модели Tippo и синие ручки модели VORTEX",
                "same concept different models",
            ),
        ]:
            r = await submit(query)
            await record(c, label, r)
            if label == "model Tippo":
                assert (
                    len(r["query_categories"]) == 1
                    and r["intent"]["models"] == ["Tippo"]
                    and r["matching_count"] == 6
                )
            if label == "brand only":
                assert r["categories_unrestricted"] and not r["query_categories"]
            if label == "unresolved position":
                assert any(not b["resolved"] for b in r["query_categories"])
            if label == "same concept different models":
                assert len(r["query_categories"]) == 2
        local = await submit(
            "Чёрные ручки Molti, красные бутылки и синие рюкзаки от 300 шт."
        )
        assert not local["intent"]["brands"] and local["intent"]["branches"][0][
            "brands"
        ] == ["Molti"]
        before = local["intent"]
        local = await change(
            local, [{"action": "hide", "branch_id": local["query_categories"][1]["id"]}]
        )
        assert local["intent"] == before
        await record(c, "local constraints preserved", local)
        r = await submit("Ручка шариковая Tippo")
        r = await change(
            r, [{"action": "remove", "branch_id": r["query_categories"][0]["id"]}]
        )
        await record(c, "last category removed", r)
        assert (
            r["result_state"] == "NO_QUERY_CATEGORIES"
            and not r["products"]
            and not r["categories_unrestricted"]
        )
        r = await submit(QUERY)
        url = f"/api/researches/{r['id']}/view"
        body = lambda n: {
            "version": r["view"]["version"],
            "branch_actions": [
                {"action": "hide", "branch_id": r["query_categories"][n]["id"]}
            ],
        }
        concurrent = await asyncio.gather(
            c.patch(url, json=body(0)), c.patch(url, json=body(1))
        )
        assert sorted(v.status_code for v in concurrent) == [200, 409]
        assert (await clients[1].patch(url, json=body(2))).status_code == 404
        assert (
            await clients[1].get(f"/api/projects/{project}/collection")
        ).status_code == 404
        history = await call(c, "GET", f"/api/projects/{project}/queries")
        query_id = next(
            q["id"]
            for q in history
            if q["research_session_id"] == state["restart_expected"]["id"]
        )
        await call(c, "POST", f"/api/projects/{project}/queries/{query_id}/activate")
        state["restart_query_id"] = query_id
        assert await data() == state["before"]
        state["http_passed"] = True
        persist()
        print(
            "HTTP chain, all IDs, cursor, persisted state, concurrent versions and ownership passed"
        )
    finally:
        for c in clients:
            await c.aclose()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
