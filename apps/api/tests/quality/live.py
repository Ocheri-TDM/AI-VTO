"""Normal bounded live acceptance only: no faults/fuzz/load on the main DB."""

import time
import unicodedata
from uuid import uuid4

import httpx
from sqlalchemy import text

from app.config import Settings
from app.database.connection import create_database


async def run(state, record):
    settings = Settings()
    assert ":55432/" in settings.database_url
    engine, sessions = create_database(settings.database_url)
    async with sessions() as db:
        facts = [
            dict(r)
            for r in (
                await db.execute(
                    text(
                        "SELECT o.id,o.payload,o.normalized_colors,p.stock,p.price_kzt FROM supplier_offers o JOIN product_observations p ON p.offer_id=o.id WHERE o.lifecycle<>'REMOVED'"
                    )
                )
            ).mappings()
        ]
        snapshots_before = [
            dict(r)
            for r in (
                await db.execute(
                    text("SELECT id,md5(draft::text) fingerprint FROM presentation_documents ORDER BY id")
                )
            ).mappings()
        ]

    # Human-labelled families. Parent-name equality / an explicitly annotated
    # title prefix identifies these 12 offers independently of search logic.
    def parent(p):
        return p["payload"].get("metadata", {}).get("parent_name") or p["payload"]["name"]

    labelled = {
        "Tippo": [p for p in facts if parent(p) == "Ручка шариковая Tippo"],
        "Melango": [p for p in facts if parent(p) == "Рюкзак-мешок Melango"],
        "VORTEX": [p for p in facts if parent(p).startswith("Ручка шариковая VORTEX,")],
    }
    assert len(labelled["Tippo"]) == 6 and len(labelled["Melango"]) == 4 and len(labelled["VORTEX"]) == 2
    clients = [httpx.AsyncClient(base_url="http://127.0.0.1:8000", timeout=50) for _ in range(3)]
    users = state.setdefault("live_users", [])
    projects = []
    results = []
    try:
        for who, c in enumerate(clients):
            if who < len(users) and users[who].get("project"):
                c.cookies.update(users[who]["cookies"])
                projects.append(users[who]["project"])
                continue
            name = "quality-live-" + uuid4().hex[:12]
            response = await c.post(
                "/api/auth/register", json={"name": name, "password": uuid4().hex + "Aa1!"}
            )
            response.raise_for_status()
            user = {"id": response.json()["id"], "cookies": dict(c.cookies), "name": name}
            users.append(user)
            record(state)
            (await c.post("/api/chats", json={"project_name": name})).raise_for_status()
            p = (await c.get("/api/projects")).json()[0]
            user["project"] = p
            projects.append(p)
            record(state)

        async def query(c, p, text):
            if state.get("live_queries_used", 0) >= 60:
                raise RuntimeError("Live normal-query budget exhausted")
            state["live_queries_used"] = state.get("live_queries_used", 0) + 1
            record(state)
            response = await c.post(f"/api/projects/{p['id']}/requests", json={"query": text})
            assert response.status_code == 202, response.text[:600]
            value = response.json()
            assert value["mode"] == "GRID"
            return value["research"]

        c, p = clients[0], projects[0]
        r = await query(c, p, "Ручки, бутылки, рюкзаки")
        assert [v["concept"] for v in r["query_categories"]] == ["pen", "bottle", "backpack"]
        selected = []
        page = r
        for _ in range(3):
            assert page["products"]
            offer = page["products"][0]["id"]
            assert offer not in selected
            selected.append(offer)
            response = await c.post(
                f"/api/projects/{p['id']}/collection/items",
                json={"offer_id": offer, "source_research_id": r["id"]},
            )
            response.raise_for_status()
            if page["next_cursor"]:
                page = (
                    await c.get(f"/api/researches/{r['id']}/products", params={"cursor": page["next_cursor"]})
                ).json()
        col = (await c.get(f"/api/projects/{p['id']}/collection")).json()
        assert col["selected_ids"] == selected
        document = await c.post(
            f"/api/projects/{p['id']}/presentations", json={"collection_version": col["version"]}
        )
        document.raise_for_status()
        doc = document.json()
        users[0]["document_id"] = doc["id"]
        record(state)
        snapshot = doc["draft"]["items"]
        for message in ("Красные", "Оставь только Molti", "Бренд не важен"):
            r = await query(c, p, message)
            assert len(r["query_categories"]) == 3
            assert set(r["view"]["selected_ids"]) == set(selected)
        assert not r["view"]["filters"]["brands"]
        union_query_id = next(
            q["id"] for q in (await c.get(f"/api/projects/{p['id']}/queries")).json() if q["is_active"]
        )
        catalogue = [
            ("Ручка шариковая Tippo", "Tippo"),
            ("Tippo", "Tippo"),
            ("Рюкзак-мешок Melango", "Melango"),
            ("VORTEX", "VORTEX"),
            ("Синие рюкзаки модели Melango от 300 шт", "blue-melango"),
            ("повербанк", "powerbank"),
            ("внешний аккумулятор", "powerbank"),
            ("Power Bank", "powerbank"),
            ("флешка", "flashdrive"),
            ("USB flash drive", "flashdrive"),
            ("термокружка", "thermomug"),
            ("термостакан", "thermocup"),
            ("бренд Molti", "Molti"),
            ("Для записи и напитков, всё чёрное, от 100 шт", "purpose"),
        ]
        equivalences = {}
        remaining = 60 - state.get("live_queries_used", 0)
        for n in range(remaining):
            who = n % 3
            client, project = clients[who], projects[who]
            text_query, label = catalogue[n % len(catalogue)]
            before = time.perf_counter()
            value = await query(client, project, text_query)
            actual = {v["id"] for v in value["products"]}
            while value["next_cursor"]:
                response = await client.get(
                    f"/api/researches/{value['id']}/products", params={"cursor": value["next_cursor"]}
                )
                response.raise_for_status()
                value = response.json()
                actual.update(v["id"] for v in value["products"])
            state["live_last_result"] = {
                "query": text_query,
                "actual_ids": sorted(actual),
                "intent": value["intent"],
            }
            record(state)
            if label in labelled:
                assert actual == {v["id"] for v in labelled[label]}, (
                    n,
                    text_query,
                    "labelled family membership",
                )
            elif label == "blue-melango":
                assert actual == {
                    v["id"]
                    for v in labelled["Melango"]
                    if "BLUE" in v["normalized_colors"] and v["stock"] is not None and v["stock"] >= 300
                }
            elif label == "Molti":
                assert actual == {
                    v["id"]
                    for v in facts
                    if unicodedata.normalize("NFKC", v["payload"].get("brand") or "").strip().casefold()
                    == "molti"
                }
            elif label in ("powerbank", "flashdrive"):
                assert value["intent"]["categories"] == [label]
                if label in equivalences:
                    assert actual == equivalences[label], (n, "multilingual pool mismatch")
                equivalences[label] = actual
            elif label in ("thermomug", "thermocup"):
                assert value["intent"]["categories"] == [label]
            elif label == "purpose":
                assert len(actual) < len(facts)
                for fact in facts:
                    if fact["id"] in actual:
                        assert (
                            "BLACK" in fact["normalized_colors"]
                            and fact["stock"] is not None
                            and fact["stock"] >= 100
                        )
            collection = (await client.get(f"/api/projects/{project['id']}/collection")).json()
            assert set(collection["selected_ids"]) == (set(selected) if who == 0 else set())
            results.append(
                {
                    "query": text_query,
                    "count": len(actual),
                    "seconds": time.perf_counter() - before,
                    "offer_ids": sorted(actual),
                    "pass": True,
                }
            )
        activated = await c.post(f"/api/projects/{p['id']}/queries/{union_query_id}/activate")
        assert activated.status_code == 200
        restored = activated.json()["research"]
        assert len(restored["query_categories"]) == 3
        assert set(restored["view"]["selected_ids"]) == set(selected)
        document = (await c.get(f"/api/presentations/{doc['id']}")).json()
        assert document["draft"]["items"] == snapshot
        assert (await clients[1].get(f"/api/presentations/{doc['id']}")).status_code == 404
        assert (await clients[1].get(f"/api/projects/{p['id']}/collection")).status_code == 404
        async with sessions() as db:
            snapshots_after = {
                r["id"]: r["fingerprint"]
                for r in (
                    await db.execute(
                        text("SELECT id,md5(draft::text) fingerprint FROM presentation_documents")
                    )
                ).mappings()
            }
        assert all(snapshots_after.get(r["id"]) == r["fingerprint"] for r in snapshots_before)
        return {
            "status": "PASS",
            "normal_search_submissions": state.get("live_queries_used"),
            "post_fix_normal_submissions": remaining + 4,
            "results": results,
            "labelled_families": {
                k: [{"id": p["id"], "title": parent(p)} for p in v] for k, v in labelled.items()
            },
            "preexisting_documents_unchanged": len(snapshots_before),
            "collection_chain": "PASS",
            "history": "PASS",
            "ownership": "PASS",
        }
    finally:
        for c in clients:
            await c.aclose()
        await engine.dispose()
