"""Controlled UTC boundaries, immutable publication, persistent project editor."""

import asyncio
import copy
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from test_commercial_offers import clients as _clients
from test_commercial_offers import create_document, edit_body, execute

from app.database.models import (
    CommercialOfferRequest,
    CommercialOfferShare,
    PresentationDocument,
    PresentationRevision,
)
from app.domain.offer_lifecycle import primary_rank

clients = _clients


async def test_one_project_one_editor_preserves_changes_and_archives(clients):
    app, owner, other, _ = clients
    project, doc = await create_document(app, owner)
    path = "/api/offers/" + doc["id"]
    body = edit_body(doc)
    body["items"][0].update(
        unit_price="35224.51", quantity=12, sku="000-OVERRIDE", sku_note="Письмо поставщика"
    )
    body["items"] = list(reversed(body["items"][:-1]))
    saved = (await owner.patch(path, json=body)).json()
    for _ in range(3):
        r = await owner.post(f"/api/projects/{project}/offers", json={"collection_version": 0})
        assert r.status_code == 200 and r.json()["id"] == doc["id"]
        assert r.json()["draft"] == saved["draft"] and not r.json()["share"]["active"]
        assert (await owner.get(f"/api/projects/{project}/offer")).json()["id"] == doc["id"]
    assert (await other.get(f"/api/projects/{project}/offer")).status_code == 404
    async with app.state.sessions.begin() as db:
        archived = PresentationDocument(
            project_id=project, user_id=saved["project_id"], draft=copy.deepcopy(saved["draft"])
        )
        original = await db.get(PresentationDocument, doc["id"])
        archived.user_id = original.user_id
        db.add(archived)
        await db.flush()
        archived_id = archived.id
    value = (await owner.get("/api/offers/" + archived_id)).json()
    assert not value["is_primary"] and value["primary_offer_id"] == doc["id"]
    assert (await owner.patch("/api/offers/" + archived_id, json=edit_body(value))).status_code == 409
    assert len((await owner.get(f"/api/projects/{project}/offers")).json()) == 2


async def test_queue_time_expiry_update_late_worker_and_renewal_receipt(clients):
    app, owner, _, guest = clients
    project, doc = await create_document(app, owner)
    path = "/api/offers/" + doc["id"]
    now = datetime(2026, 11, 1, 8, tzinfo=UTC)
    app.state.presentations.offer_now = lambda: now
    first_action = {"version": doc["version"], "request_id": str(uuid4()), "share_epoch": 0}
    r = await owner.post(path + "/publish", json=first_action)
    assert r.status_code == 202 and r.json()["share"]["expires_at"] is None
    now += timedelta(days=2)  # Waiting must not consume the inaccessible link's lifetime.
    first = await execute(app)
    doc = (await owner.get(path)).json()
    deadline = now + timedelta(days=7)
    assert datetime.fromisoformat(doc["share"]["activated_at"]) == now
    assert datetime.fromisoformat(doc["share"]["expires_at"]) == deadline
    token = doc["share"]["local_preview_path"].split("/")[-1]
    public = "/api/public/offers/" + token
    value = (await guest.get(public)).json()
    async with app.state.sessions() as db:
        counts = [
            await db.scalar(select(func.count()).select_from(t))
            for t in (CommercialOfferRequest, CommercialOfferShare, PresentationRevision)
        ]
    for _ in range(10):
        assert (await owner.get(path)).json()["share"]["expires_at"] == doc["share"]["expires_at"]
    async with app.state.sessions() as db:
        assert counts == [
            await db.scalar(select(func.count()).select_from(t))
            for t in (CommercialOfferRequest, CommercialOfferShare, PresentationRevision)
        ]
    body = edit_body(doc)
    body["items"][0]["quantity"] = 101
    doc = (await owner.patch(path, json=body)).json()
    now = deadline - timedelta(seconds=1)
    for url in [
        public,
        value["pdf_url"],
        value["xlsx_url"],
        value["logo_url"],
        value["vto_logo_url"],
        value["items"][0]["images"][0]["url"],
    ]:
        assert (await guest.get(url)).status_code == 200
    update = {"version": doc["version"], "request_id": str(uuid4()), "share_epoch": 0}
    assert (await owner.post(path + "/publish", json=update)).status_code == 202
    now = deadline
    for url in [
        public,
        value["pdf_url"],
        value["xlsx_url"],
        value["logo_url"],
        value["vto_logo_url"],
        value["items"][0]["images"][0]["url"],
    ]:
        expired = await guest.get(url)
        assert expired.status_code == 410 and "no-store" in expired.headers["cache-control"]
        assert expired.json()["detail"]["code"] == "OFFER_LINK_EXPIRED"
    assert (await guest.get("/api/branding/vto-logo.png")).status_code == 200
    await execute(app)  # Old update completed after expiry; it must remain private.
    doc = (await owner.get(path)).json()
    assert doc["share"]["expired"] and not doc["share"]["active"]
    assert doc["share"]["published_revision_id"] == first.id
    assert doc["draft"]["items"][0]["quantity"] == 101
    assert (await owner.get(doc["revisions"][0]["pdf_url"])).status_code == 200
    assert (await owner.post(path + "/publish", json=first_action)).status_code == 202
    assert not (await owner.get(path)).json()["share"]["active"]
    now += timedelta(seconds=1)
    renewed = {"version": doc["version"], "request_id": str(uuid4()), "share_epoch": 0}
    response = await owner.post(path + "/publish", json=renewed)
    assert response.status_code == 202
    doc = response.json()
    assert doc["share"]["active"] and doc["share"]["epoch"] == 1
    assert datetime.fromisoformat(doc["share"]["activated_at"]) == now
    assert datetime.fromisoformat(doc["share"]["expires_at"]) == now + timedelta(days=7)
    assert doc["share"]["local_preview_path"].split("/")[-1] != token
    assert (await guest.get(public)).status_code == 404
    second_deadline = now + timedelta(days=7)
    now = second_deadline
    assert (await owner.post(path + "/publish", json=renewed)).status_code == 202
    repeated = (await owner.get(path)).json()
    assert repeated["share"]["expired"] and repeated["share"]["epoch"] == 1
    assert datetime.fromisoformat(repeated["share"]["expires_at"]) == second_deadline


