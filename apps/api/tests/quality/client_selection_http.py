"""Opt-in populated PostgreSQL/HTTP checks. Never resets or fabricates catalog data."""

import asyncio
import hashlib
import secrets
import time
from uuid import uuid4

import httpx
from check_chat_workflow import call, save

LOCAL = "http://127.0.0.1:8000"
PUBLIC = "https://souvenir-studio-three.vercel.app"


def headers(origin=PUBLIC):
    return {"Origin": origin, "X-Offer-Selection": "1"}


async def choose(client, offer, state, item, selected=True, mutation=None):
    return await client.put(
        offer["path"] + "/selection",
        headers=headers() | {"X-Offer-CSRF": state["csrf_token"]},
        json={
            "item_id": item,
            "selected": selected,
            "mutation_id": mutation or str(uuid4()),
            "published_revision_id": state["published_revision_id"],
            "version": state["version"],
        },
    )


async def setup(state, ledger):
    from check_commercial_offers import ready

    if len(state["users"]) == 1:
        async with httpx.AsyncClient(base_url=LOCAL, timeout=60) as client:
            name = "clean-start-" + state["run"] + "-second"
            user = await call(
                client,
                "POST",
                "/api/auth/register",
                json={"name": name, "password": secrets.token_urlsafe(24)},
            )
            state["users"].append({"id": user["id"], "name": name, "cookies": dict(client.cookies)})
            save(ledger, state)
    state.setdefault("offers", [])
    for owner_index in [0, 0, 1][len(state["offers"]) :]:
        async with httpx.AsyncClient(
            base_url=LOCAL, timeout=180, cookies=state["users"][owner_index]["cookies"]
        ) as client:
            chat = await call(
                client, "POST", "/api/chats", json={"project_name": "Client interest " + state["run"]}
            )
            projects = await call(client, "GET", "/api/projects")
            project = next(p for p in projects if p["chat_id"] == chat["id"])["id"]
            state["projects"].append(project)
            save(ledger, state)
            for offer_id in ["vto:1823109", "vto:1823108", "vto:1823107"]:
                collection = await call(
                    client, "POST", f"/api/projects/{project}/collection/items", json={"offer_id": offer_id}
                )
            document = await call(
                client,
                "POST",
                f"/api/projects/{project}/offers",
                json={"collection_version": collection["version"]},
            )
            state["documents"].append(document["id"])
            save(ledger, state)
            path = "/api/offers/" + document["id"]
            await call(
                client,
                "POST",
                path + "/publish",
                json={
                    "version": document["version"],
                    "request_id": str(uuid4()),
                    "share_epoch": document["share"]["epoch"],
                },
            )
            document, revision = await ready(client, path, document["version"])
            token = document["share"]["local_preview_path"].split("/")[-1]
            state["offers"].append(
                {
                    "document": document["id"],
                    "owner": owner_index,
                    "project": project,
                    "token": token,
                    "path": "/api/public/offers/" + token,
                    "revision": revision["id"],
                    "items": [i["id"] for i in document["draft"]["items"]],
                    "initial_version": document["version"],
                    "expires_at": document["share"]["expires_at"],
                }
            )
            save(ledger, state)
    print("Three real three-variant offers prepared; two isolated managers")


