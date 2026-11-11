"""Guest scope, durable state and revision semantics without touching the live DB."""

import asyncio
import copy
from datetime import timedelta
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from test_commercial_offers import clients as clients
from test_commercial_offers import create_document, edit_body

from app.database.models import (
    CommercialOfferShare as Share,
)
from app.database.models import (
    ManagerEvent,
    ManagerNotification,
    OfferClientMutation,
    OfferClientSelection,
    OfferVisitor,
    PresentationRevision,
)
from app.domain.commercial_offer import KIND, client_snapshot
from app.domain.models import utcnow


@pytest.fixture
async def published(clients):
    app, owner, other, guest = clients
    project, doc = await create_document(app, owner)
    await app.state.offers.ensure_vault()
    token, digest, encrypted = app.state.offers.vault.create()
    now = utcnow()
    revision = str(uuid4())
    async with app.state.sessions.begin() as db:
        db.add(
            PresentationRevision(
                id=revision,
                document_id=doc["id"],
                source_version=doc["version"],
                status="READY",
                input={"kind": KIND},
                result={
                    "client_snapshot": client_snapshot(doc["draft"]),
                    "snapshot_hash": "fixture",
                    "asset_keys": {},
                },
                attempts=0,
            )
        )
        await db.flush()
        db.add(
            Share(
                document_id=doc["id"],
                token_hash=digest,
                encrypted_token=encrypted,
                active=True,
                epoch=0,
                activated_at=now,
                expires_at=now + timedelta(days=14),
                published_revision_id=revision,
            )
        )
    return app, owner, other, guest, project, doc, revision, "/api/public/offers/" + token


HEADERS = {"Origin": "http://test", "X-Offer-Selection": "1"}


async def start(guest, path, revision):
    response = await guest.post(
        path + "/selection/session", json={"published_revision_id": revision}, headers=HEADERS
    )
    assert response.status_code == 200, response.text
    return response.json()


async def choose(guest, path, state, item, selected=True, mutation=None, **changes):
    body = {
        "item_id": item,
        "selected": selected,
        "published_revision_id": state["published_revision_id"],
        "mutation_id": mutation or str(uuid4()),
        "version": state["version"],
    } | changes
    return await guest.put(
        path + "/selection", json=body, headers=HEADERS | {"X-Offer-CSRF": state["csrf_token"]}
    )


async def count(app, model):
    async with app.state.sessions() as db:
        return await db.scalar(select(func.count()).select_from(model))


async def test_reads_and_preview_do_not_create_interest(published):
    app, owner, other, guest, project, doc, revision, path = published
    for url in [path, path + "/selection", "/api/offers/" + doc["id"]]:
        response = await (owner if url.startswith("/api/offers") else guest).get(url)
        assert response.status_code == 200, response.text
    for model in [OfferVisitor, OfferClientSelection, ManagerNotification, ManagerEvent]:
        assert await count(app, model) == 0
    state = await start(guest, path, revision)
    assert state["selected_items"] == [] and state["initialized"]
    assert await count(app, ManagerNotification) == 0
    assert (await owner.get("/api/notifications")).json()["unread_count"] == 0


async def test_guest_state_owner_projection_and_safe_cookie(published):
    app, owner, other, guest, project, doc, revision, path = published
    response = await guest.post(
        path + "/selection/session", json={"published_revision_id": revision}, headers=HEADERS
    )
    assert "HttpOnly" in response.headers["set-cookie"] and "SameSite=lax" in response.headers["set-cookie"]
    state = response.json()
    item = doc["draft"]["items"][0]["id"]
    response = await choose(guest, path, state, item)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["version"] == 1 and result["changed"]
    assert result["selected_items"] == [{"item_id": item, "published_revision_id": revision}]
    assert "visitor_id" not in result and "user_id" not in result
    restored = (await guest.get(path + "/selection")).json()
    assert restored["selected_items"] == result["selected_items"]
    feedback = (await owner.get("/api/offers/" + doc["id"] + "/client-selection")).json()
    assert feedback["summary"] == {
        "visitor_count": 1,
        "item_count": 1,
        "mark_count": 1,
        "historical_mark_count": 0,
    }
    assert feedback["visitors"][0]["label"] == "Посетитель 1"
    assert feedback["visitors"][0]["items"][0]["sku"] == doc["draft"]["items"][0]["sku"]
    assert (await other.get("/api/offers/" + doc["id"] + "/client-selection")).status_code == 404
    assert (await guest.get("/api/notifications")).status_code == 401
    notification = (await owner.get("/api/notifications")).json()
    assert notification["unread_count"] == 1
    assert (await other.get("/api/notifications")).json()["unread_count"] == 0
    assert (await owner.get(f"/api/projects/{project}/collection")).json()["selected_ids"] == [
        i["offer_id"] for i in doc["draft"]["items"]
    ]
    assert (await owner.get("/api/offers/" + doc["id"])).json()["version"] == doc["version"]


