import copy
import hashlib
import io
from contextlib import closing
from datetime import timedelta

import pypdfium2 as pdfium
import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image, ImageDraw
from sqlalchemy import update

from app.application.presentation_assets import (
    AssetPreparer,
    decode_image,
    safe_path,
    store_upload,
    transparent,
    valid_source,
)
from app.application.presentation_render import luminance, plan_pages, render_document, theme_roles
from app.database.models import (
    Base,
    PresentationDocument,
    PresentationRevision,
    ProductObservation,
)
from app.domain.models import Product, ProductColor, utcnow
from app.domain.presentation import Theme, price_label, short_display_name
from app.main import create_app


def image_bytes(color="blue", alpha=True):
    image = Image.new("RGBA", (300, 300), (0, 0, 0, 0) if alpha else "white")
    ImageDraw.Draw(image).rounded_rectangle((100, 35, 200, 270), radius=20, fill=color)
    data = io.BytesIO()
    image.save(data, format="PNG")
    return data.getvalue()


def edit_body(doc, **settings):
    draft = doc["draft"]
    return {
        "version": doc["version"],
        "settings": draft["settings"] | settings,
        "items": [
            {k: i[k] for k in ("offer_id", "display_name", "caption", "image_url")} for i in draft["items"]
        ],
        "category_order": draft["category_order"],
        "original_fallback_ids": draft["original_fallback_ids"],
    }


@pytest.mark.parametrize(
    "value,label",
    [
        (None, "Цена по запросу"),
        ("12630.49", "12 630 ₸ / шт."),
        ("12630.50", "12 631 ₸ / шт."),
        ("0", "0 ₸ / шт."),
    ],
)
def test_decimal_commercial_display(value, label):
    assert price_label(value) == label


def test_short_name_omits_inherited_parent_color_but_retains_model_and_capacity():
    assert short_display_name("Ручка модели Example, чёрный — Красный") == "Ручка модели Example"
    assert short_display_name("Бутылка Example, 500 мл — Синий") == "Бутылка Example, 500 мл"
    assert short_display_name("Рюкзак «Чёрный лебедь» — Красный") == "Рюкзак «Чёрный лебедь»"


@pytest.mark.parametrize(
    "url",
    [
        "http://vto.kz/a",
        "https://localhost/a",
        "https://vto.kz.evil/a",
        "https://u:p@vto.kz/a",
        "https://vto.kz:8080/a",
        "file:///etc/passwd",
        "https://127.0.0.1/a",
    ],
)
def test_only_trusted_catalog_image_sources(url):
    with pytest.raises(ValueError):
        valid_source(url, resolve=False)


def test_upload_validation_and_real_transparency(tmp_path):
    with pytest.raises(ValueError):
        decode_image(b"<svg><script/></svg>")
    with pytest.raises(ValueError):
        decode_image(b"not a photo")
    truncated = io.BytesIO()
    Image.new("RGB", (400, 400), "white").save(truncated, format="JPEG")
    with pytest.raises(ValueError, match="допустимым изображением"):
        decode_image(truncated.getvalue()[:-80])
    with pytest.raises(ValueError):
        store_upload(tmp_path, "doc", image_bytes(alpha=False), prepared=True)
    key = store_upload(tmp_path, "doc", image_bytes(), prepared=True)
    assert transparent(Image.open(safe_path(tmp_path, key)))
    with pytest.raises(ValueError):
        safe_path(tmp_path, "../other-user.pdf")


@pytest.mark.parametrize("failed_mask", [False, True])
def test_worker_reports_each_failed_photo_and_does_not_silently_export(tmp_path, monkeypatch, failed_mask):
    import json

    from app.application.presentation_worker import run

    if failed_mask:

        def reject(*args, **kwargs):
            raise ValueError("Не удалось сохранить контуры предмета")

        monkeypatch.setattr(AssetPreparer, "prepare", reject)
    source = tmp_path / "input.json"
    output = tmp_path / "result.json"
    value = {
        "document_id": "fixture",
        "settings": Theme().model_dump(),
        "original_fallback_ids": [],
        "category_order": ["pen"],
        "items": [{"offer_id": "vto:missing", "display_name": "Ручка", "image_url": None}],
    }
    source.write_text(
        json.dumps({"storage": str(tmp_path), "revision": "fixture", "input": value, "output": str(output)}),
        "utf-8",
    )
    run(source)
    result = json.loads(output.read_text("utf-8"))
    assert result["status"] == "NEEDS_REVIEW" and result["issues"][0]["offer_id"] == "vto:missing"
    assert "pdf" not in result and result["assets"] == {}


