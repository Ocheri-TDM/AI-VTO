"""Conversation identity, paginated history and owner/public separation."""

import asyncio
import copy
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from test_commercial_offers import clients as _clients
from test_commercial_offers import create_document, execute

from app.database.models import (
    Message,
    PresentationDocument,
    PresentationRevision,
    ProductObservation,
    ProjectQuery,
)
from app.domain.models import utcnow

clients = _clients


@pytest.mark.parametrize(
    "query,expected",
    [
        ("Красные ручки от 200 шт.", "Ручки"),
        ("Ручки, блокноты и кепки", "Ручки, блокноты и кепки"),
        ("Синий мерч для IT-конференции", "Мерч для IT-конференции"),
    ],
)
async def test_short_title_uses_parsed_concepts_or_user_purpose(clients, query, expected):
    from app.application.conversation_turns import title_for

    app, _, _, _ = clients
    assert title_for(await app.state.research.grid_plan(query)) == expected


async def new_chat(owner, key=None):
    response = await owner.post("/api/chats", json={"request_id": key or str(uuid4())})
    assert response.status_code == 201, response.text
    chat = response.json()["id"]
    project = next(p for p in (await owner.get("/api/projects")).json() if p["chat_id"] == chat)
    return project


async def send(owner, project, query, key=None):
    return await owner.post(
        f"/api/projects/{project['id']}/requests", json={"query": query, "request_id": key or str(uuid4())}
    )


async def test_create_idempotence_empty_collection_and_no_automatic_document(clients):
    app, owner, _, _ = clients
    key = str(uuid4())
    a, b = await asyncio.gather(new_chat(owner, key), new_chat(owner, key))
    assert a["id"] == b["id"] and a["name"] == "Новый чат" and a["name_source"] == "default"
    assert a["has_messages"] is False
    c = await new_chat(owner)
    assert c["id"] != a["id"]
    assert (await owner.get(f"/api/projects/{a['id']}/collection")).json()["selected_ids"] == []
    async with app.state.sessions() as db:
        assert not await db.scalar(select(func.count()).select_from(PresentationDocument))


async def test_raw_history_five_messages_replay_and_history_activation_do_not_erase(clients):
    app, owner, other, _ = clients
    p = await new_chat(owner)
    key = str(uuid4())
    first = None
    queries = [
        "Синие бутылки, 300 шт.",
        "Ручки и блокноты",
        "Оставь только красные",
        "Рюкзаки",
        "Оставь только синие",
    ]
    for n, q in enumerate(queries):
        response = await send(owner, p, q, key if n == 0 else None)
        assert response.status_code == 202, response.text
        if n == 0:
            first = response.json()
    replay = await send(owner, p, queries[0], key)
    assert replay.status_code == 202 and replay.json()["active"] is False
    assert replay.json()["turn"]["id"] == first["turn"]["id"]
    conflict = await send(owner, p, "Другой текст", key)
    assert conflict.status_code == 409
    history = (await owner.get(f"/api/chats/{p['chat_id']}/history")).json()
    assert len(history["messages"]) == 10
    assert [m["content"] for m in history["messages"] if m["role"] == "user"] == queries
    assert all(m["operation_id"] and m["status"] == "COMPLETED" for m in history["messages"])
    assert (
        next(p2 for p2 in (await owner.get("/api/projects")).json() if p2["id"] == p["id"])["has_messages"]
        is True
    )
    assert (await other.get(f"/api/chats/{p['chat_id']}/history")).status_code == 404
    rows = (await owner.get(f"/api/projects/{p['id']}/queries")).json()
    await owner.post(f"/api/projects/{p['id']}/queries/{rows[-1]['id']}/activate")
    assert (await owner.get(f"/api/chats/{p['chat_id']}/history")).json()["messages"] == history["messages"]
    assert (
        next(v for v in (await owner.get("/api/projects")).json() if v["id"] == p["id"])["name"] == "Бутылки"
    )