async def test_idempotency_noop_and_last_unselect_update_one_group(published):
    app, owner, other, guest, project, doc, revision, path = published
    initial = await start(guest, path, revision)
    item = doc["draft"]["items"][0]["id"]
    mutation = str(uuid4())
    first = (await choose(guest, path, initial, item, mutation=mutation)).json()
    repeated = await choose(guest, path, initial, item, mutation=mutation)
    assert repeated.status_code == 200 and repeated.json()["replayed"]
    assert await count(app, ManagerEvent) == 1 and await count(app, OfferClientMutation) == 1
    noop = await choose(guest, path, first, item)
    assert noop.status_code == 200 and not noop.json()["changed"] and noop.json()["version"] == 1
    assert await count(app, ManagerEvent) == 1
    mismatch = await choose(guest, path, first, item, False, mutation=mutation)
    assert mismatch.status_code == 409 and mismatch.json()["detail"]["code"] == "MUTATION_REUSED"
    before = (await owner.get("/api/notifications")).json()["items"][0]
    removed = (await choose(guest, path, first, item, False)).json()
    assert removed["selected_items"] == [] and removed["version"] == 2
    after = (await owner.get("/api/notifications")).json()
    assert (
        len(after["items"]) == 1
        and after["items"][0]["id"] == before["id"]
        and after["items"][0]["selected_count"] == 0
    )
    assert await count(app, ManagerEvent) == 2


async def test_read_ack_does_not_swallow_a_newer_choice(published):
    app, owner, other, guest, project, doc, revision, path = published
    state = await start(guest, path, revision)
    items = doc["draft"]["items"]
    first = (await choose(guest, path, state, items[0]["id"])).json()
    old = (await owner.get("/api/notifications")).json()["items"][0]
    await choose(guest, path, first, items[1]["id"])
    seen = await owner.post("/api/notifications/" + old["id"] + "/read", json={"sequence": old["sequence"]})
    assert seen.status_code == 200 and seen.json()["unread_count"] == 1
    fresh = seen.json()["items"][0]
    read = await owner.post("/api/notifications/" + old["id"] + "/read", json={"sequence": fresh["sequence"]})
    assert read.json()["unread_count"] == 0
    assert (
        await owner.post("/api/notifications/" + old["id"] + "/read", json={"sequence": fresh["sequence"]})
    ).json()["cursor"] == read.json()["cursor"]
    assert (
        await other.post("/api/notifications/" + old["id"] + "/read", json={"sequence": fresh["sequence"]})
    ).status_code == 404