async def test_read_absence_is_not_creation_and_simultaneous_create(clients):
    app, owner, _, _ = clients
    chat = (await owner.post("/api/chats", json={"project_name": "one editor"})).json()
    project = next(p for p in (await owner.get("/api/projects")).json() if p["chat_id"] == chat["id"])["id"]
    assert (await owner.get(f"/api/projects/{project}/offer")).json() is None
    research = (await owner.post(f"/api/projects/{project}/requests", json={"query": "ручки"})).json()[
        "research"
    ]
    collection = (
        await owner.post(
            f"/api/projects/{project}/collection/items",
            json={"offer_id": "vto:offer-pen", "source_research_id": research["id"]},
        )
    ).json()
    results = await asyncio.gather(
        *[
            owner.post(f"/api/projects/{project}/offers", json={"collection_version": collection["version"]})
            for _ in range(2)
        ]
    )
    assert sorted(r.status_code for r in results) == [200, 201]
    assert len({r.json()["id"] for r in results}) == 1


async def test_composition_is_explicit_preserves_manual_and_exclusions(clients):
    app, owner, _, _ = clients
    project, doc = await create_document(app, owner)
    path = "/api/offers/" + doc["id"]
    body = edit_body(doc)
    body["items"][0].update(unit_price="987.65", quantity=13)
    body["items"] = body["items"][:-1]  # Excluded bottle remains selected in the project.
    doc = (await owner.patch(path, json=body)).json()
    preview = (await owner.get(path + "/composition")).json()
    assert not preview["added"] and preview["excluded_retained"] == 1
    assert (await owner.get(path)).json()["version"] == doc["version"]
    await owner.delete(f"/api/projects/{project}/collection/items/vto:offer-backpack")
    preview = (await owner.get(path + "/composition")).json()
    assert len(preview["removed"]) == 1
    r = await owner.post(
        path + "/composition",
        json={
            "version": doc["version"],
            "collection_version": preview["collection_version"],
            "confirmed": True,
        },
    )
    assert r.status_code == 200
    rows = r.json()["draft"]["items"]
    assert len(rows) == 1 and rows[0]["unit_price"] == "987.65" and rows[0]["quantity"] == 13
    research = (
        await owner.post(f"/api/projects/{project}/requests", json={"query": "рюкзаки от 80 шт"})
    ).json()["research"]
    await owner.post(
        f"/api/projects/{project}/collection/items",
        json={
            "offer_id": "vto:offer-backpack",
            "source_research_id": research["id"],
        },
    )
    delta = (await owner.get(path + "/composition")).json()
    assert len(delta["added"]) == 1 and delta["retained"] == 1 and delta["excluded_retained"] == 1
    result = await owner.post(
        path + "/composition",
        json={
            "version": delta["version"],
            "collection_version": delta["collection_version"],
            "confirmed": True,
        },
    )
    assert result.status_code == 200
    values = result.json()["draft"]["items"]
    assert values[0]["unit_price"] == "987.65" and values[0]["quantity"] == 13
    assert values[1]["offer_id"] == "vto:offer-backpack" and values[1]["quantity"] == 80
    assert all(i["offer_id"] != "vto:offer-bottle" for i in values)


def test_manual_populated_document_wins_over_later_copies():
    base = {
        "id": "a",
        "version": 1,
        "updated_at": datetime(2026, 1, 1, tzinfo=UTC),
        "draft": {"items": [{"price_source": "MANUAL"}]},
    }
    untouched = copy.deepcopy(base) | {
        "id": "b",
        "updated_at": datetime(2026, 11, 1, tzinfo=UTC),
        "draft": {"items": [{}]},
    }
    empty = untouched | {"draft": {"items": []}, "version": 20}
    assert primary_rank(base) > primary_rank(untouched, True) > primary_rank(empty)