async def test_late_search_answer_stays_in_history_without_overwriting_manual_title_or_grid(clients):
    app, owner, _, _ = clients
    p = await new_chat(owner)
    started = asyncio.Event()
    release = asyncio.Event()
    original = app.state.research.grid_start

    async def delayed(chat, query, *args, **kwargs):
        if query == "ручки":
            started.set()
            await release.wait()
        return await original(chat, query, *args, **kwargs)

    app.state.research.grid_start = delayed
    a = asyncio.create_task(send(owner, p, "ручки"))
    await started.wait()
    accepted = (await owner.get(f"/api/chats/{p['chat_id']}/history")).json()["messages"]
    assert len(accepted) == 1 and accepted[0]["status"] == "ACCEPTED"
    assert (
        await owner.patch("/api/projects/" + p["id"], json={"name": "Имя менеджера", "client_name": None})
    ).status_code == 200
    b = await send(owner, p, "рюкзаки")
    release.set()
    older = await a
    assert b.status_code == 202 and older.status_code == 409, older.text
    active = (await owner.get("/api/researches/chat/" + p["chat_id"])).json()
    assert active["id"] == b.json()["research"]["id"]
    messages = (await owner.get(f"/api/chats/{p['chat_id']}/history")).json()["messages"]
    assert len(messages) == 4
    assert any(
        m["role"] == "assistant" and m["status"] == "SUPERSEDED" and m["research_id"] for m in messages
    )
    title = next(v for v in (await owner.get("/api/projects")).json() if v["id"] == p["id"])
    assert title["name"] == "Имя менеджера" and title["name_source"] == "manual"
    async with app.state.sessions() as db:
        assert (
            await db.scalar(
                select(func.count()).select_from(ProjectQuery).where(ProjectQuery.project_id == p["id"])
            )
            == 2
        )