async def test_visitors_do_not_share_choices_and_two_marks_are_not_two_items(published):
    app, owner, other, guest, project, doc, revision, path = published
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as guest2:
        state1 = await start(guest, path, revision)
        state2 = await start(guest2, path, revision)
        item = doc["draft"]["items"][0]["id"]
        await choose(guest, path, state1, item)
        assert (await guest2.get(path + "/selection")).json()["selected_items"] == []
        await choose(guest2, path, state2, item)
        feedback = (await owner.get("/api/offers/" + doc["id"] + "/client-selection")).json()
        assert (
            feedback["summary"]["visitor_count"] == 2
            and feedback["summary"]["item_count"] == 1
            and feedback["summary"]["mark_count"] == 2
        )
        assert (
            len(feedback["visitors"]) == 2
            and (await owner.get("/api/notifications")).json()["unread_count"] == 2
        )


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Origin": "https://evil.example", "X-Offer-Selection": "1"},
        {"Origin": "null", "X-Offer-Selection": "1"},
        {"Origin": "http://test"},
        {"Origin": "http://test", "X-Offer-Selection": "1", "Sec-Fetch-Site": "cross-site"},
    ],
)
async def test_session_creation_requires_real_origin_and_non_simple_header(published, headers):
    app, owner, other, guest, project, doc, revision, path = published
    r = await guest.post(
        path + "/selection/session", json={"published_revision_id": revision}, headers=headers
    )
    assert r.status_code == 403 and await count(app, OfferVisitor) == 0


async def test_csrf_item_revision_and_extra_fields_are_checked_per_write(published):
    app, owner, other, guest, project, doc, revision, path = published
    state = await start(guest, path, revision)
    item = doc["draft"]["items"][0]["id"]
    badcsrf = {**state, "csrf_token": "bad"}
    assert (await choose(guest, path, badcsrf, item)).status_code == 403
    assert (await choose(guest, path, state, str(uuid4()))).status_code == 404
    assert (await choose(guest, path, state, item, published_revision_id=str(uuid4()))).status_code == 409
    assert (await choose(guest, path, state, item, visitor_id=str(uuid4()))).status_code == 422
    assert (await choose(guest, path, state, item, unit_price="1.00")).status_code == 422
    assert await count(app, OfferClientSelection) == 0


async def test_expiry_blocks_new_reads_and_writes_but_owner_keeps_history(published):
    app, owner, other, guest, project, doc, revision, path = published
    state = await start(guest, path, revision)
    item = doc["draft"]["items"][0]["id"]
    result = (await choose(guest, path, state, item)).json()
    app.state.client_selections.now = lambda: utcnow() + timedelta(days=15)
    assert (await guest.get(path + "/selection")).status_code == 410
    assert (await choose(guest, path, result, item, False)).status_code == 410
    assert (
        len(
            (await owner.get("/api/offers/" + doc["id"] + "/client-selection")).json()["visitors"][0]["items"]
        )
        == 1
    )


async def test_rotation_starts_another_round_without_reusing_old_cookie(published):
    app, owner, other, guest, project, doc, revision, path = published
    state = await start(guest, path, revision)
    item = doc["draft"]["items"][0]["id"]
    await choose(guest, path, state, item)
    rotated = (
        await owner.post("/api/offers/" + doc["id"] + "/rotate", json={"version": doc["version"], "epoch": 0})
    ).json()
    newpath = "/api/public/offers/" + rotated["share"]["local_preview_path"].split("/")[-1]
    assert (await guest.get(path + "/selection")).status_code == 404
    newstate = (await guest.get(newpath + "/selection")).json()
    assert not newstate["initialized"] and newstate["selected_items"] == []
    assert (
        await choose(guest, newpath, {**newstate, "csrf_token": state["csrf_token"]}, item)
    ).status_code == 401
    await choose(guest, newpath, await start(guest, newpath, revision), item)
    feedback = (await owner.get("/api/offers/" + doc["id"] + "/client-selection")).json()
    assert {v["current_round"] for v in feedback["visitors"]} == {True, False}


