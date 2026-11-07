"""Real local HTTP: persistent turns, collections, owner list and public exports."""

import asyncio
import copy
import json
from datetime import datetime, timedelta
from uuid import uuid4

import httpx


async def run(state, root, ledger, public):
    from check_chat_workflow import call, save

    checks = []

    async def check(label, condition):
        assert condition, label
        checks.append({"check": label, "pass": True})
        save(public / "http.json", {"checks": checks})

    clients = [
        httpx.AsyncClient(base_url="http://127.0.0.1:8000", cookies=u["cookies"], timeout=120)
        for u in state["users"]
    ]
    owner, other = clients

    async def new(key=None):
        result = await call(owner, "POST", "/api/chats", json={"request_id": key or str(uuid4())})
        p = next(p for p in await call(owner, "GET", "/api/projects") if p["chat_id"] == result["id"])
        if p["id"] not in state["projects"]:
            state["projects"].append(p["id"])
            save(ledger, state)
        return p

    try:
        key = str(uuid4())
        a, b = await asyncio.gather(new(key), new(key))
        await check("Idempotent one-click chat creation", a["id"] == b["id"] and a["name"] == "Новый чат")
        p = a
        state["conversation_project"] = p
        save(ledger, state)
        await check(
            "No proposal created with empty chat",
            (await call(owner, "GET", f"/api/projects/{p['id']}/offer")) is None,
        )
        await check(
            "New chat has own empty collection",
            not (await call(owner, "GET", f"/api/projects/{p['id']}/collection"))["selected_ids"],
        )
        messages = [
            "Синие бутылки, 300 шт.",
            "Ручки и блокноты",
            "Оставь только красные",
            "Найди рюкзаки",
            "Оставь только синие",
        ]
        snapshots = []
        first_key = str(uuid4())
        for n, message in enumerate(messages):
            result = await call(
                owner,
                "POST",
                f"/api/projects/{p['id']}/requests",
                json={"query": message, "request_id": first_key if n == 0 else str(uuid4())},
            )
            snapshots.append(result)
        history = await call(owner, "GET", f"/api/chats/{p['chat_id']}/history")
        await check(
            "Three searches and two refinements preserve all ten messages",
            len(history["messages"]) == 10
            and [m["content"] for m in history["messages"] if m["role"] == "user"] == messages,
        )
        project = next(v for v in await call(owner, "GET", "/api/projects") if v["id"] == p["id"])
        await check(
            "First title stays independent of current grid",
            project["name"] == "Бутылки" and project["name_source"] == "auto",
        )
        await check(
            "Standalone search does not inherit old quantity",
            snapshots[3]["research"]["intent"]["quantity"] is None,
        )
        replay = await call(
            owner,
            "POST",
            f"/api/projects/{p['id']}/requests",
            json={"query": messages[0], "request_id": first_key},
        )
        await check(
            "Network retry reuses turn ID",
            replay["turn"]["id"] == snapshots[0]["turn"]["id"] and replay["active"] is False,
        )
        await check(
            "No duplicate messages after retry",
            len((await call(owner, "GET", f"/api/chats/{p['chat_id']}/history"))["messages"]) == 10,
        )
        queries = await call(owner, "GET", f"/api/projects/{p['id']}/queries")
        await call(owner, "POST", f"/api/projects/{p['id']}/queries/{queries[-1]['id']}/activate", json={})
        await check(
            "History activation preserves later conversation",
            (await call(owner, "GET", f"/api/chats/{p['chat_id']}/history"))["messages"]
            == history["messages"],
        )
        await call(owner, "POST", f"/api/projects/{p['id']}/queries/{queries[0]['id']}/activate", json={})
        await check(
            "Other user cannot read chat history",
            (await other.get(f"/api/chats/{p['chat_id']}/history")).status_code == 404,
        )
        for identifier in ["vto:1823109", "vto:1883738", "vto:333294"]:
            await call(
                owner, "POST", f"/api/projects/{p['id']}/collection/items", json={"offer_id": identifier}
            )
        selected = await call(owner, "GET", f"/api/projects/{p['id']}/collection")
        doc = await call(
            owner, "POST", f"/api/projects/{p['id']}/offers", json={"collection_version": selected["version"]}
        )
        state.setdefault("documents", []).append(doc["id"])
        state["document"] = doc["id"]
        save(ledger, state)
        current_stock = {
            item["offer_id"]: doc["owner_stock"][item["id"]]["quantity"] for item in doc["draft"]["items"]
        }
        await check(
            "Owner sees positive, zero and unknown numeric quantities",
            current_stock["vto:1823109"] > 0
            and current_stock["vto:1883738"] == 0
            and current_stock["vto:333294"] is None,
        )
        body = {
            "version": doc["version"],
            "title": doc["draft"]["settings"]["title"],
            "company": "Локальная приёмка",
            "use_logo": True,
            "use_banner": False,
            "items": [
                {k: i[k] for k in ["id", "title", "sku", "sku_note", "unit_price", "quantity", "main_image"]}
                for i in doc["draft"]["items"]
            ],
        }
        body["items"][0].update(unit_price="35224.51", quantity=77)
        doc = await call(owner, "PATCH", "/api/offers/" + doc["id"], json=body)
        saved = copy.deepcopy(doc["draft"])
        await call(
            owner,
            "PATCH",
            "/api/projects/" + p["id"],
            json={"name": "Переписка менеджера", "client_name": None},
        )
        again = await call(owner, "POST", f"/api/projects/{p['id']}/offers", json={"collection_version": 0})
        await check(
            "Existing primary editor reused with manual values",
            again["id"] == doc["id"] and again["draft"] == saved,
        )
        second = await new()
        state["second_project"] = second
        save(ledger, state)
        await check(
            "Separate chats have separate collections",
            not (await call(owner, "GET", f"/api/projects/{second['id']}/collection"))["selected_ids"],
        )
        await call(
            owner,
            "POST",
            f"/api/projects/{second['id']}/requests",
            json={"query": "Красные ручки от 200 шт.", "request_id": str(uuid4())},
        )
        second = next(v for v in await call(owner, "GET", "/api/projects") if v["id"] == second["id"])
        state["second_project"] = second
        save(ledger, state)
        await check("Automatic title without separate model call", second["name"] == "Ручки")
        await call(
            owner, "POST", f"/api/projects/{second['id']}/collection/items", json={"offer_id": "vto:1934569"}
        )
        collection = await call(owner, "GET", f"/api/projects/{second['id']}/collection")
        doc2 = await call(
            owner,
            "POST",
            f"/api/projects/{second['id']}/offers",
            json={"collection_version": collection["version"]},
        )
        state["documents"].append(doc2["id"])
        save(ledger, state)
        page = await call(owner, "GET", "/api/offers", params={"limit": 1})
        items = list(page["items"])
        while page["next_cursor"]:
            page = await call(owner, "GET", "/api/offers", params={"limit": 1, "cursor": page["next_cursor"]})
            items.extend(page["items"])
        await check(
            "Navbar list includes documents from both chats exactly once",
            {i["id"] for i in items} == set(state["documents"]) and len(items) == len(state["documents"]),
        )
        await check(
            "List DTO contains no share keys or stock details",
            all(
                k not in json.dumps(items)
                for k in ["public_url", "encrypted_token", "owner_stock", "storage_key", "stock_quantity"]
            ),
        )
        await check("Foreign offer list empty", not (await call(other, "GET", "/api/offers"))["items"])
        await check(
            "Foreign owner stock blocked", (await other.get("/api/offers/" + doc["id"])).status_code == 404
        )
        action = {"version": doc["version"], "request_id": str(uuid4()), "share_epoch": doc["share"]["epoch"]}
        queued = await call(owner, "POST", "/api/offers/" + doc["id"] + "/publish", json=action)
        await check("Queue does not start link lifetime", queued["share"]["activated_at"] is None)
        for _ in range(180):
            latest = await call(owner, "GET", "/api/offers/" + doc["id"])
            if latest["share"]["active"]:
                break
            assert not any(rev["status"] == "FAILED" for rev in latest["revisions"]), (
                "Real export failed; inspect private service log"
            )
            await asyncio.sleep(1)
        else:
            raise RuntimeError("Real publication timeout")
        await check(
            "Fourteen days start at READY activation",
            datetime.fromisoformat(latest["share"]["expires_at"])
            - datetime.fromisoformat(latest["share"]["activated_at"])
            == timedelta(days=14),
        )
        state["published"] = {
            "path": latest["share"]["local_preview_path"],
            "expires_at": latest["share"]["expires_at"],
            "revision": latest["share"]["published_revision_id"],
        }
        save(ledger, state)
        async with httpx.AsyncClient(base_url="http://127.0.0.1:8000", timeout=30) as guest:
            key = latest["share"]["local_preview_path"].split("/")[-1]
            public_response = await guest.get("/api/public/offers/" + key)
            await check(
                "Anonymous client DTO contains no numeric inventory",
                public_response.status_code == 200
                and all(
                    k not in public_response.text
                    for k in [
                        "owner_stock",
                        "stock_quantity",
                        "snapshot_quantity",
                        "incoming_quantity",
                        "availability_evidence",
                    ]
                ),
            )
            client = public_response.json()
            for kind in ["pdf", "xlsx"]:
                response = await guest.get(client[kind + "_url"])
                await check("Public " + kind + " download is ready", response.status_code == 200)
                import hashlib

                state.setdefault("export_hashes", {})[kind] = hashlib.sha256(response.content).hexdigest()
        save(ledger, state)
        final = (await call(owner, "GET", "/api/offers/" + doc["id"]))["draft"]
        fields = [
            "id",
            "title",
            "sku",
            "sku_note",
            "unit_price",
            "quantity",
            "main_image",
            "price_source",
            "quantity_source",
        ]
        await check(
            "Read and publish preserve all manual commercial fields",
            [{k: i.get(k) for k in fields} for i in final["items"]]
            == [{k: i.get(k) for k in fields} for i in saved["items"]]
            and final["settings"] == saved["settings"]
            and final["excluded_offer_ids"] == saved["excluded_offer_ids"],
        )
        save(
            public / "http.json",
            {
                "status": "PASS",
                "checks": checks,
                "owner_quantities": current_stock,
                "history_count": len(history["messages"]),
                "documents": len(items),
                "lifetime_days": 14,
                "external_deployment": "NOT TESTED",
            },
        )
        print("HTTP acceptance", len(checks), "checks; real ready PDF/XLSX")
    finally:
        for client in clients:
            await client.aclose()