async def test_search_error_and_cancellation_preserve_accepted_user_turn(clients):
    app, owner, _, _ = clients
    p = await new_chat(owner)

    async def fail(*args, **kw):
        raise TimeoutError("controlled timeout")

    app.state.research.grid_start = fail
    with pytest.raises(TimeoutError):
        await app.state.project_queries.request(
            p["id"], "Исходный текст. С точками.", (await owner.get("/api/auth/me")).json()["id"], uuid4()
        )
    messages = (await owner.get("/api/chats/" + p["chat_id"] + "/history")).json()["messages"]
    assert (
        len(messages) == 2
        and messages[0]["content"] == "Исходный текст. С точками."
        and messages[0]["status"] == "FAILED"
    )
    started = asyncio.Event()

    async def slow(*args, **kw):
        started.set()
        await asyncio.Event().wait()

    app.state.research.grid_start = slow
    task = asyncio.create_task(
        app.state.project_queries.request(
            p["id"], "Ещё сообщение", (await owner.get("/api/auth/me")).json()["id"], uuid4()
        )
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    history = (await owner.get("/api/chats/" + p["chat_id"] + "/history")).json()["messages"]
    assert len(history) == 4 and history[-1]["status"] == "CANCELLED"


async def test_message_cursor_is_stable_bounded_and_chat_scoped(clients):
    import base64
    import json

    app, owner, _, _ = clients
    p = await new_chat(owner)
    now = utcnow() - timedelta(days=1)
    async with app.state.sessions.begin() as db:
        db.add_all(
            [
                Message(
                    id=str(uuid4()),
                    chat_id=p["chat_id"],
                    role="user",
                    content="Каталог обновляется автоматически" if n == 0 else f"Message {n}",
                    created_at=now + timedelta(seconds=n),
                )
                for n in range(137)
            ]
        )
    messages = []
    before = None
    pages = 0
    while True:
        result = (
            await owner.get(
                "/api/chats/" + p["chat_id"] + "/history", params={"before": before} if before else {}
            )
        ).json()
        assert len(result["messages"]) <= 50
        messages = result["messages"] + messages
        pages += 1
        before = result["next_before"]
        if not before:
            break
    assert pages == 3 and len({m["id"] for m in messages}) == 137
    assert messages[0]["content"] == "Каталог обновляется автоматически"
    other = await new_chat(owner)
    first = (await owner.get("/api/chats/" + p["chat_id"] + "/history")).json()
    assert (
        await owner.get(
            "/api/chats/" + other["chat_id"] + "/history", params={"before": first["next_before"]}
        )
    ).status_code == 422
    malformed = base64.urlsafe_b64encode(json.dumps([p["chat_id"], now.isoformat(), {}]).encode()).decode()
    assert (
        await owner.get(f"/api/chats/{p['chat_id']}/history", params={"before": malformed})
    ).status_code == 422
    malformed = base64.urlsafe_b64encode(json.dumps([now.isoformat(), {}]).encode()).decode()
    assert (await owner.get("/api/offers", params={"cursor": malformed})).status_code == 422


async def test_pending_retry_is_not_executed_twice_and_interrupted_turn_is_visible(clients):
    from app.database.models import ConversationTurn

    app, owner, _, _ = clients
    p = await new_chat(owner)
    original = app.state.research.grid_start
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def delayed(*args, **kwargs):
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        return await original(*args, **kwargs)

    app.state.research.grid_start = delayed
    key = str(uuid4())
    request = asyncio.create_task(send(owner, p, "Ручки", key))
    await entered.wait()
    try:
        replay = await send(owner, p, "Ручки", key)
        assert replay.status_code == 202 and replay.json()["research"] is None
        assert replay.json()["turn"]["status"] == "ACCEPTED" and calls == 1
    finally:
        release.set()
    assert (await request).status_code == 202
    assert len((await owner.get(f"/api/chats/{p['chat_id']}/history")).json()["messages"]) == 2
    identifier, message_id = str(uuid4()), str(uuid4())
    async with app.state.sessions.begin() as db:
        db.add(
            Message(
                id=message_id,
                chat_id=p["chat_id"],
                role="user",
                content="Принято до перезапуска",
                operation_id=identifier,
            )
        )
        await db.flush()
        db.add(
            ConversationTurn(
                id=identifier,
                chat_id=p["chat_id"],
                request_key=str(uuid4()),
                request_epoch=2,
                user_message_id=message_id,
                status="ACCEPTED",
                created_at=utcnow() - timedelta(minutes=6),
            )
        )
    page = (await owner.get(f"/api/chats/{p['chat_id']}/history")).json()
    assert len(page["messages"]) == 4
    assert page["messages"][-1]["status"] == "INTERRUPTED"
    assert "прервано" in page["messages"][-1]["content"]


async def test_owner_listing_paginates_documents_not_revisions_and_excludes_legacy(clients):
    app, owner, other, _ = clients
    project, doc = await create_document(app, owner)
    async with app.state.sessions.begin() as db:
        original = await db.get(PresentationDocument, doc["id"])
        db.add(
            PresentationDocument(
                project_id=project,
                user_id=original.user_id,
                draft=copy.deepcopy(original.draft),
                is_primary_offer=False,
            )
        )
        db.add(
            PresentationDocument(
                project_id=project,
                user_id=original.user_id,
                draft={"settings": {"title": "Архив"}, "items": []},
            )
        )
    result = await owner.get("/api/offers", params={"limit": 1})
    assert result.status_code == 200, result.text
    page = result.json()
    assert len(page["items"]) == 1 and page["next_cursor"]
    next_page = (await owner.get("/api/offers", params={"limit": 1, "cursor": page["next_cursor"]})).json()
    assert len(next_page["items"]) == 1 and next_page["items"][0]["id"] != page["items"][0]["id"]
    assert (await other.get("/api/offers")).json()["items"] == []
    archive = await owner.get("/api/offers/archive")
    assert archive.status_code == 200, archive.text
    assert len(archive.json()["items"]) == 1
    assert not any(
        token in result.text for token in ["encrypted_token", "storage", "public_url", "local_preview_path"]
    )
    async with app.state.sessions() as db:
        assert not await db.scalar(select(func.count()).select_from(PresentationRevision))


async def test_owner_stock_is_current_batched_projection_without_public_leak_or_snapshot_mutation(clients):
    app, owner, _, guest = clients
    _, doc = await create_document(app, owner)
    original = copy.deepcopy(doc["draft"])
    async with app.state.sessions.begin() as db:
        row = await db.get(ProductObservation, "vto:offer-pen")
        row.stock = 13
        row.incoming = 7
        row.observed_at = utcnow() - timedelta(days=40)
        row = await db.get(ProductObservation, "vto:offer-backpack")
        row.stock = 0
    response = await owner.get("/api/offers/" + doc["id"])
    assert response.status_code == 200
    updated = response.json()
    assert updated["draft"] == original
    by_offer = {i["offer_id"]: updated["owner_stock"][i["id"]] for i in updated["draft"]["items"]}
    assert by_offer["vto:offer-pen"]["quantity"] == 13 and by_offer["vto:offer-pen"]["incoming_quantity"] == 7
    assert by_offer["vto:offer-pen"]["snapshot_quantity"] == 500
    assert (
        by_offer["vto:offer-backpack"]["quantity"] == 0 and by_offer["vto:offer-bottle"]["quantity"] is None
    )
    action = {"version": doc["version"], "request_id": str(uuid4()), "share_epoch": 0}
    assert (await owner.post("/api/offers/" + doc["id"] + "/publish", json=action)).status_code == 202
    await execute(app)
    ready = (await owner.get("/api/offers/" + doc["id"])).json()
    token = ready["share"]["local_preview_path"].split("/")[-1]
    public = await guest.get("/api/public/offers/" + token)
    assert public.status_code == 200
    assert all(
        key not in public.text
        for key in ["owner_stock", "snapshot_quantity", "observed_at", "incoming_quantity", "stock"]
    )
    assert ready["share"]["lifetime_days"] == 14