async def test_publication_reorder_price_and_removed_item_keep_original_revision(published):
    app, owner, other, guest, project, doc, revision, path = published
    state = await start(guest, path, revision)
    item = doc["draft"]["items"][0]["id"]
    first = (await choose(guest, path, state, item)).json()
    body = edit_body(doc)
    body["items"].reverse()
    next(i for i in body["items"] if i["id"] == item)["unit_price"] = "555.00"
    updated = (await owner.patch("/api/offers/" + doc["id"], json=body)).json()

    async def publish_fixture(draft):
        new = str(uuid4())
        async with app.state.sessions.begin() as db:
            db.add(
                PresentationRevision(
                    id=new,
                    document_id=doc["id"],
                    source_version=updated["version"],
                    status="READY",
                    input={"kind": KIND},
                    result={
                        "client_snapshot": client_snapshot(draft),
                        "snapshot_hash": "new",
                        "asset_keys": {},
                    },
                )
            )
            await db.flush()
            (await db.get(Share, doc["id"])).published_revision_id = new
        return new

    new = await publish_fixture(updated["draft"])
    assert (await choose(guest, path, first, item, False)).status_code == 409
    retained = (await guest.get(path + "/selection")).json()
    assert (
        retained["selected_items"][0]["published_revision_id"] == revision
        and retained["published_revision_id"] == new
    )
    selected = (await owner.get("/api/offers/" + doc["id"] + "/client-selection")).json()["visitors"][0][
        "items"
    ][0]
    assert (
        selected["conditions_changed"]
        and selected["unit_price"] == "111.11"
        and selected["published_revision_id"] == revision
    )
    changed = copy.deepcopy(updated["draft"])
    changed["items"] = [i for i in changed["items"] if i["id"] != item]
    await publish_fixture(changed)
    assert (await guest.get(path + "/selection")).json()["selected_items"] == []
    feedback = (await owner.get("/api/offers/" + doc["id"] + "/client-selection")).json()
    assert (
        feedback["visitors"][0]["items"][0]["removed"] and feedback["summary"]["historical_mark_count"] == 1
    )


async def test_soft_delete_blocks_guest_and_hides_active_notifications(published):
    app, owner, other, guest, project, doc, revision, path = published
    state = await start(guest, path, revision)
    item = doc["draft"]["items"][0]["id"]
    first = (await choose(guest, path, state, item)).json()
    r = await owner.request(
        "DELETE", "/api/offers/" + doc["id"], json={"version": doc["version"], "confirmed": True}
    )
    assert r.status_code == 200, r.text
    assert (await choose(guest, path, first, item, False)).status_code == 404
    assert (await guest.get(path + "/selection")).status_code == 404
    assert (await owner.get("/api/notifications")).json()["items"] == []
    assert (await owner.get("/api/offers/" + doc["id"] + "/client-selection")).status_code == 404


async def test_transaction_rollback_leaves_no_choice_event_or_notification(published, monkeypatch):
    import app.application.client_selections as implementation

    app, owner, other, guest, project, doc, revision, path = published
    state = await start(guest, path, revision)
    item = doc["draft"]["items"][0]["id"]
    original = implementation.next_owner_event

    async def fail_after_allocation(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError("isolated transaction rollback")

    monkeypatch.setattr(implementation, "next_owner_event", fail_after_allocation)
    assert (await choose(guest, path, state, item)).status_code == 500
    for model in [OfferClientSelection, OfferClientMutation, ManagerNotification, ManagerEvent]:
        assert await count(app, model) == 0
    assert (await guest.get(path + "/selection")).json()["version"] == 0
    monkeypatch.setattr(implementation, "next_owner_event", original)
    assert (await choose(guest, path, state, item)).status_code == 200


async def test_cursor_replay_and_missing_history_require_snapshot(published):
    app, owner, other, guest, project, doc, revision, path = published
    state = await start(guest, path, revision)
    await choose(guest, path, state, doc["draft"]["items"][0]["id"])
    user = (await owner.get("/api/auth/me")).json()["id"]
    assert (await app.state.client_notifications.events(user, 0))["events"] == [1]
    assert (await app.state.client_notifications.events(user, 99))["reset"]
    async with app.state.sessions.begin() as db:
        await db.delete(await db.get(ManagerEvent, (user, 1)))
    assert (await app.state.client_notifications.events(user, 0))["reset"]
    assert (await owner.get("/api/notifications")).json()["unread_count"] == 1


async def test_two_tabs_cas_and_replay_after_service_recreation(published):
    from app.application.client_notifications import ClientNotificationService
    from app.application.client_selections import ClientSelectionService

    app, owner, other, guest, project, doc, revision, path = published
    state = await start(guest, path, revision)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", cookies=dict(guest.cookies)
    ) as tab:
        responses = await asyncio.gather(
            choose(guest, path, state, doc["draft"]["items"][0]["id"]),
            choose(tab, path, state, doc["draft"]["items"][1]["id"]),
        )
        assert sorted(r.status_code for r in responses) == [200, 409]
        winner = next(r.json() for r in responses if r.status_code == 200)
        rejected_index = next(n for n, r in enumerate(responses) if r.status_code == 409)
        service = ClientSelectionService(app.state.offers, app.state.settings)
        app.state.client_selections = service
        app.state.client_notifications = ClientNotificationService(service)
        restored = (await tab.get(path + "/selection")).json()
        assert restored["selected_items"] == winner["selected_items"]
        assert (
            await choose(tab, path, restored, doc["draft"]["items"][rejected_index]["id"])
        ).status_code == 200
        assert len((await tab.get(path + "/selection")).json()["selected_items"]) == 2
        assert await count(app, ManagerNotification) == 1
        assert await count(app, ManagerEvent) == 2


