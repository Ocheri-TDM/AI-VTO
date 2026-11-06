"""Independent money, provenance, publication and export truth, isolated storage/DB."""

import asyncio
import copy
import io
from contextlib import closing
from decimal import Decimal
from uuid import uuid4

import openpyxl
import pypdfium2 as pdfium
import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image
from sqlalchemy import select

from app.application.commercial_exports import chunks
from app.database.models import Base, CommercialOfferShare, PresentationDocument, PresentationRevision
from app.domain.commercial_offer import characteristics, line_amount, plain_text, price
from app.domain.models import Product, ProductColor
from app.main import create_app


def picture():
    data = io.BytesIO()
    Image.new("RGB", (240, 320), "#224f8c").save(data, format="PNG")
    return data.getvalue()


def values():
    return [
        Product(
            id="vto:offer-" + category,
            supplier="vto",
            source_url="https://vto.kz/product/offer-" + category + "/",
            name=name,
            original_price=Decimal("111.11"),
            original_currency="KZT",
            stock_quantity=500 if category != "bottle" else None,
            colors=[ProductColor(original_color="синий")],
            description="<p>Полное описание &amp; детали.</p><script>window.evil=1</script><p>Второй абзац.</p>",
            images=["https://vto.kz/wp-content/uploads/offer-" + category + ".png"],
            primary_image="https://vto.kz/wp-content/uploads/offer-" + category + ".png",
            metadata={
                "sku": sku,
                "attributes": {"Цвет": "красный,синий", "Материал": "Сталь", "Склад Москва": "9000"},
            },
        )
        for category, name, sku in [
            ("pen", "Ручка шариковая Offer", "0001-A.01"),
            ("backpack", "Рюкзак Offer", "00-B.02"),
            ("bottle", "Бутылка Offer", "00-C.03"),
        ]
    ]


def edit_body(document):
    return {
        "version": document["version"],
        "title": document["draft"]["settings"]["title"],
        "company": "Контроль",
        "use_logo": True,
        "use_banner": True,
        "items": [
            {k: i[k] for k in ["id", "title", "sku", "sku_note", "unit_price", "quantity", "main_image"]}
            for i in document["draft"]["items"]
        ],
    }


@pytest.mark.parametrize("value", ["0", "-1", "NaN", "Infinity", "1.005", 0, True, 1.1, "1000000001"])
def test_invalid_offer_price(value):
    with pytest.raises(ValueError):
        price(value)


def test_decimal_amount_and_client_text():
    assert line_amount("35224.51", 100) == "3522451.00"
    assert line_amount("0.01", 1) == "0.01"
    assert plain_text("<p>Текст <b>жирный</b></p><script>alert(1)</script>") == "Текст жирный"
    attributes = characteristics(
        {
            "colors": [{"original_color": "синий"}],
            "metadata": {
                "attributes": {"Цвет": "красный, синий", "Габариты": "10×20", "Остаток на складе": "999"}
            },
        }
    )
    assert {a["value"] for a in attributes} == {"синий", "10×20"}
    long = ("Строка текста\n" * 120) + ("ДлинноеСлово" * 350)
    assert "".join(chunks(long)) == long
    assert all(len(part) < 2000 for part in chunks(long))


@pytest.fixture
async def clients(settings):
    settings = settings.model_copy(update={"auth_required": True})
    app = create_app(settings, [])
    async with app.state.engine.begin() as db:
        await db.run_sync(Base.metadata.create_all)
    await app.state.index.ingest(values())
    async with (
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as owner,
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as other,
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as guest,
    ):
        for client, name in [(owner, "Offer owner"), (other, "Other owner")]:
            response = await client.post(
                "/api/auth/register", json={"name": name, "password": "isolated-password"}
            )
            assert response.status_code == 201
        yield app, owner, other, guest
    await app.state.engine.dispose()


