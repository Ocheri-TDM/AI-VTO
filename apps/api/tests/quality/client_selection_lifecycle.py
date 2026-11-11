"""Real publication/expiry/rotation/deletion of one exact disposable offer."""

from datetime import timedelta
from uuid import uuid4

import httpx
from check_chat_workflow import call, save
from check_commercial_offers import edit_body, ready
from sqlalchemy import select

from app.config import Settings
from app.database.connection import create_database
from app.database.models import CommercialOfferShare, PresentationDocument
from app.domain.models import utcnow
from quality.client_selection_http import PUBLIC, choose, headers


async def run(state, root, ledger, public):
    offer = dict(state["offers"][1])
    assert offer["document"] in state["documents"]
    checks = []

    def check(label, valid):
        checks.append({"check": label, "status": "PASS" if valid else "FAIL"})
        save(public / "lifecycle.json", {"path": PUBLIC, "checks": checks})
        assert valid, label

    engine, sessions = create_database(Settings().database_url)
    try:
        async with (
            httpx.AsyncClient(base_url=PUBLIC, cookies=state["users"][0]["cookies"], timeout=90) as owner,
            httpx.AsyncClient(base_url=PUBLIC, timeout=90) as guest,
        ):
            path = "/api/offers/" + offer["document"]
            doc = await call(owner, "GET", path)
            own = await call(
                guest,
                "POST",
                offer["path"] + "/selection/session",
                headers=headers(),
                json={"published_revision_id": offer["revision"]},
            )
            selected_item = doc["draft"]["items"][0]
            own = (await choose(guest, offer, own, selected_item["id"])).json()
            body = edit_body(doc)
            body["items"].reverse()
            next(i for i in body["items"] if i["id"] == selected_item["id"]).update(
                unit_price="8765.43", quantity=7
            )
            doc = await call(owner, "PATCH", path, json=body)
            await call(
                owner,
                "POST",
                path + "/publish",
                json={
                    "version": doc["version"],
                    "request_id": str(uuid4()),
                    "share_epoch": doc["share"]["epoch"],
                },
            )
            doc, revision = await ready(owner, path, doc["version"])
            stale = await choose(guest, offer, own, selected_item["id"], False)
            check(
                "actual republish rejects stale guest revision",
                stale.status_code == 409 and stale.json()["detail"]["code"] == "REVISION_CHANGED",
            )
            restored = await call(guest, "GET", offer["path"] + "/selection")
            check(
                "reordering keeps the same stable row and original selected revision",
                restored["selected_items"] == own["selected_items"]
                and restored["published_revision_id"] == revision["id"],
            )
            feedback = await call(owner, "GET", path + "/client-selection")
            old = [i for v in feedback["visitors"] for i in v["items"] if i["item_id"] == selected_item["id"]]
            check(
                "manager sees original commercial terms and changed-conditions warning",
                any(
                    i["conditions_changed"]
                    and i["unit_price"] == selected_item["unit_price"]
                    and i["quantity"] == selected_item["quantity"]
                    for i in old
                ),
            )
            body = edit_body(doc)
            body["items"] = [i for i in body["items"] if i["id"] != selected_item["id"]]
            doc = await call(owner, "PATCH", path, json=body)
            await call(
                owner,
                "POST",
                path + "/publish",
                json={
                    "version": doc["version"],
                    "request_id": str(uuid4()),
                    "share_epoch": doc["share"]["epoch"],
                },
            )
            doc, revision = await ready(owner, path, doc["version"])
            restored = await call(guest, "GET", offer["path"] + "/selection")
            check(
                "removed position never moves to its new array index",
                restored["selected_items"] == [] and restored["previous_item_count"] == 1,
            )
            feedback = await call(owner, "GET", path + "/client-selection")
            check(
                "removed position survives in manager history",
                any(
                    i["item_id"] == selected_item["id"] and i["removed"]
                    for v in feedback["visitors"]
                    for i in v["items"]
                ),
            )
            # A controlled clock fixture for this exact task-owned share only.
            async with sessions.begin() as db:
                document = await db.scalar(
                    select(PresentationDocument)
                    .where(
                        PresentationDocument.id == offer["document"],
                        PresentationDocument.user_id == state["users"][0]["id"],
                    )
                    .with_for_update()
                )
                assert document and document.id in state["documents"]
                share = await db.get(CommercialOfferShare, document.id, with_for_update=True)
                share.activated_at, share.expires_at = (
                    utcnow() - timedelta(days=15),
                    utcnow() - timedelta(days=1),
                )
            check(
                "expired real share refuses new guest reads",
                (await guest.get(offer["path"] + "/selection")).status_code == 410,
            )
            check(
                "expired real share refuses new writes",
                (await choose(guest, offer, restored, offer["items"][1])).status_code == 410,
            )
            check(
                "expiry retains authorized manager history",
                (await owner.get(path + "/client-selection")).status_code == 200,
            )
            doc = await call(owner, "GET", path)
            rotated = await call(
                owner,
                "POST",
                path + "/rotate",
                json={"version": doc["version"], "epoch": doc["share"]["epoch"]},
            )
            oldpath = offer["path"]
            offer["path"] = "/api/public/offers/" + rotated["share"]["local_preview_path"].split("/")[-1]
            check(
                "old capability is invalid after rotation",
                (await guest.get(oldpath + "/selection")).status_code == 404,
            )
            fresh = await call(guest, "GET", offer["path"] + "/selection")
            check(
                "new generation never inherits prior browser session",
                not fresh["initialized"] and not fresh["selected_items"],
            )
            check(
                "prior csrf cannot write to new generation",
                (
                    await choose(guest, offer, {**fresh, "csrf_token": own["csrf_token"]}, offer["items"][1])
                ).status_code
                == 401,
            )
            fresh = await call(
                guest,
                "POST",
                offer["path"] + "/selection/session",
                headers=headers(),
                json={"published_revision_id": revision["id"]},
            )
            doc = await call(owner, "GET", path)
            await call(owner, "DELETE", path, json={"version": doc["version"], "confirmed": True})
            check(
                "deleted share blocks a previously open visitor session",
                (await choose(guest, offer, fresh, offer["items"][1])).status_code == 404,
            )
            check(
                "deleted document disappears from owner notifications",
                all(
                    n["document_id"] != offer["document"]
                    for n in (await call(owner, "GET", "/api/notifications"))["items"]
                ),
            )
            check("late events never recreate owner offer access", (await owner.get(path)).status_code == 404)
        print("Real Vercel/PG revision and lifecycle checks:", len(checks))
    finally:
        await engine.dispose()