def test_hash_cache_model_version_and_original_are_separate(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "app.application.presentation_assets.fetch_image", lambda url: calls.append(url) or image_bytes()
    )
    preparer = AssetPreparer(tmp_path)
    first = preparer.prepare({"image_url": "https://vto.kz/blue.png"})
    second = preparer.prepare({"image_url": "https://vto.kz/blue.png", "original_key": first["original"]})
    assert len(calls) == 1 and second["cached"] and first["hash"] == second["hash"]
    assert first["key"] != first["original"]
    assert safe_path(tmp_path, first["original"]).read_bytes() == image_bytes()
    fallback = preparer.prepare({"original_key": first["original"]}, original_fallback=True)
    assert fallback["pipeline"] == "explicit-original-fallback" and fallback["key"] != first["key"]


def test_render_30_variants_without_supplier_data_and_real_pdf_previews(tmp_path):
    assets = {}
    items = []
    for n in range(30):
        key = store_upload(tmp_path, "fixture", image_bytes("blue" if n % 2 else "grey"), prepared=True)
        id = f"vto:{n}"
        items.append(
            {
                "offer_id": id,
                "display_name": f"Рюкзак для ноутбука {n}",
                "caption": "",
                "brand": None if n == 0 else "Molti",
                "unit_price_kzt": None if n == 0 else "12630.50",
                "category_key": "backpack",
                "category_label": "Рюкзаки",
                "sku": "SHOULD_NOT_LEAK",
                "source_url": "https://vto.kz/private-provenance",
                "stock": 400,
            }
        )
        assets[id] = {"key": key}
    settings = Theme().model_dump()
    settings["title"] = "Подарки для команды"
    output = render_document(
        tmp_path,
        "revision",
        {"document_id": "fixture", "items": items, "settings": settings, "category_order": ["backpack"]},
        assets,
    )
    assert output["placements"] == [i["offer_id"] for i in items] and output["page_count"] == 16
    assert len(output["pages"]) == 16
    with pdfium.PdfDocument(str(safe_path(tmp_path, output["pdf"]))) as pdf:
        extracted = ""
        for page in pdf:
            assert tuple(round(v) for v in page.get_size()) == (960, 540)
            with closing(page.get_textpage()) as text:
                extracted += text.get_text_range()
            page.close()
        assert "SHOULD_NOT_LEAK" not in extracted and "vto.kz" not in extracted and "vto:" not in extracted
        assert "Цена по запросу" in extracted and "12 631" in extracted and "₸" in extracted
    with Image.open(safe_path(tmp_path, output["pages"][0]["preview"])) as preview:
        assert preview.size == (1440, 810)
    long = copy.deepcopy(items[:1])
    long[0]["display_name"] = (
        "Длинное русское название рюкзака для командировок с отделением для ноутбука и несколькими карманами"
    )
    assert plan_pages(long, settings, ["backpack"])[1]["type"] == "hero"
    output = render_document(
        tmp_path,
        "long",
        {"document_id": "fixture", "items": long, "settings": settings, "category_order": ["backpack"]},
        assets,
    )
    assert output["page_count"] == 2


@pytest.mark.parametrize(
    "palette", [["#FFFFFF"], ["#000000"], ["#777777"], ["#808080"], ["#A9B3C9", "#FFFFFF"]]
)
def test_palette_text_has_readable_contrast(palette):
    roles = theme_roles(palette)
    a, b = sorted([luminance(roles["cover"]), luminance(roles["cover_text"])])
    assert (b + 0.05) / (a + 0.05) >= 4.5