async def create_document(app, owner):
    chat = (await owner.post("/api/chats", json={"project_name": "Offer test"})).json()
    project = next(p for p in (await owner.get("/api/projects")).json() if p["chat_id"] == chat["id"])["id"]
    for category, query in [
        ("pen", "ручки от 250 шт"),
        ("backpack", "рюкзаки от 100 шт"),
        ("bottle", "бутылки"),
    ]:
        response = await owner.post(f"/api/projects/{project}/requests", json={"query": query})
        assert response.status_code in (200, 202), response.text
        research = response.json()["research"]
        selected = await owner.post(
            f"/api/projects/{project}/collection/items",
            json={"offer_id": "vto:offer-" + category, "source_research_id": research["id"]},
        )
        assert selected.status_code == 200, selected.text
    collection = (await owner.get(f"/api/projects/{project}/collection")).json()
    duplicate = await owner.post(
        f"/api/projects/{project}/collection/items",
        json={"offer_id": "vto:offer-pen", "source_research_id": research["id"]},
    )
    assert duplicate.json()["version"] == collection["version"]
    response = await owner.post(
        f"/api/projects/{project}/offers", json={"collection_version": collection["version"]}
    )
    assert response.status_code == 201, response.text
    document = response.json()
    assert [i["quantity"] for i in document["draft"]["items"]] == [250, 100, 1]
    # Network-free fixture evidence for the supervised subprocess; never main catalog.
    root = app.state.presentations.root
    (root / "fixture.png").write_bytes(picture())
    async with app.state.sessions.begin() as db:
        row = await db.get(PresentationDocument, document["id"])
        draft = copy.deepcopy(row.draft)
        for item in draft["items"]:
            for photo in item["photos"]:
                photo["original_key"] = "fixture.png"
        row.draft = draft
    return project, (await owner.get("/api/offers/" + document["id"])).json()


async def execute(app):
    job = await app.state.presentations.claim_next()
    assert job
    await app.state.presentations.execute(job)
    async with app.state.sessions() as db:
        row = await db.get(PresentationRevision, job.id)
        assert row.status == "READY", row.error
        return row


