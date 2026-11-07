"""Tombstones close every resource without clearing project selection/history."""

import copy
from datetime import timedelta
from uuid import uuid4

import pytest
from test_commercial_offers import clients as _clients
from test_commercial_offers import create_document, edit_body, execute

from app.database.models import CommercialOfferShare, PresentationDocument, PresentationRevision
from app.domain.models import utcnow

clients = _clients


@pytest.mark.parametrize("published", [False, True])
async def test_delete_is_owned_idempotent_revokes_resources_and_preserves_project(clients, published):
    app, owner, other, guest = clients
    project, doc = await create_document(app, owner)
    path = "/api/offers/" + doc["id"]
    collection = (await owner.get(f"/api/projects/{project}/collection")).json()
    private, public = [], []
    if published:
        await owner.post(
            path + "/publish", json={"version": doc["version"], "request_id": str(uuid4()), "share_epoch": 0}
        )
        await execute(app)
        doc = (await owner.get(path)).json()
        key = doc["share"]["local_preview_path"].rsplit("/", 1)[-1]
        value = (await guest.get("/api/public/offers/" + key)).json()
        public = [
            "/api/public/offers/" + key,
            value["pdf_url"],
            value["xlsx_url"],
            value["logo_url"],
            value["items"][0]["images"][0]["url"],
        ]
        revision = doc["share"]["published_revision_id"]
        private = [
            path + f"/revisions/{revision}/pdf",
            path + f"/revisions/{revision}/xlsx",
            path + f"/revisions/{revision}/assets/logo",
            path + f"/preview?revision={revision}",
        ]
    body = {"version": doc["version"], "confirmed": True}
    assert (await other.request("DELETE", path, json=body)).status_code == 404
    assert (await guest.request("DELETE", path, json=body)).status_code == 401
    assert (await owner.request("DELETE", path, json=body | {"confirmed": False})).status_code == 422
    assert (await owner.get(path)).status_code == 200
    assert (await owner.request("DELETE", path, json=body)).status_code == 200
    assert (await owner.request("DELETE", path, json=body)).status_code == 200
    assert (await owner.get(path)).status_code == 404
    assert (await owner.get(f"/api/presentations/{doc['id']}")).status_code == 404
    assert (await owner.patch(path, json=edit_body(doc))).status_code == 404
    assert (await owner.get(f"/api/projects/{project}/collection")).json() == collection
    assert (await owner.get(f"/api/projects/{project}/offer")).json() is None
    assert not (await owner.get("/api/offers")).json()["items"]
    for url in public:
        assert (await guest.get(url)).status_code == 404
    for url in private:
        assert (await owner.get(url)).status_code == 404
    async with app.state.sessions() as db:
        old = await db.get(PresentationDocument, doc["id"])
        assert old.deleted_at and not old.is_primary_offer and old.version == doc["version"] + 1
    # A stale get-or-create or ordinary GET cannot manufacture a replacement.
    request = {"collection_version": collection["version"]}
    assert (await owner.post(f"/api/projects/{project}/offers", json=request)).status_code == 409
    state = (await owner.get(f"/api/projects/{project}/offer-state")).json()
    assert state == {"offer": None, "generation": 1}
    new = await owner.post(
        f"/api/projects/{project}/offers", json=request | {"offer_generation": state["generation"]}
    )
    assert new.status_code == 201 and new.json()["id"] != doc["id"]
    assert (await owner.request("DELETE", path, json=body)).status_code == 200
    assert (await owner.get(f"/api/projects/{project}/offer")).json()["id"] == new.json()["id"]


async def test_delete_previous_or_expired_document_does_not_promote_or_remove_primary(clients):
    app, owner, _, guest = clients
    project, doc = await create_document(app, owner)
    async with app.state.sessions.begin() as db:
        original = await db.get(PresentationDocument, doc["id"])
        previous = PresentationDocument(
            project_id=project,
            user_id=original.user_id,
            draft=copy.deepcopy(original.draft),
            is_primary_offer=False,
        )
        legacy = PresentationDocument(
            project_id=project, user_id=original.user_id, draft={"settings": {"title": "Legacy"}, "items": []}
        )
        db.add_all([previous, legacy])
        await db.flush()
        identifiers = previous.id, legacy.id
    assert (
        await owner.request("DELETE", "/api/offers/" + identifiers[0], json={"version": 1, "confirmed": True})
    ).status_code == 200
    assert (
        await owner.request("DELETE", "/api/offers/" + identifiers[1], json={"version": 1, "confirmed": True})
    ).status_code == 404
    assert (await owner.get(f"/api/projects/{project}/offer")).json()["id"] == doc["id"]
    assert (await owner.get(f"/api/projects/{project}/offer-state")).json()["generation"] == 0
    await owner.post(
        f"/api/offers/{doc['id']}/publish",
        json={"version": doc["version"], "request_id": str(uuid4()), "share_epoch": 0},
    )
    await execute(app)
    ready = (await owner.get("/api/offers/" + doc["id"])).json()
    key = ready["share"]["local_preview_path"].rsplit("/", 1)[-1]
    app.state.presentations.offer_now = lambda: utcnow() + timedelta(days=15)
    assert (await guest.get("/api/public/offers/" + key)).status_code == 410
    # Link expiry itself retained the owner document, list and files.
    assert (await owner.get("/api/offers/" + doc["id"])).status_code == 200
    assert (await owner.get("/api/offers")).json()["items"][0]["publication_state"] == "EXPIRED"
    assert (
        await owner.request(
            "DELETE", "/api/offers/" + doc["id"], json={"version": doc["version"], "confirmed": True}
        )
    ).status_code == 200
    assert (await guest.get("/api/public/offers/" + key)).status_code == 404


async def test_late_worker_and_autosave_cannot_publish_or_reanimate_deleted_document(clients):
    app, owner, _, _ = clients
    _, doc = await create_document(app, owner)
    path = "/api/offers/" + doc["id"]
    action = {"version": doc["version"], "request_id": str(uuid4()), "share_epoch": 0}
    await owner.post(path + "/publish", json=action)
    row = await app.state.presentations.claim_next()
    assert row
    old = await app.state.offers.owned(doc["id"], (await owner.get("/api/auth/me")).json()["id"])
    assert (
        await owner.request("DELETE", path, json={"version": doc["version"], "confirmed": True})
    ).status_code == 200
    await app.state.presentations.publish(row, "READY", result={"client_snapshot": {}, "pdf": "untrusted"})
    await app.state.presentations.freeze_offer_assets(row, {"photo_original_keys": {}})
    with pytest.raises(ValueError, match="STATE_CONFLICT"):
        await app.state.presentations.replace_draft(old, old.version, old.draft)
    assert (await owner.post(path + "/publish", json=action)).status_code == 404
    async with app.state.sessions() as db:
        revision = await db.get(PresentationRevision, row.id)
        share = await db.get(CommercialOfferShare, doc["id"])
        assert revision.status == "CANCELLED" and revision.claim is None
        assert not share.active and share.epoch == 1 and not share.pending_revision_id
    assert await app.state.presentations.claim_next() is None
