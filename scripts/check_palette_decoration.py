"""Live palette/decor acceptance. Never prints the disposable cookie ledger.

prepare -> palettes -> decoration -> inspect -> approve --reviewed -> verify -> cleanup
Installed Edge acceptance is separate; no bundled browser is installed.
"""

import argparse
import asyncio
import copy
import hashlib
import json
import secrets
import shutil
import sys
import time
from pathlib import Path
from uuid import uuid4

import httpx
from sqlalchemy import Text, cast, delete, func, select, text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))
from app.application.presentation_render import render_document
from app.config import Settings
from app.database.connection import create_database
from app.database.models import PresentationDocument, PresentationRevision, User
from app.domain.presentation import ACTIVE
from app.domain.presentation_materials import MaterialEdit
from app.domain.presentation_palette import library, validate_roles

LEDGER = ROOT / ".local/palette-acceptance.json"
SOURCES = ROOT / "apps/api/tests/fixtures/presentations/custom-materials/sources"
IDS = ["vto:1823109", "vto:1823108", "vto:1823107"]


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "phase",
        choices=[
            "prepare",
            "palettes",
            "light-styles",
            "extra-sample",
            "decoration",
            "prime-ui",
            "approve",
            "verify",
            "cleanup",
        ],
    )
    parser.add_argument("--reviewed", action="store_true")
    args = parser.parse_args()
    if args.phase == "prepare" and LEDGER.exists():
        raise SystemExit("Finish/clean the existing run first")
    state = (
        json.loads(LEDGER.read_text("utf-8"))
        if LEDGER.exists()
        else {
            "run": uuid4().hex,
            "users": [],
            "projects": [],
            "documents": [],
            "renders": {},
            "created_cache": [],
        }
    )
    if args.phase != "prepare" and not state["users"]:
        raise SystemExit("Run prepare first")
    output = ROOT / ".local/palette-acceptance" / state["run"]
    output.mkdir(parents=True, exist_ok=True)
    cfg = Settings()
    engine, sessions = create_database(cfg.database_url)

    def persist():
        LEDGER.write_text(json.dumps(state, ensure_ascii=False, indent=2), "utf-8")

    async def call(c, method, path, **kw):
        r = await c.request(method, path, **kw)
        r.raise_for_status()
        return (
            r.json()
            if r.headers.get("content-type", "").startswith("application/json")
            else r.content
        )

    async def get(c, id):
        return await call(c, "GET", f"/api/presentations/{id}")

    async def edit(c, doc, materials=None, **settings):
        draft = doc["draft"]
        return await call(
            c,
            "PATCH",
            f"/api/presentations/{doc['id']}",
            json={
                "version": doc["version"],
                "settings": draft["settings"] | settings,
                "items": [
                    {
                        k: i[k]
                        for k in ("offer_id", "display_name", "caption", "image_url")
                    }
                    for i in draft["items"]
                ],
                "category_order": draft["category_order"],
                "original_fallback_ids": draft["original_fallback_ids"],
                "materials": [
                    {k: m[k] for k in MaterialEdit.model_fields}
                    for m in (
                        draft.get("materials", []) if materials is None else materials
                    )
                ],
            },
        )

    async def make_project(c, label, ids):
        chat = await call(
            c,
            "POST",
            "/api/chats",
            json={"project_name": "palette-" + state["run"][:8] + "-" + label},
        )
        project = next(
            p
            for p in await call(c, "GET", "/api/projects")
            if p["chat_id"] == chat["id"]
        )
        state["projects"].append(project["id"])
        persist()
        for id in ids:
            collection = await call(
                c,
                "POST",
                f"/api/projects/{project['id']}/collection/items",
                json={"offer_id": id},
            )
        doc = await call(
            c,
            "POST",
            f"/api/projects/{project['id']}/presentations",
            json={"collection_version": collection["version"]},
        )
        state["documents"].append(doc["id"])
        persist()
        assert doc["draft"]["settings"]["auto_decoration"]["enabled"]
        return doc

    async def upload(c, doc, role, file):
        doc = await get(c, doc["id"])
        raw = file.read_bytes()
        if any(
            m["role"] == role
            and m["hash"] == hashlib.sha256(raw).hexdigest()
            and m["status"] == "READY"
            for m in doc["draft"].get("materials", [])
        ):
            return doc
        key = "assets/custom/" + hashlib.sha256(raw).hexdigest() + ".v1.png"
        for candidate in (key, key + ".json", key.removesuffix(".png") + "-thumb.png"):
            if not (cfg.presentation_storage / candidate).exists():
                state["created_cache"].append(candidate)
        persist()
        existing = len(doc["revisions"])
        doc = await call(
            c,
            "POST",
            f"/api/presentations/{doc['id']}/materials?role={role}&version={doc['version']}",
            content=raw,
            headers={"X-Filename": file.name},
        )
        started = time.perf_counter()
        while doc["materials_preparing"]:
            assert time.perf_counter() - started < 80
            await asyncio.sleep(0.3)
            doc = await get(c, doc["id"])
        assert len(doc["revisions"]) == existing
        assert all(m["status"] == "READY" for m in doc["draft"]["materials"])
        return doc

    async def render(c, doc, label, *, alternate=False, required=False):
        before = await call(c, "GET", f"/api/projects/{doc['project_id']}/collection")
        submitted = await call(
            c,
            "POST",
            f"/api/presentations/{doc['id']}/{'alternate' if alternate else 'generate'}",
            json={"version": doc["version"]},
        )
        if alternate:
            doc = await get(c, doc["id"])
            revision = doc["revisions"][0]
        else:
            revision = submitted
        started = time.perf_counter()
        while revision["status"] in ACTIVE:
            assert time.perf_counter() - started < 330, label
            await asyncio.sleep(0.4)
            doc = await get(c, doc["id"])
            revision = next(r for r in doc["revisions"] if r["id"] == revision["id"])
        assert revision["result"].get("pdf_url"), (label, revision)
        assert bool(revision["result"].get("issues")) == required, (
            label,
            revision["result"].get("issues"),
        )
        assert before == await call(
            c, "GET", f"/api/projects/{doc['project_id']}/collection"
        )
        assert revision["result"]["placements"] == [
            i["offer_id"] for i in doc["draft"]["items"]
        ]
        folder = output / label
        folder.mkdir(exist_ok=True)
        for page in revision["result"]["pages"]:
            (folder / f"page-{page['page']}.png").write_bytes(
                await call(c, "GET", page["preview"])
            )
        async with sessions() as db:
            row = await db.get(PresentationRevision, revision["id"])
            shutil.copy2(
                cfg.presentation_storage / row.result["pdf"],
                folder / "presentation.pdf",
            )
            item = {
                "document": doc["id"],
                "revision": revision["id"],
                "status": revision["status"],
                "input": copy.deepcopy(row.input),
                "result": copy.deepcopy(row.result),
                "required": required,
            }
        state["renders"][label] = item
        persist()
        print(
            label,
            json.dumps(
                {
                    k: item["result"].get(k)
                    for k in (
                        "page_count",
                        "total_seconds",
                        "bytes",
                        "sha256",
                        "ai_used",
                        "fallback_reason",
                    )
                }
            ),
            flush=True,
        )
        if item["result"].get("contrast_audit"):
            assert all(
                v["after"] >= v["required"] for v in item["result"]["contrast_audit"]
            )
        return doc

    async def other_documents(db):
        return dict(
            (
                await db.execute(
                    select(
                        PresentationDocument.id,
                        func.md5(cast(PresentationDocument.draft, Text)),
                    ).where(PresentationDocument.id.not_in(state["documents"]))
                )
            ).all()
        )

    try:
        async with httpx.AsyncClient(
            base_url="http://127.0.0.1:8000",
            timeout=120,
            cookies=state["users"][0]["cookies"] if state["users"] else {},
        ) as c:
            if args.phase == "prepare":
                async with sessions() as db:
                    state["other_drafts"] = await other_documents(db)
                    state["catalog_before"] = await db.scalar(
                        text("SELECT count(*) FROM supplier_offers")
                    )
                user = await call(
                    c,
                    "POST",
                    "/api/auth/register",
                    json={
                        "name": "palette-" + state["run"],
                        "password": secrets.token_urlsafe(24),
                    },
                )
                state["users"].append({"id": user["id"], "cookies": dict(c.cookies)})
                persist()
                doc = await make_project(c, "Melango", IDS)
                state["primary"] = doc["id"]
                persist()
                doc = await edit(
                    c,
                    doc,
                    style="premium-dark",
                    style_version=3,
                    auto_design=False,
                    auto_decoration=None,
                    palette_plan=None,
                )
                await render(c, doc, "legacy-v3")
                doc = await make_project(
                    c, "Mixed", IDS + ["vto:1961375", "vto:1950874", "vto:1883738"]
                )
                state["mixed"] = doc["id"]
                persist()
                print(
                    "Isolated projects ready; new-document auto-decoration default verified"
                )
            elif args.phase == "palettes":
                pools, stats = library()
                values = [v for pool in pools.values() for v in pool]
                assert len(values) >= 300 and len(pools["red"]) >= 40
                state["library"] = {
                    "count": len(values),
                    "directions": {d: len(v) for d, v in pools.items()},
                    "duplicate_validation": stats,
                    "minimum_role_contrast": min(
                        min(validate_roles(v).values()) for v in values
                    ),
                }
                doc = await get(c, state["primary"])
                before = doc["version"]
                jobs = len(doc["revisions"])
                timings = []
                for page in (1, 2, 1):
                    t = time.perf_counter()
                    result = await call(
                        c,
                        "GET",
                        f"/api/presentations/palettes?direction=red&page={page}",
                    )
                    timings.append(round(time.perf_counter() - t, 4))
                    assert result["published_total"] == len(values)
                doc = await get(c, doc["id"])
                assert doc["version"] == before and len(doc["revisions"]) == jobs
                state["library"]["http_seconds"] = timings
                red = pools["red"]
                sample = [("red-" + str(n), red[n]) for n in (0, 8, 16, 24, 32, 40)]
                sample += [(d, p[len(p) // 2]) for d, p in pools.items() if d != "red"]
                sample += [("neutral-soft", pools["neutral"][-1])]
                custom = (
                    await call(
                        c,
                        "GET",
                        "/api/presentations/palettes?direction=custom&primary=%23D6243B",
                    )
                )["items"][0]
                assert custom["primary"] == "#D6243B" and custom["locked"]
                sample += [("custom-fixed", custom)]
                assert len(sample) == 18
                doc = await edit(
                    c,
                    doc,
                    materials=[
                        m | {"enabled": False, "required": False}
                        for m in doc["draft"].get("materials", [])
                    ],
                    use_logo=False,
                )
                for label, p in sample:
                    doc = await edit(
                        c,
                        doc,
                        style="premium-dark",
                        palette=p["colors"],
                        palette_plan=p,
                        auto_decoration={
                            "version": 1,
                            "enabled": True,
                            "intensity": "NORMAL",
                        },
                        design_seed=0,
                    )
                    await render(c, doc, "palette-" + label)
                for style in ("editorial", "modern-graphic"):
                    p = red[16]
                    doc = await edit(
                        c, doc, style=style, palette=p["colors"], palette_plan=p
                    )
                    await render(c, doc, "light-" + style)
                persist()
            elif args.phase == "extra-sample":
                doc = await get(c, state["primary"])
                p = library()[0]["blue"][-1]
                started = time.perf_counter()
                pending = asyncio.create_task(
                    edit(
                        c,
                        doc,
                        style="premium-dark",
                        palette=p["colors"],
                        palette_plan=p,
                        materials=[
                            m | {"enabled": False, "required": False}
                            for m in doc["draft"].get("materials", [])
                        ],
                        use_logo=False,
                        design_seed=0,
                        auto_design=False,
                        auto_decoration={
                            "version": 1,
                            "enabled": True,
                            "intensity": "NORMAL",
                        },
                    )
                )
                health = []
                for _ in range(5):
                    await asyncio.sleep(0.03)
                    tick = time.perf_counter()
                    await call(c, "GET", "/api/health")
                    health.append(round(time.perf_counter() - tick, 4))
                doc = await pending
                state["cold_save_smoke"] = {
                    "save_and_probes_seconds": round(time.perf_counter() - started, 3),
                    "health_seconds": health,
                }
                await render(c, doc, "palette-extra-blue")
                persist()
            elif args.phase == "light-styles":
                doc = await get(c, state["primary"])
                p = library()[0]["red"][16]
                for style in ("editorial", "modern-graphic"):
                    doc = await edit(
                        c,
                        doc,
                        style=style,
                        palette=p["colors"],
                        palette_plan=p,
                        use_logo=False,
                        materials=[
                            m | {"enabled": False, "required": False}
                            for m in doc["draft"].get("materials", [])
                        ],
                        design_seed=0,
                        auto_design=False,
                        auto_decoration={
                            "version": 1,
                            "enabled": True,
                            "intensity": "NORMAL",
                        },
                    )
                    await render(c, doc, "light-" + style)
            elif args.phase == "decoration":
                doc = await get(c, state["primary"])
                p = library()[0]["red"][24]
                doc = await edit(
                    c,
                    doc,
                    materials=[
                        m | {"enabled": False, "required": False}
                        for m in doc["draft"].get("materials", [])
                    ],
                    use_logo=False,
                    style="premium-dark",
                    design_seed=0,
                    palette=p["colors"],
                    palette_plan=p,
                    auto_decoration={
                        "version": 1,
                        "enabled": False,
                        "intensity": "NORMAL",
                    },
                )
                await render(c, doc, "auto-off")
                doc = await edit(
                    c,
                    doc,
                    auto_decoration={
                        "version": 1,
                        "enabled": True,
                        "intensity": "NORMAL",
                    },
                )
                await render(c, doc, "auto-on")
                doc = await render(c, doc, "auto-other", alternate=True)
                a, b = state["renders"]["auto-on"], state["renders"]["auto-other"]
                assert (
                    a["result"]["decoration_plan"]["family"]
                    != b["result"]["decoration_plan"]["family"]
                )
                assert (
                    a["input"]["items"] == b["input"]["items"]
                    and a["input"]["settings"]["palette_plan"]
                    == b["input"]["settings"]["palette_plan"]
                )
                doc = await edit(c, doc, design_seed=0)
                doc = await call(
                    c,
                    "POST",
                    f"/api/presentations/{doc['id']}/logo?version={doc['version']}",
                    content=(SOURCES / "logo.png").read_bytes(),
                )
                await render(c, doc, "logo-only")
                doc = await upload(c, doc, "COVER_PHOTO", SOURCES / "architecture.jpg")
                doc = await edit(
                    c,
                    doc,
                    materials=[
                        m
                        | {
                            "enabled": m["role"] == "COVER_PHOTO",
                            "scope": "COVER",
                            "required": False,
                            "position": "AUTO",
                        }
                        for m in doc["draft"].get("materials", [])
                    ],
                )
                doc = await render(c, doc, "photo-cover")
                pages = state["renders"]["photo-cover"]["result"]["decoration_plan"][
                    "pages"
                ]
                assert not pages[0]["elements"] and pages[1]["elements"]
                doc = await upload(c, doc, "DECORATION", SOURCES / "ornament.png")
                materials = [
                    m
                    | {
                        "enabled": m["role"] == "DECORATION",
                        "scope": "PRODUCTS" if m["role"] == "DECORATION" else "COVER",
                    }
                    for m in doc["draft"]["materials"]
                ]
                doc = await edit(c, doc, materials=materials)
                await render(c, doc, "decor-products")
                pages = state["renders"]["decor-products"]["result"]["decoration_plan"][
                    "pages"
                ]
                assert pages[0]["elements"] and not pages[1]["elements"]
                doc = await edit(
                    c,
                    doc,
                    materials=[
                        m | {"enabled": False} for m in doc["draft"]["materials"]
                    ],
                )
                await render(c, doc, "disabled-custom")
                doc = await edit(
                    c,
                    doc,
                    materials=[
                        m
                        | {
                            "enabled": m["role"] == "DECORATION",
                            "required": m["role"] == "DECORATION",
                            "scope": "DOCUMENT",
                            "position": "LEFT",
                        }
                        for m in doc["draft"]["materials"]
                    ],
                )
                await render(c, doc, "required-skip", required=True)
                doc = await edit(
                    c,
                    doc,
                    materials=[
                        m | {"enabled": False, "required": False}
                        for m in doc["draft"]["materials"]
                    ],
                    auto_design=True,
                )
                await render(c, doc, "local-ai")
                doc = await edit(c, doc, auto_design=False)
                mixed = await get(c, state["mixed"])
                mixed = await edit(
                    c,
                    mixed,
                    style="premium-dark",
                    palette=p["colors"],
                    palette_plan=p,
                    client="Объединённая научно-производственная компания",
                    subtitle="Подборка для команды. Точные варианты, материалы и цены.",
                    auto_decoration={
                        "version": 1,
                        "enabled": True,
                        "intensity": "NORMAL",
                    },
                )
                await render(c, mixed, "mixed")
                for label in ("legacy-v3", "auto-on", "photo-cover"):
                    item = state["renders"][label]
                    value = copy.deepcopy(item["input"])
                    result = render_document(
                        cfg.presentation_storage,
                        "replay-" + label,
                        value,
                        item["result"]["assets"],
                    )
                    assert result["sha256"] == item["result"]["sha256"]
                state["repeat_hashes_identical"] = True
                persist()
            elif args.phase == "prime-ui":
                doc = await get(c, state["primary"])
                p = library()[0]["red"][24]
                doc = await edit(
                    c,
                    doc,
                    style="premium-dark",
                    palette=p["colors"],
                    palette_plan=p,
                    design_seed=0,
                    auto_design=False,
                    use_logo=True,
                    materials=[
                        m | {"enabled": False, "required": False}
                        for m in doc["draft"].get("materials", [])
                    ],
                    auto_decoration={
                        "version": 1,
                        "enabled": True,
                        "intensity": "NORMAL",
                    },
                )
                await render(c, doc, "ui-prime")
            elif args.phase == "approve":
                if not args.reviewed:
                    raise SystemExit("Inspect every final page before --reviewed")
                for label, r in state["renders"].items():
                    doc = await get(c, r["document"])
                    rev = next(v for v in doc["revisions"] if v["id"] == r["revision"])
                    if r["required"]:
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
                    assert hashlib.sha256(data).hexdigest() == r["result"]["sha256"]
                    (output / label / "presentation.pdf").write_bytes(data)
                state["reviewed"] = True
                persist()
                print("Authorized PDF hashes verified after visual review")
            elif args.phase == "verify":
                async with sessions() as db:
                    assert await other_documents(db) == state["other_drafts"]
                    state["catalog_after"] = await db.scalar(
                        text("SELECT count(*) FROM supplier_offers")
                    )
                foreign = httpx.AsyncClient(base_url="http://127.0.0.1:8000")
                if len(state["users"]) > 1:
                    foreign.cookies.update(state["users"][1]["cookies"])
                async with foreign:
                    if len(state["users"]) == 1:
                        u = await call(
                            foreign,
                            "POST",
                            "/api/auth/register",
                            json={
                                "name": "palette-foreign-" + state["run"],
                                "password": secrets.token_urlsafe(24),
                            },
                        )
                        state["users"].append(
                            {"id": u["id"], "cookies": dict(foreign.cookies)}
                        )
                        persist()
                    r = state["renders"]["auto-on"]
                    for url in (
                        f"/api/presentations/{r['document']}",
                        f"/api/presentations/{r['document']}/revisions/{r['revision']}/pdf?historical=true",
                        f"/api/presentations/{r['document']}/revisions/{r['revision']}/pages/1",
                    ):
                        assert (await foreign.get(url)).status_code == 404
                persist()
                print(
                    "Other user drafts preserved; foreign document/PDF/preview blocked"
                )
            else:
                for project in state["projects"]:
                    await call(c, "DELETE", "/api/projects/" + project)
                async with sessions.begin() as db:
                    await db.execute(
                        delete(User).where(
                            User.id.in_([u["id"] for u in state["users"]])
                        )
                    )
                storage = cfg.presentation_storage.resolve()
                for identifier in state["documents"]:
                    target = (storage / "documents" / identifier).resolve()
                    assert target.parent == storage / "documents"
                    if target.exists():
                        shutil.rmtree(target)
                for key in set(state["created_cache"]):
                    ref = key.removesuffix(".json")
                    if ref.endswith("-thumb.png"):
                        ref = ref.removesuffix("-thumb.png") + ".png"
                    async with sessions() as db:
                        count = await db.scalar(
                            select(func.count())
                            .select_from(PresentationDocument)
                            .where(cast(PresentationDocument.draft, Text).contains(ref))
                        )
                        count += await db.scalar(
                            select(func.count())
                            .select_from(PresentationRevision)
                            .where(
                                cast(PresentationRevision.input, Text).contains(ref)
                                | cast(PresentationRevision.result, Text).contains(ref)
                            )
                        )
                    target = (storage / key).resolve()
                    assert target.is_relative_to(storage / "assets/custom")
                    if not count and target.exists():
                        target.unlink()
                assert output.parent == (ROOT / ".local/palette-acceptance").resolve()
                shutil.rmtree(output)
                LEDGER.unlink()
                print("Only recorded disposable IDs and their files removed")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