async def test_full_private_public_revisions_exports_and_access(clients):
    app, owner, other, guest = clients
    project, document = await create_document(app, owner)
    identifier = document["id"]
    path = "/api/offers/" + identifier
    collection = (await owner.get(f"/api/projects/{project}/collection")).json()
    body = edit_body(document)
    body["items"][0].update(unit_price="35224.51", quantity=100)
    response = await owner.patch(path, json=body)
    assert response.status_code == 200, response.text
    document = response.json()
    assert document["draft"]["items"][0]["line_amount"] == "3522451.00"
    assert document["draft"]["items"][0]["catalog_price"] == "111.11"
    assert document["draft"]["items"][0]["price_source"] == "MANUAL"
    assert (await guest.get(path)).status_code == 401
    assert (await other.get(path)).status_code == 404
    assert (await owner.get("/api/presentations/" + identifier)).status_code == 404
    action = {"version": document["version"], "request_id": str(uuid4())}
    response = await owner.post(path + "/exports", json=action)
    assert response.status_code == 202, response.text
    private = await execute(app)
    async with app.state.sessions() as db:
        assert await db.get(CommercialOfferShare, identifier) is None
    pdf = await owner.get(f"{path}/revisions/{private.id}/pdf")
    assert pdf.status_code == 200
    with pdfium.PdfDocument(pdf.content) as pages:
        extracted = []
        for p in pages:
            with closing(p.get_textpage()) as text:
                extracted.append(text.get_text_range())
        pdf_text = "\n".join(extracted)
    for sku in ["0001-A.01", "00-B.02", "00-C.03"]:
        assert sku in pdf_text
    assert "3 522 451,00" in pdf_text and "₸" in pdf_text
    xlsx = await owner.get(f"{path}/revisions/{private.id}/xlsx")
    assert xlsx.status_code == 200
    book = openpyxl.load_workbook(io.BytesIO(xlsx.content), data_only=True)
    sheet = book.active
    assert sheet["C2"].value == "0001-A.01" and sheet["C2"].data_type == "s"
    assert Decimal(str(sheet["I2"].value)) == Decimal("3522451.00")
    assert sheet.protection.sheet and len(sheet._images) == 3
    assert book._external_links == [] and book.sheetnames == ["Предложение"]
    formulas = openpyxl.load_workbook(io.BytesIO(xlsx.content), data_only=False)
    assert formulas.active["I2"].value == "=ROUND(G2*H2,2)"
    assert not any("SUM(" in str(cell.value) for row in formulas.active for cell in row)
    action = {"version": document["version"], "request_id": str(uuid4()), "share_epoch": 0}
    published = (await owner.post(path + "/publish", json=action)).json()
    assert published["share"]["active"]
    token = published["share"]["local_preview_path"].split("/")[-1]
    public = "/api/public/offers/" + token
    response = await guest.get(public)
    assert response.status_code == 200, response.text
    value = response.json()
    assert value["revision_id"] == private.id
    forbidden = {
        "offer_id",
        "source_url",
        "catalog_price",
        "price_source",
        "quantity_context",
        "user_id",
        "stock",
        "asset_keys",
        "storage",
        "encrypted_token",
    }

    def check_clean(v):
        if isinstance(v, dict):
            assert not forbidden.intersection(v)
            for item in v.values():
                check_clean(item)
        elif isinstance(v, list):
            for item in v:
                check_clean(item)

    check_clean(value)
    assert (
        response.headers["cache-control"].startswith("private, no-store")
        and response.headers["referrer-policy"] == "no-referrer"
    )
    assert value["items"][0]["line_amount"] == "3522451.00"
    assert (await guest.get(value["pdf_url"])).content == pdf.content
    assert (await guest.get(value["xlsx_url"])).content == xlsx.content
    assert (await other.get(public)).status_code == 200
    assert (await other.patch(path, json=body)).status_code == 404
    assert (await guest.patch(public, json=body)).status_code == 405
    assert (await guest.get("/api/public/offers/" + "x" * 43)).status_code == 404
    repeated = await owner.post(path + "/publish", json=action)
    assert repeated.json()["share"]["local_preview_path"] == published["share"]["local_preview_path"]
    async with app.state.sessions() as db:
        assert (
            len(
                (
                    await db.scalars(
                        select(PresentationRevision).where(PresentationRevision.document_id == identifier)
                    )
                ).all()
            )
            == 1
        )
    body = edit_body(published)
    body["items"][0]["quantity"] = 1
    changed = await owner.patch(path, json=body)
    assert changed.status_code == 200
    document = changed.json()
    assert (await guest.get(public)).json() == value
    action = {"version": document["version"], "request_id": str(uuid4()), "share_epoch": 0}
    update = await owner.post(path + "/publish", json=action)
    assert update.status_code == 202, update.text
    assert (await guest.get(public)).json() == value
    next_revision = await execute(app)
    current = (await guest.get(public)).json()
    assert current["revision_id"] == next_revision.id and current["items"][0]["line_amount"] == "35224.51"
    assert (await guest.get(value["pdf_url"])).status_code == 409
    current_owner = (await owner.get(path)).json()
    revoke = {"version": current_owner["version"], "epoch": current_owner["share"]["epoch"]}
    revoked = await owner.post(path + "/revoke", json=revoke)
    assert revoked.status_code == 200
    for url in [
        public,
        current["pdf_url"],
        current["xlsx_url"],
        current["logo_url"],
        current["items"][0]["images"][0]["url"],
    ]:
        denied = await guest.get(url)
        assert denied.status_code == 404
        assert "no-store" in denied.headers["cache-control"]
    stale = await owner.post(
        path + "/publish",
        json={"version": current_owner["version"], "request_id": str(uuid4()), "share_epoch": 0},
    )
    assert stale.status_code == 409
    rotated = await owner.post(path + "/rotate", json={"version": current_owner["version"], "epoch": 1})
    assert rotated.status_code == 200
    new = rotated.json()["share"]["local_preview_path"].split("/")[-1]
    assert (
        new != token
        and (await guest.get(public)).status_code == 404
        and (await guest.get("/api/public/offers/" + new)).status_code == 200
    )
    assert (await owner.get(f"/api/projects/{project}/collection")).json() == collection


