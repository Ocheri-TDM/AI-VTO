"""Real HTTP acceptance for custom presentation materials; exact-ID cleanup.

prepare -> run -> visually inspect pages -> approve --reviewed -> prime-ui
-> browser verification -> verify -> cleanup. Never prints session cookies.
"""

import argparse
import asyncio
import hashlib
import json
import secrets
import shutil
import sys
import time
from pathlib import Path
from uuid import uuid4

import httpx
from PIL import Image, ImageCms, ImageDraw, ImageFont
from sqlalchemy import Text, cast, delete, func, select, text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))
from app.config import Settings
from app.database.connection import create_database
from app.database.models import PresentationDocument, PresentationRevision, User
from app.domain.presentation import ACTIVE
from app.domain.presentation_materials import MaterialEdit

LEDGER = ROOT / ".local/custom-materials-run.json"
SOURCES = ROOT / "apps/api/tests/fixtures/presentations/custom-materials/sources"
IDS = ["vto:1823109", "vto:1823108", "vto:1823107"]


def fixtures():
    SOURCES.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (1600, 1000))
    draw = ImageDraw.Draw(image)
    for y in range(1000):
        t = y / 1000
        draw.line(
            (0, y, 1600, y),
            fill=(round(117 - 80 * t), round(151 - 90 * t), round(167 - 95 * t)),
        )
    draw.polygon([(300, 950), (360, 230), (1000, 55), (1270, 950)], fill="#1C3545")
    draw.polygon([(360, 230), (680, 390), (1000, 55)], fill="#C6B68C")
    for n in range(17):
        x = 380 + n * 46
        draw.line((x, 250 - 11 * n, x + 90, 950), fill="#8B9C9E", width=3)
    for y in range(330, 960, 73):
        draw.line((340, y, 1260, y), fill="#415F6E", width=3)
    draw.polygon(
        [(0, 1000), (0, 830), (520, 890), (1600, 800), (1600, 1000)], fill="#122B3C"
    )
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    if not (SOURCES / "architecture.jpg").exists():
        image.save(SOURCES / "architecture.jpg", quality=92, icc_profile=profile)
    ornament = Image.new("RGBA", (640, 160))
    d = ImageDraw.Draw(ornament)
    for x in range(24, 620, 72):
        d.line(
            (x, 80, x + 28, 28, x + 56, 80, x + 28, 132, x, 80), fill="#C9AB78", width=5
        )
    if not (SOURCES / "ornament.png").exists():
        ornament.save(SOURCES / "ornament.png")
    texture = Image.new("RGB", (1200, 675), "#183B4A")
    d = ImageDraw.Draw(texture)
    for x in range(-600, 1200, 120):
        d.polygon([(x, 0), (x + 58, 0), (x + 675, 675), (x + 617, 675)], fill="#D6B96F")
    for y in range(0, 675, 90):
        d.rectangle((0, y, 1200, y + 20), fill="#A0BBC3")
    if not (SOURCES / "texture.webp").exists():
        texture.save(SOURCES / "texture.webp", lossless=True)
    logo = Image.new("RGBA", (320, 100))
    d = ImageDraw.Draw(logo)
    font = ImageFont.truetype(
        str(ROOT / "apps/api/app/assets/fonts/NotoSans-Bold.ttf"), 72
    )
    d.text((4, -7), "SS", font=font, fill="#C9AB78")
    if not (SOURCES / "logo.png").exists():
        logo.save(SOURCES / "logo.png")
    if not (SOURCES / "low-resolution.png").exists():
        Image.new("RGB", (64, 64), "#163E52").save(SOURCES / "low-resolution.png")
    (SOURCES / "provenance.json").write_text(
        json.dumps(
            {
                "source": "Deterministic original Pillow regression illustrations; no third-party or corporate content. Architecture is an illustration, not a camera photograph.",
                "files": {
                    p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in SOURCES.iterdir()
                    if p.suffix in (".jpg", ".png", ".webp")
                },
            },
            indent=2,
        ),
        "utf-8",
    )


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "phase",
        choices=[
            "prepare",
            "run",
            "refresh-visuals",
            "scope-products",
            "replace-valid",
            "approve",
            "prime-ui",
            "verify",
            "cleanup",
        ],
    )
    parser.add_argument("--reviewed", action="store_true")
    args = parser.parse_args()
    if args.phase == "prepare" and LEDGER.exists():
        raise SystemExit("Clean or finish the existing run first")
    if args.phase != "prepare" and not LEDGER.exists():
        raise SystemExit("Run prepare first")
    state = (
        json.loads(LEDGER.read_text("utf-8"))
        if LEDGER.exists()
        else {
            "run": uuid4().hex,
            "users": [],
            "projects": [],
            "documents": [],
            "renders": {},
        }
    )
    output = ROOT / ".local/custom-materials-acceptance" / state["run"]
    output.mkdir(parents=True, exist_ok=True)
    cfg = Settings()
    engine, sessions = create_database(cfg.database_url)

    def persist():
        LEDGER.write_text(json.dumps(state, ensure_ascii=False, indent=2), "utf-8")

    async def call(c, method, path, **kwargs):
        r = await c.request(method, path, **kwargs)
        r.raise_for_status()
        return (
            r.json()
            if r.headers.get("content-type", "").startswith("application/json")
            else r.content
        )

    async def baseline():
        async with sessions() as db:
            return dict(
                (
                    await db.execute(
                        text(
                            "SELECT count(*) offers,md5(string_agg(o.id||o.product_id||o.source_url||coalesce(p.payload::text,'')||coalesce(p.observed_at::text,''),'' ORDER BY o.id)) fingerprint FROM supplier_offers o LEFT JOIN product_observations p ON p.offer_id=o.id"
                        )
                    )
                )
                .mappings()
                .one()
            )

    async def project(c, name, ids):
        chat = await call(c, "POST", "/api/chats", json={"project_name": name})
        p = next(
            p
            for p in await call(c, "GET", "/api/projects")
            if p["chat_id"] == chat["id"]
        )
        state["projects"].append(p["id"])
        persist()
        for id in ids:
            collection = await call(
                c,
                "POST",
                f"/api/projects/{p['id']}/collection/items",
                json={"offer_id": id},
            )
        doc = await call(
            c,
            "POST",
            f"/api/projects/{p['id']}/presentations",
            json={"collection_version": collection["version"]},
        )
        state["documents"].append(doc["id"])
        persist()
        return await edit(c, doc, style="premium-dark", auto_design=False)

    async def edit(c, doc, materials=None, **settings):
        draft = doc["draft"]
        body = {
            "version": doc["version"],
            "settings": draft["settings"] | settings,
            "items": [
                {k: i[k] for k in ("offer_id", "display_name", "caption", "image_url")}
                for i in draft["items"]
            ],
            "category_order": draft["category_order"],
            "original_fallback_ids": draft["original_fallback_ids"],
        }
        if materials is not None:
            body["materials"] = [
                {k: m[k] for k in MaterialEdit.model_fields} for m in materials
            ]
        return await call(c, "PATCH", "/api/presentations/" + doc["id"], json=body)

    async def get(c, id):
        return await call(c, "GET", "/api/presentations/" + id)

    async def upload(c, doc, role, file, replace=None, expect="READY"):
        doc = await call(
            c,
            "POST",
            f"/api/presentations/{doc['id']}/materials?version={doc['version']}&role={role}"
            + (f"&replace={replace}" if replace else ""),
            content=file.read_bytes(),
            headers={"X-Filename": file.name},
        )
        start = time.perf_counter()
        while doc.get("materials_preparing"):
            assert time.perf_counter() - start < 75
            await asyncio.sleep(0.35)
            doc = await get(c, doc["id"])
        entry = doc["draft"]["materials"][-1]
        assert entry["status"] == expect, (role, entry)
        if expect == "READY":
            thumb = await call(c, "GET", entry["thumbnail_url"])
            assert thumb[:8] == b"\x89PNG\r\n\x1a\n"
        return doc

    async def render(c, doc, label, expected_review=False):
        before = await call(c, "GET", f"/api/projects/{doc['project_id']}/collection")
        rev = await call(
            c,
            "POST",
            f"/api/presentations/{doc['id']}/generate",
            json={"version": doc["version"]},
        )
        start = time.perf_counter()
        while rev["status"] in ACTIVE:
            assert time.perf_counter() - start < 330, label
            await asyncio.sleep(0.5)
            doc = await get(c, doc["id"])
            rev = next(r for r in doc["revisions"] if r["id"] == rev["id"])
        assert rev["result"].get("pdf_url"), (label, rev)
        assert bool(rev["result"].get("issues")) == expected_review, (label, rev)
        assert before == await call(
            c, "GET", f"/api/projects/{doc['project_id']}/collection"
        )
        assert rev["result"]["placements"] == [
            i["offer_id"] for i in doc["draft"]["items"]
        ]
        folder = output / label
        folder.mkdir(exist_ok=True)
        for p in rev["result"]["pages"]:
            (folder / f"page-{p['page']}.png").write_bytes(
                await call(c, "GET", p["preview"])
            )
        async with sessions() as db:
            row = await db.get(PresentationRevision, rev["id"])
            metrics = {
                k: row.result.get(k)
                for k in (
                    "sha256",
                    "bytes",
                    "page_count",
                    "asset_seconds",
                    "render_seconds",
                    "preview_seconds",
                    "total_seconds",
                    "peak_rss_bytes",
                    "material_usage",
                    "material_reservations",
                    "design_plan",
                    "placements",
                )
            }
            state["renders"][label] = {
                "document": doc["id"],
                "revision": rev["id"],
                "version": rev["source_version"],
                "review_blocked": expected_review,
                "metrics": metrics,
                "facts": [
                    {
                        k: i.get(k)
                        for k in (
                            "offer_id",
                            "display_name",
                            "brand",
                            "unit_price_kzt",
                            "image_url",
                        )
                    }
                    for i in row.input["items"]
                ],
            }
        persist()
        print(
            label,
            json.dumps(
                {
                    k: metrics[k]
                    for k in ("page_count", "total_seconds", "bytes", "sha256")
                }
            ),
            flush=True,
        )
        return doc

    try:
        async with httpx.AsyncClient(
            base_url="http://127.0.0.1:8000",
            cookies=state["users"][0]["cookies"] if state["users"] else {},
            timeout=120,
        ) as c:
            if args.phase == "prepare":
                fixtures()
                state["catalog_before"] = await baseline()
                state["created_cache"] = []
                for p in SOURCES.iterdir():
                    if p.suffix not in (".png", ".jpg", ".webp"):
                        continue
                    key = (
                        "assets/custom/"
                        + hashlib.sha256(p.read_bytes()).hexdigest()
                        + ".v1.png"
                    )
                    for candidate in (
                        key,
                        key + ".json",
                        key.removesuffix(".png") + "-thumb.png",
                    ):
                        if not (cfg.presentation_storage / candidate).exists():
                            state["created_cache"].append(candidate)
                user = await call(
                    c,
                    "POST",
                    "/api/auth/register",
                    json={
                        "name": "materials-v1-" + state["run"],
                        "password": secrets.token_urlsafe(24),
                    },
                )
                state["users"].append({"id": user["id"], "cookies": dict(c.cookies)})
                persist()
                doc = await project(c, "Custom materials control", IDS)
                state["primary"] = doc["id"]
                persist()
                print(
                    "Prepared isolated account/project/collection and original synthetic fixtures"
                )
            elif args.phase == "run":
                doc = await get(c, state["primary"])
                doc = await render(c, doc, "A-empty")
                expected = json.loads(
                    (
                        ROOT
                        / "apps/api/tests/fixtures/presentations/premium-dark-v3/manifest.json"
                    ).read_text("utf-8")
                )["control_pdf"]["sha256"]
                assert state["renders"]["A-empty"]["metrics"]["sha256"] == expected
                doc = await upload(c, doc, "COVER_PHOTO", SOURCES / "architecture.jpg")
                doc = await render(c, doc, "B-cover")
                doc = await edit(
                    c, doc, [m | {"enabled": False} for m in doc["draft"]["materials"]]
                )
                doc = await upload(c, doc, "DECORATION", SOURCES / "ornament.png")
                doc = await render(c, doc, "C-decoration")
                doc = await edit(
                    c, doc, [m | {"enabled": False} for m in doc["draft"]["materials"]]
                )
                doc = await upload(c, doc, "BACKGROUND", SOURCES / "texture.webp")
                doc = await render(c, doc, "D-texture")
                photo = next(
                    m for m in doc["draft"]["materials"] if m["role"] == "COVER_PHOTO"
                )
                state["original_material_url"] = photo["thumbnail_url"]
                state["original_material_hash"] = hashlib.sha256(
                    await call(c, "GET", photo["thumbnail_url"])
                ).hexdigest()
                persist()
                doc = await edit(
                    c,
                    doc,
                    [
                        m
                        | {
                            "enabled": m["role"] != "BACKGROUND",
                            "crop": m["role"] == "COVER_PHOTO",
                            "focal_x": 0.75,
                        }
                        for m in doc["draft"]["materials"]
                    ],
                )
                doc = await call(
                    c,
                    "POST",
                    f"/api/presentations/{doc['id']}/logo?version={doc['version']}",
                    content=(SOURCES / "logo.png").read_bytes(),
                )
                doc = await render(c, doc, "E-combined")
                doc = await edit(
                    c,
                    doc,
                    [m | {"enabled": False} for m in doc["draft"]["materials"]],
                    use_logo=False,
                )
                doc = await render(c, doc, "F-disabled")
                assert state["renders"]["F-disabled"]["metrics"]["sha256"] == expected
                long = {
                    "client": "Объединённая научно-производственная компания развития технологий",
                    "subtitle": "Подборка для команды: разные варианты, реальные цены и материалы оформления.",
                }
                for label, ids in [
                    ("G-one", IDS[:1]),
                    ("G-three", IDS),
                    ("G-six", IDS + ["vto:1961375", "vto:1950874", "vto:1883738"]),
                ]:
                    other = await project(c, label, ids)
                    other = await edit(c, other, **long)
                    other = await upload(
                        c, other, "DECORATION", SOURCES / "ornament.png"
                    )
                    await render(c, other, label)
                doc = await get(c, state["primary"])
                current = next(
                    m for m in doc["draft"]["materials"] if m["role"] == "COVER_PHOTO"
                )
                doc = await upload(
                    c,
                    doc,
                    "COVER_PHOTO",
                    SOURCES / "low-resolution.png",
                    current["id"],
                    expect="FAILED",
                )
                assert any(
                    m["id"] == current["id"] and m["status"] == "READY"
                    for m in doc["draft"]["materials"]
                )
                invalid = output / "invalid.bin"
                invalid.write_bytes(b"<svg><script/></svg>")
                bg = next(
                    m for m in doc["draft"]["materials"] if m["role"] == "BACKGROUND"
                )
                doc = await upload(
                    c, doc, "BACKGROUND", invalid, bg["id"], expect="FAILED"
                )
                oversized = await c.post(
                    f"/api/presentations/{doc['id']}/materials?version={doc['version']}&role=DECORATION",
                    content=b"x" * (cfg.presentation_material_max_bytes + 1),
                )
                assert oversized.status_code == 413
                doc = await get(c, doc["id"])
                values = [
                    m
                    | {
                        "enabled": m["role"] == "DECORATION",
                        "position": "LEFT",
                        "required": m["role"] == "DECORATION",
                    }
                    for m in doc["draft"]["materials"]
                    if m["status"] == "READY"
                ]
                doc = await edit(c, doc, values)
                await render(c, doc, "I-required-skip", expected_review=True)
                async with httpx.AsyncClient(
                    base_url="http://127.0.0.1:8000", timeout=30
                ) as foreign:
                    user = await call(
                        foreign,
                        "POST",
                        "/api/auth/register",
                        json={
                            "name": "materials-other-" + state["run"],
                            "password": secrets.token_urlsafe(24),
                        },
                    )
                    state["users"].append(
                        {"id": user["id"], "cookies": dict(foreign.cookies)}
                    )
                    persist()
                    r = state["renders"]["E-combined"]
                    base = (
                        f"/api/presentations/{r['document']}/revisions/{r['revision']}"
                    )
                    for url in [
                        state["original_material_url"],
                        base + "/pages/1",
                        base + "/pdf?historical=true",
                    ]:
                        assert (await foreign.get(url)).status_code == 404
                assert await baseline() == state["catalog_before"]
                print(
                    "H invalid/low-resolution/oversize, I blocked required material, J foreign reads: PASS"
                )
            elif args.phase == "refresh-visuals":
                state["superseded"] = {
                    label: state["renders"][label]
                    for label in ("B-cover", "D-texture", "E-combined")
                }
                persist()
                for label, role, logo in [
                    ("B-cover", "COVER_PHOTO", False),
                    ("D-texture", "BACKGROUND", False),
                    ("E-combined", None, True),
                ]:
                    doc = await get(c, state["primary"])
                    values = [
                        m
                        | {
                            "enabled": m["role"] == role
                            if role
                            else m["role"] != "BACKGROUND",
                            "position": "AUTO",
                            "required": False,
                            "crop": label == "E-combined"
                            and m["role"] == "COVER_PHOTO",
                        }
                        for m in doc["draft"]["materials"]
                        if m["status"] == "READY"
                    ]
                    doc = await edit(c, doc, values, use_logo=logo)
                    await render(c, doc, label)
            elif args.phase == "scope-products":
                doc = await get(c, state["primary"])
                values = [
                    m
                    | (
                        {"scope": "PRODUCTS", "crop": True}
                        if m["role"] == "COVER_PHOTO"
                        else {}
                    )
                    for m in doc["draft"]["materials"]
                ]
                doc = await edit(c, doc, values)
                doc = await render(c, doc, "K-photo-products")
                rev = doc["revisions"][0]
                photo = next(
                    u
                    for u in rev["result"]["material_usage"]
                    if u["role"] == "COVER_PHOTO"
                )
                assert photo["pages"] == [2]
            elif args.phase == "replace-valid":
                doc = await get(c, state["primary"])
                previous = next(
                    m for m in doc["draft"]["materials"] if m["role"] == "COVER_PHOTO"
                )
                replacement = output / "replacement.png"
                with Image.open(SOURCES / "architecture.jpg") as image:
                    image.convert("RGB").save(replacement)
                key = (
                    "assets/custom/"
                    + hashlib.sha256(replacement.read_bytes()).hexdigest()
                    + ".v1.png"
                )
                for candidate in (
                    key,
                    key + ".json",
                    key.removesuffix(".png") + "-thumb.png",
                ):
                    if not (cfg.presentation_storage / candidate).exists():
                        state["created_cache"].append(candidate)
                persist()
                count = len(doc["revisions"])
                doc = await upload(c, doc, "COVER_PHOTO", replacement, previous["id"])
                current = next(
                    m for m in doc["draft"]["materials"] if m["role"] == "COVER_PHOTO"
                )
                assert (
                    len(doc["revisions"]) == count
                    and current["id"] != previous["id"]
                    and current["hash"] != previous["hash"]
                )
                assert all(
                    current[k] == previous[k]
                    for k in MaterialEdit.model_fields
                    if k != "id"
                )
                assert (
                    hashlib.sha256(
                        await call(c, "GET", state["original_material_url"])
                    ).hexdigest()
                    == state["original_material_hash"]
                )
                await render(c, doc, "L-replaced")
            elif args.phase == "approve":
                if not args.reviewed:
                    raise SystemExit("Inspect every final page, then pass --reviewed")
                for label, r in state["renders"].items():
                    doc = await get(c, r["document"])
                    rev = next(v for v in doc["revisions"] if v["id"] == r["revision"])
                    if r["review_blocked"]:
                        assert (
                            await c.get(rev["result"]["pdf_url"] + "?historical=true")
                        ).status_code == 409
                        continue
                    hashes = [
                        a["hash"]
                        for a in rev["result"]["assets"].values()
                        if a["review_required"]
                    ]
                    await call(
                        c,
                        "POST",
                        f"/api/presentations/{doc['id']}/revisions/{rev['id']}/approve",
                        json={"asset_hashes": hashes},
                    )
                    data = await call(
                        c, "GET", rev["result"]["pdf_url"] + "?historical=true"
                    )
                    assert hashlib.sha256(data).hexdigest() == r["metrics"]["sha256"]
                    (output / label / "presentation.pdf").write_bytes(data)
                state["reviewed"] = True
                persist()
                print(
                    "Reviewed PDFs approved; authenticated downloads match previews/revision hashes"
                )
            elif args.phase == "prime-ui":
                doc = await get(c, state["primary"])
                values = [
                    m
                    | {
                        "enabled": m["role"] != "BACKGROUND",
                        "position": "AUTO",
                        "required": False,
                    }
                    for m in doc["draft"]["materials"]
                    if m["status"] == "READY"
                ]
                doc = await edit(c, doc, values, use_logo=True)
                await render(c, doc, "UI-prime")
                print("UI document primed; use the recorded disposable session only")
            elif args.phase == "verify":
                assert await baseline() == state["catalog_before"]
                assert (
                    hashlib.sha256(
                        await call(c, "GET", state["original_material_url"])
                    ).hexdigest()
                    == state["original_material_hash"]
                )
                print(
                    "Catalog fingerprint and replaced/disabled historical material bytes preserved"
                )
            else:
                for p in state["projects"]:
                    await call(c, "DELETE", "/api/projects/" + p)
                async with sessions.begin() as db:
                    await db.execute(
                        delete(User).where(
                            User.id.in_([u["id"] for u in state["users"]])
                        )
                    )
                storage = cfg.presentation_storage.resolve()
                for id in state["documents"]:
                    target = (storage / "documents" / id).resolve()
                    assert target.parent == storage / "documents"
                    if target.exists():
                        shutil.rmtree(target)
                for key in state.get("created_cache", []):
                    # Sidecar/thumbnail lifetime follows the normalized original.
                    reference_key = key.removesuffix(".json")
                    if reference_key.endswith("-thumb.png"):
                        reference_key = (
                            reference_key.removesuffix("-thumb.png") + ".png"
                        )
                    async with sessions() as db:
                        refs = await db.scalar(
                            select(func.count())
                            .select_from(PresentationDocument)
                            .where(
                                cast(PresentationDocument.draft, Text).contains(
                                    reference_key
                                )
                            )
                        )
                        refs += await db.scalar(
                            select(func.count())
                            .select_from(PresentationRevision)
                            .where(
                                cast(PresentationRevision.input, Text).contains(
                                    reference_key
                                )
                            )
                        )
                    target = (storage / key).resolve()
                    assert target.is_relative_to(storage / "assets/custom")
                    if not refs and target.exists():
                        target.unlink()
                assert (
                    output.parent
                    == (ROOT / ".local/custom-materials-acceptance").resolve()
                )
                shutil.rmtree(output)
                LEDGER.unlink()
                print(
                    "Exact-ID test accounts/projects/documents and owned files cleaned; user assets preserved"
                )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