async def http(state, ledger, public):
    evidence = {"path": PUBLIC, "checks": [], "transport": {}}

    def check(name, condition):
        assert condition, name
        evidence["checks"].append({"check": name, "status": "PASS"})

    offer = state["offers"][0]
    async with (
        httpx.AsyncClient(base_url=PUBLIC, cookies=state["users"][0]["cookies"], timeout=60) as owner,
        httpx.AsyncClient(base_url=PUBLIC, cookies=state["users"][1]["cookies"], timeout=60) as other,
        httpx.AsyncClient(base_url=PUBLIC, timeout=60) as guest,
        httpx.AsyncClient(base_url=PUBLIC, timeout=60) as second,
    ):
        check("real HTTPS proxy health", (await owner.get("/api/health")).status_code == 200)
        before = await call(owner, "GET", "/api/notifications")
        document_before = await call(owner, "GET", "/api/offers/" + offer["document"])
        summary_before = (await call(owner, "GET", "/api/offers/" + offer["document"] + "/client-selection"))[
            "summary"
        ]
        view = await call(guest, "GET", offer["path"])
        untouched = await call(guest, "GET", offer["path"] + "/selection")
        check(
            "opening and preview create no visitor/notification",
            not untouched["initialized"]
            and (await call(owner, "GET", "/api/notifications"))["cursor"] == before["cursor"],
        )
        hashes = {}
        for kind in ("pdf", "xlsx"):
            response = await guest.get(view[kind + "_url"])
            check("published " + kind + " download", response.status_code == 200)
            hashes[kind] = hashlib.sha256(response.content).hexdigest()
        initial = await guest.post(
            offer["path"] + "/selection/session",
            headers=headers(),
            json={"published_revision_id": offer["revision"]},
        )
        check(
            "HTTPS cookie HttpOnly/Secure/scoped",
            initial.status_code == 200
            and all(
                v in initial.headers["set-cookie"]
                for v in ["HttpOnly", "Secure", "SameSite=lax", "Path=" + offer["path"] + "/selection"]
            ),
        )
        own = initial.json()
        initial2 = await call(
            second,
            "POST",
            offer["path"] + "/selection/session",
            headers=headers(),
            json={"published_revision_id": offer["revision"]},
        )
        mutation = str(uuid4())
        first = await choose(guest, offer, own, offer["items"][0], mutation=mutation)
        check("guest desired-state write", first.status_code == 200 and first.json()["changed"])
        replay = await choose(guest, offer, own, offer["items"][0], mutation=mutation)
        check("mutation receipt replays once", replay.status_code == 200 and replay.json()["replayed"])
        check(
            "independent visitor reads only own marks",
            (await call(second, "GET", offer["path"] + "/selection"))["selected_items"] == [],
        )
        own = first.json()
        for item in offer["items"][1:]:
            response = await choose(guest, offer, own, item)
            check("add desired position", response.status_code == 200)
            own = response.json()
        check(
            "three choices, one unread group",
            len((await call(owner, "GET", "/api/notifications"))["items"]) == len(before["items"]) + 1
            and len(own["selected_items"]) == 3,
        )
        check(
            "foreign owner cannot read visitor lists",
            (await other.get("/api/offers/" + offer["document"] + "/client-selection")).status_code == 404,
        )
        check(
            "guest cannot edit price/document",
            (await guest.patch("/api/offers/" + offer["document"], json={"unit_price": "1"})).status_code
            == 401,
        )
        check("unpublished item rejected", (await choose(guest, offer, own, str(uuid4()))).status_code == 404)
        bad = await guest.put(
            offer["path"] + "/selection",
            headers={"Origin": "https://evil.example", "X-Offer-Selection": "1"},
            json={
                "item_id": offer["items"][0],
                "selected": False,
                "mutation_id": str(uuid4()),
                "published_revision_id": offer["revision"],
                "version": own["version"],
            },
        )
        check("foreign Origin rejected", bad.status_code == 403)
        await choose(second, offer, initial2, offer["items"][0])
        groups = await call(owner, "GET", "/api/notifications")
        check(
            "two visitors produce two groups",
            groups["unread_count"] == before["unread_count"] + 2
            and len(groups["items"]) == len(before["items"]) + 2,
        )
        feedback = await call(owner, "GET", "/api/offers/" + offer["document"] + "/client-selection")
        check(
            "visitor/position/mark counts separated",
            feedback["summary"]["visitor_count"] == summary_before["visitor_count"] + 2
            and feedback["summary"]["item_count"] == 3
            and feedback["summary"]["mark_count"] == summary_before["mark_count"] + 4,
        )
        seen = next(i for i in groups["items"] if i["selected_count"] == 3)
        own = (await choose(guest, offer, own, offer["items"][0], False)).json()
        read = await call(
            owner, "POST", "/api/notifications/" + seen["id"] + "/read", json={"sequence": seen["sequence"]}
        )
        check(
            "old read ack leaves racing newer mark unread",
            next(i for i in read["items"] if i["id"] == seen["id"])["unread"],
        )
        for item in offer["items"][1:]:
            own = (await choose(guest, offer, own, item, False)).json()
        check(
            "last unselect clears group count",
            next(
                i for i in (await call(owner, "GET", "/api/notifications"))["items"] if i["id"] == seen["id"]
            )["selected_count"]
            == 0,
        )
        before_stream = await call(owner, "GET", "/api/notifications")
        started = time.perf_counter()
        async with owner.stream(
            "GET", "/api/notifications/events?cursor=" + str(before_stream["cursor"])
        ) as stream:
            check(
                "real Vercel SSE content-type",
                stream.status_code == 200 and stream.headers["content-type"].startswith("text/event-stream"),
            )
            lines = stream.aiter_lines()
            async with asyncio.timeout(15):
                async for line in lines:
                    if line == "event: stream.ready":
                        evidence["transport"]["ready_seconds"] = round(time.perf_counter() - started, 3)
                        break
                await asyncio.sleep(0.15)
                commit_started = time.perf_counter()
                own = (await choose(guest, offer, own, offer["items"][0])).json()
                async for line in lines:
                    if line == "event: account.changed":
                        evidence["transport"]["later_event_seconds"] = round(time.perf_counter() - started, 3)
                        evidence["transport"]["write_to_sse_seconds"] = round(
                            time.perf_counter() - commit_started, 3
                        )
                        break
                else:
                    raise AssertionError("No incremental account signal")
        check(
            "SSE delivery does not mark read",
            (await call(owner, "GET", "/api/notifications"))["unread_count"] == before["unread_count"] + 2,
        )
        document = await call(owner, "GET", "/api/offers/" + offer["document"])
        check(
            "guest marks leave document version unchanged", document["version"] == document_before["version"]
        )
        check(
            "guest marks leave link expiry unchanged", document["share"]["expires_at"] == offer["expires_at"]
        )
        for kind in ("pdf", "xlsx"):
            response = await guest.get(view[kind + "_url"])
            check(
                "guest choices do not render/change " + kind,
                hashlib.sha256(response.content).hexdigest() == hashes[kind],
            )
        state["http_guests"] = [dict(guest.cookies), dict(second.cookies)]
        state["export_hashes"] = hashes
        save(ledger, state)
    save(public / "http.json", evidence)
    print("Real Vercel HTTPS API checks:", len(evidence["checks"]), "incremental SSE verified")


async def run(phase, state, root, ledger, public):
    if phase == "setup":
        return await setup(state, ledger)
    if phase == "http":
        return await http(state, ledger, public)
    from quality.client_selection_processes import run as processes

    return await processes(phase, state, root, ledger, public)