async def test_expiry_while_waiting_rolls_back_even_after_event_allocation(published, monkeypatch):
    import app.application.client_selections as implementation

    app, owner, other, guest, project, doc, revision, path = published
    state = await start(guest, path, revision)
    original = implementation.next_owner_event

    async def cross_expiry(*args, **kwargs):
        value = await original(*args, **kwargs)
        app.state.client_selections.now = lambda: utcnow() + timedelta(days=15)
        return value

    monkeypatch.setattr(implementation, "next_owner_event", cross_expiry)
    response = await choose(guest, path, state, doc["draft"]["items"][0]["id"])
    assert response.status_code == 410
    for model in [OfferClientSelection, OfferClientMutation, ManagerNotification, ManagerEvent]:
        assert await count(app, model) == 0


async def test_budgets_are_bounded_without_deleting_prior_state(published):
    app, owner, other, guest, project, doc, revision, path = published
    state = await start(guest, path, revision)
    app.state.client_selections.settings = app.state.settings.model_copy(
        update={"offer_visitor_max_sessions": 1, "offer_visitor_max_mutations": 1}
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as another:
        limited = await another.post(
            path + "/selection/session", json={"published_revision_id": revision}, headers=HEADERS
        )
        assert limited.status_code == 429 and limited.headers["retry-after"]
    item = doc["draft"]["items"][0]["id"]
    mutation = str(uuid4())
    chosen = (await choose(guest, path, state, item, mutation=mutation)).json()
    # A known receipt remains replayable even after the durable mutation cap.
    assert (await choose(guest, path, state, item, mutation=mutation)).status_code == 200
    limited = await choose(guest, path, chosen, item, False)
    assert limited.status_code == 429
    assert (await guest.get(path + "/selection")).json()["selected_items"] == chosen["selected_items"]
    assert await count(app, ManagerEvent) == 1


async def test_notification_http_stream_auth_cursor_and_replay(published):
    app, owner, other, guest, project, doc, revision, path = published
    state = await start(guest, path, revision)
    await choose(guest, path, state, doc["draft"]["items"][0]["id"])
    assert (await guest.get("/api/notifications/events")).status_code == 401
    assert (await owner.get("/api/notifications/events", headers={"Last-Event-ID": "bad"})).status_code == 400
    # Bound the real ASGI stream in this isolated test; no live configuration changes.
    app.state.settings.notification_stream_seconds = 0.03
    app.state.settings.notification_poll_seconds = 0.005
    r = await owner.get("/api/notifications/events?cursor=0")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    assert "no-transform" in r.headers["cache-control"]
    assert "id: 1\nevent: account.changed" in r.text
    assert "stream.ready" in r.text and "stream.renew" in r.text
    assert doc["id"] not in r.text and state["csrf_token"] not in r.text
    assert (
        "account.changed"
        not in (await owner.get("/api/notifications/events", headers={"Last-Event-ID": "1"})).text
    )
    assert "account.changed" not in (await other.get("/api/notifications/events?cursor=0")).text
    assert (await owner.get("/api/notifications")).json()["unread_count"] == 1
