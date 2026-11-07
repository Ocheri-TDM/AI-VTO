"""Two actual users, same idempotency key and overlapping accepted requests."""

import asyncio
import time
from uuid import uuid4

import httpx


async def run(state, ledger, public):
    from check_chat_workflow import call, save

    clients = [
        httpx.AsyncClient(base_url="http://127.0.0.1:8000", cookies=u["cookies"], timeout=120)
        for u in state["users"]
    ]
    projects = []
    try:
        for client in clients:
            chat = await call(client, "POST", "/api/chats", json={"request_id": str(uuid4())})
            project = next(
                p for p in await call(client, "GET", "/api/projects") if p["chat_id"] == chat["id"]
            )
            projects.append(project)
            state["projects"].append(project["id"])
            save(ledger, state)
        key = str(uuid4())
        started = time.perf_counter()

        async def send(client, project, query, request_id):
            response = await client.post(
                f"/api/projects/{project['id']}/requests", json={"query": query, "request_id": request_id}
            )
            return response

        results = await asyncio.gather(
            send(clients[0], projects[0], "Красные ручки от 200 шт.", key),
            send(clients[0], projects[0], "Красные ручки от 200 шт.", key),
            send(clients[1], projects[1], "Синие рюкзаки от 100 шт.", key),
        )
        assert all(r.status_code == 202 for r in results)
        assert results[0].json()["turn"]["id"] == results[1].json()["turn"]["id"]
        assert results[2].json()["turn"]["id"] != results[0].json()["turn"]["id"]
        for client, project in zip(clients, projects, strict=True):
            history = await call(client, "GET", f"/api/chats/{project['chat_id']}/history")
            assert len(history["messages"]) == 2
        for client, foreign in [(clients[0], projects[1]), (clients[1], projects[0])]:
            assert (await client.get(f"/api/chats/{foreign['chat_id']}/history")).status_code == 404
        # Let the first request be durably accepted before issuing a newer one.
        owner, project = clients[0], projects[0]
        first = asyncio.create_task(send(owner, project, "Термокружки", str(uuid4())))
        for _ in range(200):
            history = await call(owner, "GET", f"/api/chats/{project['chat_id']}/history")
            if any(m["role"] == "user" and m["content"] == "Термокружки" for m in history["messages"]):
                break
            await asyncio.sleep(0.025)
        else:
            raise AssertionError("Request was not accepted")
        overlapped = not first.done()
        second = await send(owner, project, "Рюкзаки", str(uuid4()))
        old = await first
        assert old.status_code in (202, 409) and second.status_code == 202
        latest = await call(owner, "GET", f"/api/researches/chat/{project['chat_id']}")
        assert latest["intent"]["raw_query"] == "Рюкзаки"
        history = await call(owner, "GET", f"/api/chats/{project['chat_id']}/history")
        assert len(history["messages"]) == 6
        assert [m["content"] for m in history["messages"] if m["role"] == "user"] == [
            "Красные ручки от 200 шт.",
            "Термокружки",
            "Рюкзаки",
        ]
        evidence = {
            "status": "PASS",
            "http_statuses": [r.status_code for r in results],
            "later_request_statuses": [old.status_code, second.status_code],
            "requests_overlapped": overlapped,
            "same_key_same_user_one_turn": True,
            "same_key_different_users_independent": True,
            "foreign_history_404": True,
            "latest_grid_is_newer_request": True,
            "all_six_messages_preserved": True,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "scope": "bounded local smoke; forced late-A completion covered separately by permanent regression",
        }
        save(public / "concurrency.json", evidence)
        print("Concurrent PostgreSQL HTTP acceptance", evidence)
    finally:
        for client in clients:
            await client.aclose()
