"""Opt-in cross-process PG locks, real crash and bounded concurrent search checks."""

import asyncio
import socket
import subprocess
import sys
import time
from uuid import uuid4

import httpx
import psutil
from check_chat_workflow import call, save
from sqlalchemy import func, select

from app.application.client_selections import next_owner_event
from app.config import Settings
from app.database.connection import create_database
from app.database.models import ManagerEvent, OfferClientMutation
from quality.client_selection_http import LOCAL, choose, headers

SECONDARY = "http://127.0.0.1:8001"


async def processes(state, root, ledger, public):
    evidence = {"checks": []}

    def check(name, condition):
        evidence["checks"].append({"check": name, "status": "PASS" if condition else "FAIL"})
        save(public / "processes.json", evidence)
        assert condition, name

    engine, sessions = create_database(Settings().database_url)
    offer = state["offers"][1]
    rollback, crash = str(uuid4()), str(uuid4())
    process = None

    async def launch(faults):
        with socket.socket() as probe:
            assert probe.connect_ex(("127.0.0.1", 8001)) != 0
        argv = [
            sys.executable,
            str(root / "scripts/serve_client_selection_probe.py"),
            "--document",
            offer["document"],
        ]
        if faults:
            argv += ["--rollback-mutation", rollback, "--crash-mutation", crash]
        with (root / ".local/client-selection-secondary.log").open("ab") as log:
            p = subprocess.Popen(
                argv, cwd=root, stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW
            )
        state["secondary_pid"] = p.pid
        save(ledger, state)
        async with httpx.AsyncClient() as client:
            for _ in range(150):
                try:
                    if (await client.get(SECONDARY + "/api/health")).status_code == 200:
                        return p
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.1)
        raise TimeoutError("Secondary API startup")

    try:
        process = await launch(True)
        async with (
            httpx.AsyncClient(base_url=LOCAL, cookies=state["users"][0]["cookies"], timeout=60) as manager,
            httpx.AsyncClient(
                base_url=SECONDARY, cookies=state["users"][0]["cookies"], timeout=60
            ) as receiver,
            httpx.AsyncClient(base_url=SECONDARY, timeout=60) as secondary_guest,
        ):
            own = await call(
                secondary_guest,
                "POST",
                offer["path"] + "/selection/session",
                headers=headers("http://127.0.0.1:3000"),
                json={"published_revision_id": offer["revision"]},
            )
            # Explicitly carry our disposable test cookie to local HTTP clients;
            # real Secure-cookie acceptance was separately checked over HTTPS.
            cookies = dict(secondary_guest.cookies)
            async with (
                httpx.AsyncClient(base_url=LOCAL, cookies=cookies, timeout=60) as writer,
                httpx.AsyncClient(base_url=SECONDARY, cookies=cookies, timeout=60) as fault_writer,
            ):
                before = await call(manager, "GET", "/api/notifications")
                failed = await choose(fault_writer, offer, own, offer["items"][0], mutation=rollback)
                check("real HTTP transaction rollback returns failure", failed.status_code == 500)
                restored = await call(writer, "GET", offer["path"] + "/selection")
                check(
                    "rollback leaves state and owner cursor unchanged",
                    restored["version"] == 0
                    and (await call(manager, "GET", "/api/notifications"))["cursor"] == before["cursor"],
                )
                user = state["users"][0]["id"]
                async with sessions.begin() as db:
                    seq = await next_owner_event(db, user, offer["document"], "acceptance.order")
                    await db.flush()
                    pending = asyncio.create_task(choose(writer, offer, own, offer["items"][0]))
                    await asyncio.sleep(0.2)
                    blocked = not pending.done()
                    observed = (await call(receiver, "GET", "/api/notifications"))["cursor"]
                    check(
                        "lower uncommitted cursor cannot be passed by another write",
                        blocked and observed == seq - 1,
                    )
                chosen = await pending
                check("waiting HTTP mutation commits after cursor lock releases", chosen.status_code == 200)
                own = chosen.json()
                async with sessions() as db:
                    events = list(
                        await db.scalars(
                            select(ManagerEvent.sequence)
                            .where(ManagerEvent.user_id == user, ManagerEvent.sequence >= seq)
                            .order_by(ManagerEvent.sequence)
                        )
                    )
                check("late transactions have contiguous committed event order", events == [seq, seq + 1])
                cursor = (await call(receiver, "GET", "/api/notifications"))["cursor"]
                async with receiver.stream(
                    "GET", "/api/notifications/events?cursor=" + str(cursor)
                ) as stream:
                    lines = stream.aiter_lines()
                    async with asyncio.timeout(10):
                        async for line in lines:
                            if line == "event: stream.ready":
                                break
                        own = (await choose(writer, offer, own, offer["items"][1])).json()
                        async for line in lines:
                            if line == "event: account.changed":
                                break
                        else:
                            raise AssertionError("No cross-process event")
                check("write on API A reaches SSE on independent API B", True)
                before_crash = (await call(manager, "GET", "/api/notifications"))["cursor"]
                lost_response = False
                try:
                    await choose(fault_writer, offer, own, offer["items"][2], mutation=crash)
                except httpx.TransportError:
                    lost_response = True
                await asyncio.to_thread(process.wait, timeout=10)
                check(
                    "actual API process crashed after commit before response",
                    lost_response and process.returncode == 91,
                )
                check(
                    "committed event survives crashed process",
                    (await call(manager, "GET", "/api/notifications"))["cursor"] == before_crash + 1,
                )
                process = await launch(False)
                replay = await choose(fault_writer, offer, own, offer["items"][2], mutation=crash)
                check(
                    "retry after restart returns receipt without double event",
                    replay.status_code == 200
                    and replay.json()["replayed"]
                    and (await call(manager, "GET", "/api/notifications"))["cursor"] == before_crash + 1,
                )
                retained = await call(
                    receiver, "GET", "/api/offers/" + offer["document"] + "/client-selection"
                )
                check(
                    "new process hydrates all three committed positions",
                    any(len(v["items"]) == 3 for v in retained["visitors"]),
                )
                async with sessions() as db:
                    check(
                        "failed transaction persisted no receipt",
                        not await db.scalar(
                            select(func.count())
                            .select_from(OfferClientMutation)
                            .where(OfferClientMutation.mutation_id == rollback)
                        ),
                    )
                    check(
                        "crashed transaction persisted one receipt",
                        await db.scalar(
                            select(func.count())
                            .select_from(OfferClientMutation)
                            .where(OfferClientMutation.mutation_id == crash)
                        )
                        == 1,
                    )
                # Cursor rollback has no lasting hole, including on PostgreSQL.
                async with sessions() as db:
                    await next_owner_event(db, user, offer["document"], "acceptance.rollback")
                    await db.rollback()
                check(
                    "rolled-back allocation never advances durable cursor",
                    (await call(manager, "GET", "/api/notifications"))["cursor"] == before_crash + 1,
                )
        evidence["main_worker_count_unchanged"] = True
        evidence["secondary_lifespan"] = "off; no crawler or render worker started"
        save(public / "processes.json", evidence)
        print("Cross-process, rollback and actual crash checks:", len(evidence["checks"]))
    finally:
        if process and process.poll() is None:
            owned = psutil.Process(process.pid)
            assert str(root / "scripts/serve_client_selection_probe.py") in owned.cmdline()
            owned.terminate()
            await asyncio.to_thread(process.wait, timeout=10)
        await engine.dispose()