async def test_pending_publication_cannot_undo_revoke_and_cas(clients):
    app, owner, _, guest = clients
    _, document = await create_document(app, owner)
    path = "/api/offers/" + document["id"]
    body = edit_body(document)
    second = copy.deepcopy(body)
    second["title"] = "Другая вкладка"
    results = await asyncio.gather(owner.patch(path, json=body), owner.patch(path, json=second))
    assert sorted(r.status_code for r in results) == [200, 409]
    document = (await owner.get(path)).json()
    action = {"version": document["version"], "request_id": str(uuid4()), "share_epoch": 0}
    results = await asyncio.gather(
        owner.post(path + "/publish", json=action), owner.post(path + "/publish", json=action)
    )
    assert all(r.status_code == 202 for r in results), [
        (r.status_code, r.json().get("detail") if r.status_code != 202 else "accepted") for r in results
    ]
    assert len((await owner.get(path)).json()["revisions"]) == 1
    response = await owner.post(path + "/revoke", json={"version": document["version"], "epoch": 0})
    assert response.status_code == 200
    await execute(app)
    current = (await owner.get(path)).json()
    assert not current["share"]["active"] and current["share"]["published_revision_id"] is None
    # Replaying an old action must not reactivate its capability after cancellation.
    assert (await owner.post(path + "/publish", json=action)).status_code == 202
    assert not (await owner.get(path)).json()["share"]["active"]
    assert (await guest.get(path)).status_code == 401


async def test_validation_upload_failure_refresh_and_long_text(clients):
    app, owner, _, _ = clients
    _, document = await create_document(app, owner)
    path = "/api/offers/" + document["id"]
    body = edit_body(document)
    body["items"][0].update(sku="", unit_price=None)
    document = (await owner.patch(path, json=body)).json()
    assert {i["code"] for i in document["issues"]} >= {"SKU_REQUIRED", "PRICE_REQUIRED"}
    action = {"version": document["version"], "request_id": str(uuid4())}
    assert (await owner.post(path + "/publish", json=action)).status_code == 422
    body = edit_body(document)
    body["items"][0].update(sku="=001-TRUE", unit_price="35224.51")
    assert (await owner.patch(path, json=body)).status_code == 422
    body["items"][0]["sku_note"] = "Подтверждено поставщиком"
    document = (await owner.patch(path, json=body)).json()
    bad = await owner.post(
        path + "/logo", params={"version": document["version"]}, content=b"<svg onload='alert(1)'></svg>"
    )
    assert bad.status_code == 422 and (await owner.get(path)).json()["version"] == document["version"]
    refreshed = await owner.post(
        path + "/refresh", json={"version": document["version"], "request_id": str(uuid4())}
    )
    assert refreshed.status_code == 200
    document = refreshed.json()
    assert (
        document["draft"]["items"][0]["unit_price"] == "35224.51"
        and document["draft"]["items"][0]["sku"] == "=001-TRUE"
    )
    long = "\n\n".join(
        f"Раздел {i:03d}: Полное описание изделия, материал и применение без сокращения." for i in range(90)
    )
    async with app.state.sessions.begin() as db:
        row = await db.get(PresentationDocument, document["id"])
        draft = copy.deepcopy(row.draft)
        draft["items"][0]["description"] = long
        for item in draft["items"]:
            for photo in item["photos"]:
                photo["original_key"] = "fixture.png"
        row.draft = draft
    action = {"version": document["version"], "request_id": str(uuid4())}
    assert (await owner.post(path + "/exports", json=action)).status_code == 202
    revision = await execute(app)
    result = await owner.get(f"{path}/revisions/{revision.id}/xlsx")
    book = openpyxl.load_workbook(io.BytesIO(result.content), data_only=False)
    assert book.active["C2"].data_type == "s" and book.active["C2"].value == "=001-TRUE"
    description = "".join(
        row[4].value or ""
        for row in book.active.iter_rows(min_row=2)
        if row[3].value and (row[2].value == "=001-TRUE" or str(row[3].value).endswith("— продолжение"))
    )
    assert description == long
    result = await owner.get(f"{path}/revisions/{revision.id}/pdf")
    with pdfium.PdfDocument(result.content) as pages:
        strings = []
        assert len(pages) > 4
        for page in pages:
            with closing(page.get_textpage()) as extracted:
                strings.append(extracted.get_text_range())
    assert "Раздел 089" in "\n".join(strings)
    assert sum(s.count("Стоимость позиции") for s in strings) == 3


