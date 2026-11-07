"""Real HTTP against disposable projects in the populated database."""

import asyncio
import copy
import time
from uuid import uuid4

import httpx

from app.config import Settings
from app.database.connection import create_database
from app.database.models import PresentationDocument


async def run(state, root, ledger, public):
    from check_chat_workflow import call, save

    checks = []

    def check(label, condition):
        assert condition, label
        checks.append({"check": label, "pass": True})
        save(public / "http.json", {"checks": checks})

    clients = [
        httpx.AsyncClient(base_url="http://127.0.0.1:8000", cookies=u["cookies"], timeout=90)
        for u in state["users"]
    ]
    owner, other = clients
    guest = httpx.AsyncClient(base_url="http://127.0.0.1:8000", timeout=30)
    try:
        chat = await call(owner, "POST", "/api/chats", json={"request_id": str(uuid4())})
        project = next(p for p in await call(owner, "GET", "/api/projects") if p["chat_id"] == chat["id"])
        state["projects"].append(project["id"])
        state["http_project"] = project
        save(ledger, state)
        latencies = []
        for query in ["Синие ручки", "Рюкзаки"]:
            started = time.perf_counter()
            await call(
                owner,
                "POST",
                f"/api/projects/{project['id']}/requests",
                json={"query": query, "request_id": str(uuid4())},
            )
            latencies.append(round(time.perf_counter() - started, 3))
        messages = await call(owner, "GET", f"/api/chats/{chat['id']}/history")
        for identifier in ["vto:1823109", "vto:1934569"]:
            await call(
                owner,
                "POST",
                f"/api/projects/{project['id']}/collection/items",
                json={"offer_id": identifier},
            )
        collection = await call(owner, "GET", f"/api/projects/{project['id']}/collection")

        async def create():
            generation = await call(owner, "GET", f"/api/projects/{project['id']}/offer-state")
            doc = await call(
                owner,
                "POST",
                f"/api/projects/{project['id']}/offers",
                json={
                    "collection_version": collection["version"],
                    "offer_generation": generation["generation"],
                },
            )
            state["documents"].append(doc["id"])
            save(ledger, state)
            return doc

        doc = await create()
        path = "/api/offers/" + doc["id"]
        body = {"version": doc["version"], "confirmed": True}
        check(
            "Foreign owner and guest cannot delete",
            (await other.request("DELETE", path, json=body)).status_code == 404
            and (await guest.request("DELETE", path, json=body)).status_code == 401,
        )
        check(
            "Origin guard preserved",
            (
                await owner.request(
                    "DELETE", path, json=body, headers={"Origin": "https://untrusted.invalid"}
                )
            ).status_code
            == 403,
        )
        check(
            "Unconfirmed delete refused",
            (await owner.request("DELETE", path, json=body | {"confirmed": False})).status_code == 422,
        )
        await call(owner, "DELETE", path, json=body)
        check(
            "Draft removed from list and private editor",
            (await owner.get(path)).status_code == 404
            and not (await call(owner, "GET", "/api/offers"))["items"],
        )
        await call(owner, "DELETE", path, json=body)
        check("Delete retry is idempotent", True)
        check(
            "Stale get-or-create rejected",
            (
                await owner.post(
                    f"/api/projects/{project['id']}/offers",
                    json={"collection_version": collection["version"], "offer_generation": 0},
                )
            ).status_code
            == 409,
        )
        check(
            "Ordinary open did not create a replacement",
            (await call(owner, "GET", f"/api/projects/{project['id']}/offer")) is None,
        )
        published = await create()
        check("Explicit replacement gets a new ID", published["id"] != doc["id"])
        path = "/api/offers/" + published["id"]
        await call(
            owner,
            "POST",
            path + "/publish",
            json={"version": published["version"], "request_id": str(uuid4()), "share_epoch": 0},
        )
        for _ in range(300):
            published = await call(owner, "GET", path)
            if published["share"]["active"]:
                break
            assert not any(r["status"] == "FAILED" for r in published["revisions"]), "Render failed"
            await asyncio.sleep(0.1)
        assert published["share"]["active"]
        secret = published["share"]["local_preview_path"].rsplit("/", 1)[-1]
        value = await call(guest, "GET", "/api/public/offers/" + secret)
        old_urls = [
            "/api/public/offers/" + secret,
            value["pdf_url"],
            value["xlsx_url"],
            value["logo_url"],
            value["items"][0]["images"][0]["url"],
        ]
        check(
            "Real PDF and XLSX ready before deletion",
            (await guest.get(value["pdf_url"])).status_code == 200
            and (await guest.get(value["xlsx_url"])).status_code == 200,
        )
        # Previous and legacy fixtures belong to this new account and this project only.
        engine, sessions = create_database(Settings().database_url)
        try:
            async with sessions() as db, db.begin():
                source = await db.get(PresentationDocument, published["id"])
                previous = PresentationDocument(
                    project_id=project["id"],
                    user_id=state["users"][0]["id"],
                    draft=copy.deepcopy(source.draft),
                    is_primary_offer=False,
                )
                legacy = PresentationDocument(
                    project_id=project["id"],
                    user_id=state["users"][0]["id"],
                    draft={"settings": {"title": "Acceptance legacy fixture"}, "items": []},
                    is_primary_offer=False,
                )
                db.add_all([previous, legacy])
                await db.flush()
                state["documents"].extend([previous.id, legacy.id])
                state["legacy_document"] = legacy.id
                save(ledger, state)
            await call(owner, "DELETE", "/api/offers/" + previous.id, json={"version": 1, "confirmed": True})
            check(
                "Previous deletion preserves primary",
                (await call(owner, "GET", f"/api/projects/{project['id']}/offer"))["id"] == published["id"],
            )
            check(
                "Legacy archive deletion refused",
                (
                    await owner.request(
                        "DELETE", "/api/offers/" + legacy.id, json={"version": 1, "confirmed": True}
                    )
                ).status_code
                == 404,
            )
        finally:
            await engine.dispose()
        revision = published["share"]["published_revision_id"]
        private = [
            path + f"/revisions/{revision}/pdf",
            path + f"/revisions/{revision}/xlsx",
            path + f"/revisions/{revision}/assets/logo",
            f"/api/presentations/{published['id']}",
        ]
        await call(owner, "DELETE", path, json={"version": published["version"], "confirmed": True})
        check(
            "Every tested guest resource closed",
            all([(await guest.get(url)).status_code == 404 for url in old_urls]),
        )
        check(
            "Private files and compatibility routes closed",
            all([(await owner.get(url)).status_code == 404 for url in private]),
        )
        check(
            "Late publish rejected",
            (
                await owner.post(
                    path + "/publish",
                    json={"version": published["version"], "request_id": str(uuid4()), "share_epoch": 0},
                )
            ).status_code
            == 404,
        )
        check(
            "Chat messages and collection unchanged",
            (await call(owner, "GET", f"/api/chats/{chat['id']}/history")) == messages
            and (await call(owner, "GET", f"/api/projects/{project['id']}/collection")) == collection,
        )
        racing = await create()
        racing_path = "/api/offers/" + racing["id"]
        await call(
            owner,
            "POST",
            racing_path + "/publish",
            json={"version": racing["version"], "request_id": str(uuid4()), "share_epoch": 0},
        )
        observed = "QUEUED"
        for _ in range(100):
            current = await call(owner, "GET", racing_path)
            observed = current["revisions"][0]["status"]
            if observed != "QUEUED":
                break
            await asyncio.sleep(0.03)
        await call(owner, "DELETE", racing_path, json={"version": current["version"], "confirmed": True})
        await asyncio.sleep(1)
        check(
            "Publication cannot resurrect a deleted offer",
            (await owner.get(racing_path)).status_code == 404
            and (await call(owner, "GET", f"/api/projects/{project['id']}/offer")) is None,
        )
        browser_doc = await create()
        state["browser_document"] = browser_doc["id"]
        save(ledger, state)
        save(
            public / "http.json",
            {
                "status": "PASS",
                "checks": checks,
                "search_seconds": latencies,
                "observed_before_delete": observed,
                "collection_count": len(collection["selected_ids"]),
                "message_count": len(messages["messages"]),
                "expired_link_boundary": "controlled regression, not a live 14-day wait",
            },
        )
        print("HTTP acceptance", len(checks), "checks; delete during", observed)
    finally:
        for client in [*clients, guest]:
            await client.aclose()