async def test_missing_managed_key_is_not_recreated_for_existing_shares(clients):
    app, owner, _, _ = clients
    _, doc = await create_document(app, owner)
    path = "/api/offers/" + doc["id"]
    await owner.post(path + "/publish", json={"version": doc["version"], "request_id": str(uuid4())})
    from app.application.commercial_offers import CommercialOfferService

    key = app.state.presentations.root / "keys/commercial-offers.key"
    data = key.read_bytes()
    key.unlink()
    new = CommercialOfferService(app.state.presentations)
    with pytest.raises(ValueError, match="резервной копии"):
        await new.ensure_vault()
    assert not key.exists()
    key.write_bytes(data)


async def test_concurrent_logo_publication_keeps_path_guard(tmp_path):
    from app.application.presentation_assets import safe_path
    from app.application.presentation_logo import OFFER_LOGO, OFFER_LOGO_SHA256, freeze_logo

    for n in range(5):
        root = tmp_path / str(n)
        roots = await asyncio.gather(
            *[
                asyncio.to_thread(
                    freeze_logo, root, {}, default_logo=OFFER_LOGO, default_sha256=OFFER_LOGO_SHA256
                )
                for _ in range(8)
            ]
        )
        assert all(r["hash"] == OFFER_LOGO_SHA256 for r in roots)
        assert safe_path(root, roots[0]["key"]).read_bytes() == OFFER_LOGO.read_bytes()
        with pytest.raises(ValueError):
            safe_path(root, "../foreign.png")


def test_lifecycle_migration_preserves_manual_duplicate_and_bounds_old_links():
    import importlib.util
    from pathlib import Path

    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    engine = sa.create_engine("sqlite://")
    meta = sa.MetaData()
    docs = sa.Table(
        "presentation_documents",
        meta,
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("project_id", sa.String),
        sa.Column("version", sa.Integer),
        sa.Column("draft", sa.JSON),
        sa.Column("created_at", sa.DateTime),
        sa.Column("updated_at", sa.DateTime),
    )
    revisions = sa.Table(
        "presentation_revisions",
        meta,
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("document_id", sa.String),
        sa.Column("status", sa.String),
        sa.Column("input", sa.JSON),
        sa.Column("created_at", sa.DateTime),
        sa.Column("updated_at", sa.DateTime),
    )
    shares = sa.Table(
        "commercial_offer_shares",
        meta,
        sa.Column("document_id", sa.String, primary_key=True),
        sa.Column("epoch", sa.Integer),
        sa.Column("active", sa.Boolean),
        sa.Column("published_revision_id", sa.String),
        sa.Column("pending_revision_id", sa.String),
        sa.Column("updated_at", sa.DateTime),
    )
    meta.create_all(engine)
    timestamp = datetime(2026, 1, 1)
    manual = {
        "kind": "COMMERCIAL_OFFER",
        "items": [{"price_source": "MANUAL", "unit_price": "999.99", "quantity": 13}],
    }
    with engine.begin() as c:
        for identifier, draft, version in [
            ("manual", manual, 3),
            ("later", {"kind": "COMMERCIAL_OFFER", "items": [{}]}, 1),
            ("empty", {"kind": "COMMERCIAL_OFFER", "items": []}, 10),
            ("legacy", {"items": [{}]}, 7),
        ]:
            c.execute(
                docs.insert().values(
                    id=identifier,
                    project_id="p",
                    draft=draft,
                    version=version,
                    created_at=timestamp,
                    updated_at=timestamp,
                )
            )
        c.execute(
            revisions.insert().values(
                id="r",
                document_id="later",
                status="READY",
                input={"kind": "COMMERCIAL_OFFER"},
                created_at=timestamp,
                updated_at=timestamp + timedelta(seconds=5),
            )
        )
        c.execute(
            shares.insert().values(
                document_id="later",
                epoch=0,
                active=True,
                published_revision_id="r",
                updated_at=timestamp + timedelta(seconds=5),
            )
        )
        c.execute(
            shares.insert().values(
                document_id="manual", epoch=1, active=False, published_revision_id=None, updated_at=timestamp
            )
        )
        spec = importlib.util.spec_from_file_location(
            "lifecycle_migration",
            Path(__file__).resolve().parents[1] / "alembic/versions/0025_offer_lifecycle.py",
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with Operations.context(MigrationContext.configure(c)):
            module.upgrade()
        assert (
            c.execute(sa.text("SELECT id FROM presentation_documents WHERE is_primary_offer")).scalar_one()
            == "manual"
        )
        assert c.execute(sa.select(docs.c.draft).where(docs.c.id == "manual")).scalar_one() == manual
        values = c.execute(
            sa.text(
                "SELECT activated_at,expires_at,activation_source,active FROM commercial_offer_shares WHERE document_id='later'"
            )
        ).one()
        assert datetime.fromisoformat(values.expires_at) - datetime.fromisoformat(
            values.activated_at
        ) == timedelta(days=7)
        assert values.activation_source == "CONFIRMED_FIRST_READY" and values.active
        assert not c.execute(sa.select(shares.c.active).where(shares.c.document_id == "manual")).scalar_one()
    engine.dispose()