async def test_snapshot_http_revision_ownership_and_process(settings):
    settings.auth_required = True
    app = create_app(settings, [])
    async with app.state.engine.begin() as db:
        await db.run_sync(Base.metadata.create_all)
    products = [
        Product(
            id=f"vto:{i}",
            supplier="vto",
            name=f"Шариковая ручка {i}",
            category="pen",
            source_url="https://vto.kz/product/pen/",
            original_price=100 + i,
            original_currency="KZT",
            brand="Без бренда" if i == 0 else "Molti",
            colors=[ProductColor(original_color="синий" if i % 2 else "серый")],
            metadata={"vto_product_id": 1000 + i // 2},
        )
        for i in range(105)
    ]
    await app.state.index.ingest(products)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post(
            "/api/auth/register", json={"name": "presentation-owner", "password": "presentation-pass-123"}
        )
        await client.post("/api/chats", json={"project_name": "Контрольный документ"})
        project = (await client.get("/api/projects")).json()[0]["id"]
        result = (await client.post(f"/api/projects/{project}/requests", json={"query": "ручки"})).json()[
            "research"
        ]
        ids = [result["products"][0]["id"], "vto:103"]
        result = (
            await client.patch(
                f"/api/researches/{result['id']}/view",
                json={"version": result["view"]["version"], "selected_ids": ids},
            )
        ).json()
        body = {"research_id": result["id"], "selection_version": result["view"]["version"]}
        response = await client.post(f"/api/projects/{project}/presentations", json=body)
        assert response.status_code == 201, response.text
        doc = response.json()
        id = doc["id"]
        base = f"/api/presentations/{id}"
        assert [i["offer_id"] for i in doc["draft"]["items"]] == ids
        assert len(doc["draft"]["items"]) == 2
        assert doc["draft"]["items"][0]["brand"] is None
        assert doc["draft"]["items"][0]["source_brand"] == "Без бренда"
        assert (await client.post(base + "/logo?version=1", content=b"<svg/>")).status_code == 422
        malicious = edit_body(doc)
        malicious["items"][0]["unit_price_kzt"] = "0"
        assert (await client.patch(base, json=malicious)).status_code == 422
        assert (
            await client.post(f"/api/projects/{project}/presentations", json=body | {"selection_version": 0})
        ).status_code == 409
        async with AsyncClient(transport=transport, base_url="http://test") as foreign:
            await foreign.post(
                "/api/auth/register", json={"name": "presentation-other", "password": "presentation-pass-456"}
            )
            assert (await foreign.get(base)).status_code == 404
            assert (await foreign.post(base + "/logo?version=1", content=image_bytes())).status_code == 404
            assert (
                await foreign.post(f"/api/projects/{project}/presentations", json=body)
            ).status_code == 404
        for number, offer in enumerate(ids):
            uploaded = await client.post(
                base + f"/items/{offer}/image?version={doc['version']}",
                content=image_bytes("blue" if number else "grey"),
            )
            assert uploaded.status_code == 200, uploaded.text
            doc = uploaded.json()
        generated = (await client.post(base + "/generate", json={"version": doc["version"]})).json()
        again = (await client.post(base + "/generate", json={"version": doc["version"]})).json()
        assert generated["id"] == again["id"]
        job = await app.state.presentations.claim_next()
        assert job and await app.state.presentations.claim_next() is None
        await app.state.presentations.execute(job)
        doc = (await client.get(base)).json()
        revision = doc["revisions"][0]
        assert revision["status"] == "READY", revision
        # An inferred mask requires review, unlike an already prepared PNG.
        async with app.state.sessions.begin() as db:
            stored = await db.get(PresentationRevision, revision["id"])
            result_copy = copy.deepcopy(stored.result)
            for asset in result_copy["assets"].values():
                asset["review_required"] = True
            stored.status, stored.result = "NEEDS_REVIEW", result_copy
        assert (await client.get(revision["result"]["pdf_url"])).status_code == 409
        assert (
            await client.post(base + f"/revisions/{revision['id']}/approve", json={"asset_hashes": []})
        ).status_code == 422
        hashes = [a["hash"] for a in result_copy["assets"].values()]
        approved = await client.post(
            base + f"/revisions/{revision['id']}/approve", json={"asset_hashes": hashes}
        )
        assert approved.status_code == 200
        download = await client.get(revision["result"]["pdf_url"])
        assert download.status_code == 200
        assert hashlib.sha256(download.content).hexdigest() == revision["result"]["sha256"]
        assert (await client.get(revision["result"]["pages"][0]["preview"])).status_code == 200
        async with AsyncClient(transport=transport, base_url="http://test") as foreign:
            assert (await foreign.get(revision["result"]["pdf_url"])).status_code == 401
        original = copy.deepcopy(doc["draft"]["items"])
        async with app.state.sessions.begin() as db:
            await db.execute(
                update(ProductObservation).where(ProductObservation.offer_id == ids[0]).values(price_kzt=9999)
            )
        assert (await client.get(base)).json()["draft"]["items"] == original
        saved = await client.patch(base, json=edit_body(doc, title="Новая редакция"))
        assert saved.status_code == 200, saved.text
        assert (await client.get(revision["result"]["pdf_url"])).status_code == 409
        assert (
            await client.get(revision["result"]["pdf_url"] + "?historical=true")
        ).content == download.content
        refreshed = (await client.post(base + "/refresh", json={"version": saved.json()["version"]})).json()
        assert refreshed["draft"]["items"][0]["unit_price_kzt"] == "9999.00"
        assert [i["offer_id"] for i in refreshed["draft"]["items"]] == ids
        current = (await client.get(f"/api/researches/{result['id']}")).json()
        assert current["view"]["selected_ids"] == ids
        queued = (await client.post(base + "/generate", json={"version": refreshed["version"]})).json()
        cancelled = await client.post(base + f"/revisions/{queued['id']}/cancel")
        assert cancelled.status_code == 200 and cancelled.json()["revisions"][0]["status"] == "CANCELLED"
        assert await app.state.presentations.claim_next() is None
    await app.state.engine.dispose()


async def test_expired_claim_recovery_is_bounded(repository, settings):
    from app.application.presentations import PresentationService
    from app.database.models import Project, User
    from app.database.research import ResearchRepository

    # No real users/catalog are involved in this isolated SQLite job test.
    service = PresentationService(repository.sessions, settings, ResearchRepository(repository.sessions))
    async with repository.sessions.begin() as db:
        db.add(User(id="fixture", display_name="fixture", normalized_name="fixture", password_hash="unused"))
        await db.flush()
        db.add(Project(id="fixture", user_id="fixture", name="fixture"))
        await db.flush()
        doc = PresentationDocument(id="doc", project_id="fixture", user_id="fixture", draft={})
        db.add(doc)
        await db.flush()
        db.add(
            PresentationRevision(
                id="expired",
                document_id="doc",
                source_version=1,
                input={},
                status="RENDERING",
                claim="old",
                attempts=1,
                lease_until=utcnow() - timedelta(seconds=1),
            )
        )
    row = await service.claim_next()
    assert row.id == "expired" and row.attempts == 2 and row.claim != "old"
    async with repository.sessions.begin() as db:
        await db.execute(
            update(PresentationRevision)
            .where(PresentationRevision.id == row.id)
            .values(lease_until=utcnow() - timedelta(seconds=1))
        )
    assert await service.claim_next() is None
    async with repository.sessions() as db:
        assert (await db.get(PresentationRevision, "expired")).status == "FAILED"


async def test_running_cancellation_terminates_owned_process_tree(repository, settings, monkeypatch):
    import asyncio
    import sys

    import psutil

    from app.application.presentations import PresentationService
    from app.database.models import Project, User
    from app.database.research import ResearchRepository

    service = PresentationService(repository.sessions, settings, ResearchRepository(repository.sessions))
    async with repository.sessions.begin() as db:
        db.add(
            User(
                id="cancel-owner",
                display_name="cancel-owner",
                normalized_name="cancel-owner",
                password_hash="unused",
            )
        )
        await db.flush()
        db.add(Project(id="cancel-project", user_id="cancel-owner", name="Cancellation"))
        await db.flush()
        db.add(
            PresentationDocument(
                id="cancel-doc", project_id="cancel-project", user_id="cancel-owner", draft={}
            )
        )
        await db.flush()
        db.add(PresentationRevision(id="cancel-job", document_id="cancel-doc", source_version=1, input={}))
    real_spawn = asyncio.create_subprocess_exec
    spawned = []

    async def slow_owned_child(*args, **kwargs):
        process = await real_spawn(sys.executable, "-c", "import time; time.sleep(60)", **kwargs)
        spawned.append(process.pid)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", slow_owned_child)
    job = await service.claim_next()
    execution = asyncio.create_task(service.execute(job))
    for _ in range(100):
        if spawned:
            break
        await asyncio.sleep(0.01)
    assert spawned
    descendants = psutil.Process(spawned[0]).children(recursive=True)
    await service.cancel("cancel-doc", "cancel-job", "cancel-owner")
    await asyncio.wait_for(execution, 5)
    assert not psutil.pid_exists(spawned[0])
    assert all(not p.is_running() for p in descendants)
    async with repository.sessions() as db:
        assert (await db.get(PresentationRevision, "cancel-job")).status == "CANCELLED"