async def test_failed_update_preserves_publication_and_retry_uses_frozen_input(clients):
    app, owner, _, guest = clients
    _, document = await create_document(app, owner)
    path = "/api/offers/" + document["id"]
    action = {"version": document["version"], "request_id": str(uuid4()), "share_epoch": 0}
    await owner.post(path + "/publish", json=action)
    first = await execute(app)
    document = (await owner.get(path)).json()
    token = document["share"]["local_preview_path"].split("/")[-1]
    public = "/api/public/offers/" + token
    previous = (await guest.get(public)).json()
    body = edit_body(document)
    body["items"][0]["quantity"] = 12
    document = (await owner.patch(path, json=body)).json()
    action = {"version": document["version"], "request_id": str(uuid4()), "share_epoch": 0}
    assert (await owner.post(path + "/publish", json=action)).status_code == 202
    job = await app.state.presentations.claim_next()
    async with app.state.sessions.begin() as db:
        row = await db.get(PresentationRevision, job.id)
        payload = copy.deepcopy(row.input)
        payload["resolved_logo"]["key"] = "temporarily-missing-logo.png"
        row.input = payload
        job.input = payload
    await app.state.presentations.execute(job)
    document = (await owner.get(path)).json()
    assert document["revisions"][0]["status"] == "FAILED"
    assert (await guest.get(public)).json() == previous
    assert (await guest.get(previous["pdf_url"])).status_code == 200
    from app.application.presentation_logo import OFFER_LOGO

    (app.state.presentations.root / "temporarily-missing-logo.png").write_bytes(OFFER_LOGO.read_bytes())
    retried = await owner.post(path + "/publish", json=action)
    assert retried.status_code == 202 and len(retried.json()["revisions"]) == 2
    second = await execute(app)
    assert second.id == job.id and second.attempts == 2 and second.input == payload
    assert (await guest.get(public)).json()["revision_id"] == second.id != first.id


def test_capability_key_recovery_and_trusted_origin(tmp_path):
    from app.application.offer_tokens import TokenVault, public_origin

    first = TokenVault(tmp_path)
    token, digest, encrypted = first.create()
    assert len(token) == 43 and token not in encrypted
    assert TokenVault(tmp_path).reveal(encrypted) == token
    assert TokenVault.digest(token) == digest
    assert public_origin(None) is None
    assert public_origin("https://offers.example.com/") == "https://offers.example.com"
    for invalid in [
        "http://example.com",
        "https://localhost",
        "https://127.0.0.1",
        "https://user:pass@example.com",
        "https://example.com/offer",
    ]:
        with pytest.raises(ValueError):
            public_origin(invalid)


def test_user_supplied_vto_logo_and_legacy_frozen_logo(tmp_path):
    import hashlib

    from app.application.commercial_offers import logo_background
    from app.application.presentation_logo import (
        DEFAULT_SHA256,
        OFFER_LOGO,
        OFFER_LOGO_SHA256,
        freeze_logo,
        frozen_logo_path,
    )

    offer = freeze_logo(tmp_path, {}, default_logo=OFFER_LOGO, default_sha256=OFFER_LOGO_SHA256)
    legacy = freeze_logo(tmp_path, {})
    assert offer["hash"] == OFFER_LOGO_SHA256 and legacy["hash"] == DEFAULT_SHA256
    assert frozen_logo_path(tmp_path, offer).read_bytes() == OFFER_LOGO.read_bytes()
    assert logo_background(OFFER_LOGO) == "#ffffff"
    assert hashlib.sha256(frozen_logo_path(tmp_path, legacy).read_bytes()).hexdigest() == DEFAULT_SHA256
    custom = freeze_logo(
        tmp_path,
        {"use_logo": True, "logo": legacy["key"]},
        default_logo=OFFER_LOGO,
        default_sha256=OFFER_LOGO_SHA256,
    )
    assert custom["source"] == "custom" and custom["hash"] == DEFAULT_SHA256
    with pytest.raises(ValueError, match="клиентский логотип"):
        freeze_logo(
            tmp_path,
            {"use_logo": True, "logo": "missing.png"},
            default_logo=OFFER_LOGO,
            default_sha256=OFFER_LOGO_SHA256,
        )