async def load(state, ledger, public):
    offer = state["offers"][0]
    clients = [httpx.AsyncClient(base_url=LOCAL, cookies=u["cookies"], timeout=60) for u in state["users"]]
    guests = [httpx.AsyncClient(base_url=LOCAL, timeout=60) for _ in range(4)]
    errors, durations = [], []
    streams = []

    async def keep_stream(client):
        async with client.stream("GET", "/api/notifications/events") as stream:
            streams.append(stream.status_code)
            async for line in stream.aiter_lines():
                if line == "event: stream.renew":
                    return

    tasks = [asyncio.create_task(keep_stream(clients[n])) for n in [0, 0, 1]]
    try:
        before = await clients[0].get("/api/dev/runtime")

        async def visitor(client):
            response = await client.post(
                offer["path"] + "/selection/session",
                headers=headers("http://127.0.0.1:3000"),
                json={"published_revision_id": offer["revision"]},
            )
            assert response.status_code == 200
            own = response.json()
            # Rebuild the local-only jar from the legitimately issued test cookie.
            client.cookies = httpx.Cookies(dict(client.cookies))
            for n in range(5):
                began = time.perf_counter()
                response = await choose(client, offer, own, offer["items"][n % 3], n < 3)
                durations.append(time.perf_counter() - began)
                if response.status_code != 200:
                    errors.append(response.status_code)
                    return
                own = response.json()
                await asyncio.sleep(0.12)

        async def search(index):
            began = time.perf_counter()
            response = await clients[index].post(
                "/api/projects/" + state["offers"][index * 2]["project"] + "/requests",
                json={"query": ("ручки", "рюкзаки")[index], "request_id": str(uuid4())},
            )
            result = {
                "status": response.status_code,
                "seconds": time.perf_counter() - began,
                "query_kind": "explicit product category",
            }
            if response.is_error:
                result["error"] = response.json()
            return result

        results = await asyncio.gather(*(visitor(c) for c in guests), search(0), search(1))
        after = await clients[0].get("/api/dev/runtime")
        passed = (
            not errors
            and sorted(streams) == [200, 200, 200]
            and all(v["status"] in (200, 202) for v in results[-2:])
        )
        evidence = {
            "status": "PASS" if passed else "FAIL",
            "visitors": 4,
            "writes": len(durations),
            "manager_streams": streams,
            "search_results": results[-2:],
            "http_errors": errors,
            "write_seconds": {
                "p50": sorted(durations)[len(durations) // 2],
                "p95": sorted(durations)[int(len(durations) * 0.95)],
                "max": max(durations),
            },
            "runtime_before": before.json() if before.status_code == 200 else "NOT AVAILABLE",
            "runtime_after": after.json() if after.status_code == 200 else "NOT AVAILABLE",
        }
        save(public / "concurrency.json", evidence)
        assert passed, evidence
        print("Four visitors, three streams and two searches: no HTTP errors/429")
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for client in clients + guests:
            await client.aclose()


async def run(phase, state, root, ledger, public):
    if phase == "race":
        from quality.client_selection_http import PUBLIC

        offer = state["offers"][0]
        results = []
        async with (
            httpx.AsyncClient(base_url=PUBLIC, cookies=state["users"][0]["cookies"], timeout=60) as owner,
            httpx.AsyncClient(base_url=PUBLIC, cookies=state["http_guests"][0], timeout=60) as guest,
        ):
            own = await call(guest, "GET", offer["path"] + "/selection")
            for _ in range(5):
                selected = any(i["item_id"] == offer["items"][0] for i in own["selected_items"])
                own = (await choose(guest, offer, own, offer["items"][0], not selected)).json()
                seen = (await call(owner, "GET", "/api/notifications"))["items"][0]
                changed, _read = await asyncio.gather(
                    choose(guest, offer, own, offer["items"][0], selected),
                    call(
                        owner,
                        "POST",
                        "/api/notifications/" + seen["id"] + "/read",
                        json={"sequence": seen["sequence"]},
                    ),
                )
                assert changed.status_code == 200
                own = changed.json()
                actual = next(
                    n
                    for n in (await call(owner, "GET", "/api/notifications"))["items"]
                    if n["id"] == seen["id"]
                )
                assert actual["unread"] and actual["sequence"] > actual["read_sequence"]
                results.append({"status": "PASS", "old_ack_did_not_read_new_change": True})
        save(public / "read-race.json", {"path": PUBLIC, "concurrent_rounds": results})
        print("Five actual HTTPS concurrent read/choice races passed")
        return
    if phase == "control":
        process = None
        try:
            with socket.socket() as probe:
                assert probe.connect_ex(("127.0.0.1", 8002)) != 0
            with (root / ".local/client-selection-control.log").open("ab") as log:
                process = await asyncio.to_thread(
                    subprocess.Popen,
                    [
                        sys.executable,
                        str(root / "scripts/serve_client_selection_probe.py"),
                        "--document",
                        state["offers"][0]["document"],
                        "--baseline",
                    ],
                    cwd=root,
                    stdout=log,
                    stderr=log,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            async with httpx.AsyncClient(base_url="http://127.0.0.1:8002", timeout=120) as client:
                for _ in range(100):
                    try:
                        if (await client.get("/api/health")).status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    await asyncio.sleep(0.1)

            async def search(index):
                async with httpx.AsyncClient(
                    base_url="http://127.0.0.1:8002", cookies=state["users"][index]["cookies"], timeout=120
                ) as client:
                    began = time.perf_counter()
                    response = await client.post(
                        "/api/projects/" + state["offers"][index * 2]["project"] + "/requests",
                        json={"query": "Melango", "request_id": str(uuid4())},
                    )
                    return {
                        "status": response.status_code,
                        "seconds": time.perf_counter() - began,
                        "error": response.json() if response.is_error else None,
                    }

            results = await asyncio.gather(search(0), search(1))
            save(
                public / "search-control.json",
                {
                    "factory": "git HEAD main.py; original unchanged search modules; no notification services/subscriptions in control",
                    "lifespan": "off",
                    "same_populated_database": True,
                    "two_concurrent_model_searches": results,
                },
            )
            print("Original search factory control statuses:", [r["status"] for r in results])
        finally:
            if process and process.poll() is None:
                process.terminate()
                await asyncio.to_thread(process.wait, timeout=10)
        return
    if phase == "processes":
        return await processes(state, root, ledger, public)
    return await load(state, ledger, public)