async def race(state, ledger, public):
    from check_chat_workflow import call, save

    project = state["http_project"]["id"]
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:8000", cookies=state["users"][0]["cookies"], timeout=60
    ) as client:
        doc = await call(client, "GET", "/api/offers/" + state["browser_document"])
        assert doc["project_id"] == project and project in state["projects"]
        current = await call(client, "GET", f"/api/projects/{project}/offer-state")
        collection = await call(client, "GET", f"/api/projects/{project}/collection")
        stale = {"collection_version": collection["version"], "offer_generation": current["generation"]}
        deleted, created = await asyncio.gather(
            client.request(
                "DELETE", "/api/offers/" + doc["id"], json={"version": doc["version"], "confirmed": True}
            ),
            client.post(f"/api/projects/{project}/offers", json=stale),
        )
        assert deleted.status_code == 200 and created.status_code in (200, 409)
        if created.status_code == 200:
            assert created.json()["id"] == doc["id"]
        after = await call(client, "GET", f"/api/projects/{project}/offer-state")
        assert after["offer"] is None and after["generation"] == current["generation"] + 1
        fresh = await call(
            client,
            "POST",
            f"/api/projects/{project}/offers",
            json=stale | {"offer_generation": after["generation"]},
        )
        state["documents"].append(fresh["id"])
        state["browser_document"] = fresh["id"]
        save(ledger, state)
        assert (await client.post(f"/api/projects/{project}/offers", json=stale)).status_code == 409
        await call(
            client, "DELETE", "/api/offers/" + doc["id"], json={"version": doc["version"], "confirmed": True}
        )
        assert (await call(client, "GET", f"/api/projects/{project}/offer"))["id"] == fresh["id"]
        save(
            public / "delete-create-race.json",
            {
                "status": "PASS",
                "http_statuses": [deleted.status_code, created.status_code],
                "no_automatic_replacement": True,
                "stale_create_cannot_return_new_document": True,
                "repeated_old_delete_preserves_new_primary": True,
            },
        )
        print("PostgreSQL delete/create race passed", deleted.status_code, created.status_code)