async def test_selection_quantity_local_global_strict_and_additional(repository, settings):
    from test_product_grid import service

    from app.database.conversations import ConversationRepository
    from app.domain.selection_quantity import selection_quantity

    g = service(repository, settings)
    await g.index.ingest(values())
    chat = await ConversationRepository(repository.sessions).create("Quantity provenance")
    r = await g.start(chat, "Ручки больше 250 шт и рюкзаки от 100 шт")
    assert selection_quantity(r.model_dump(mode="json"), "vto:offer-pen")["quantity"] == 251
    assert selection_quantity(r.model_dump(mode="json"), "vto:offer-backpack")["quantity"] == 100
    r = await g.start(chat, "Ручки и рюкзаки. От 200 шт.")
    assert selection_quantity(r.model_dump(mode="json"), "vto:offer-pen")["quantity"] == 200
    r = await g.grid_change(r.id, version=r.view.version, filters=type(r.view.filters)(minimum_quantity=300))
    assert selection_quantity(r.model_dump(mode="json"), "vto:offer-pen")["quantity"] == 300
    assert selection_quantity({"invalid": "old snapshot"})["rule"] == "HISTORICAL_CONTEXT_UNAVAILABLE"


async def test_same_family_colors_own_photos_unknown_stock_and_shortage_warning(clients):
    app, owner, _, _ = clients
    variants = []
    for n, color in enumerate(("синий", "красный")):
        variants.append(
            Product(
                id=f"vto:color-{n}",
                supplier="vto",
                name="Рюкзак ColorFixture",
                source_url="https://vto.kz/product/colors/",
                original_price=Decimal("123.45"),
                original_currency="KZT",
                stock_quantity=12 if n == 0 else None,
                colors=[ProductColor(original_color=color)],
                images=[
                    f"https://vto.kz/wp-content/uploads/color-{n}-main.png",
                    f"https://vto.kz/wp-content/uploads/color-{n}-detail.png",
                ],
                metadata={"sku": f"000{n}-V", "vto_product_id": 12345, "vto_variation_id": 12346 + n},
            )
        )
    await app.state.index.ingest(variants)
    chat = (await owner.post("/api/chats", json={"project_name": "Color fixtures"})).json()
    project = next(p for p in (await owner.get("/api/projects")).json() if p["chat_id"] == chat["id"])["id"]
    r = (await owner.post(f"/api/projects/{project}/requests", json={"query": "рюкзаки"})).json()["research"]
    for p in variants:
        response = await owner.post(
            f"/api/projects/{project}/collection/items",
            json={"offer_id": p.id, "source_research_id": r["id"]},
        )
        assert response.status_code == 200
    collection = response.json()
    document = (
        await owner.post(
            f"/api/projects/{project}/offers", json={"collection_version": collection["version"]}
        )
    ).json()
    items = document["draft"]["items"]
    assert len(items) == 2 and [i["sku"] for i in items] == ["0000-V", "0001-V"]
    assert len({i["product_id"] for i in items}) == 1
    assert [{a["value"] for a in i["characteristics"] if a["name"] == "Цвет"} for i in items] == [
        {"синий"},
        {"красный"},
    ]
    assert items[1]["availability"] == "Наличие уточняется"
    body = edit_body(document)
    body["items"][0].update(quantity=100, main_image=items[0]["photos"][1]["id"])
    response = await owner.patch("/api/offers/" + document["id"], json=body)
    assert response.status_code == 200
    changed = response.json()["draft"]["items"]
    assert changed[0]["quantity"] == 100 and changed[0]["warnings"]
    assert (
        changed[0]["main_image"] == items[0]["photos"][1]["id"]
        and changed[1]["main_image"] == items[1]["main_image"]
    )
    assert changed[0]["photos"][1]["source_url"].endswith("color-0-detail.png")
    assert (await owner.get(f"/api/projects/{project}/collection")).json() == collection
