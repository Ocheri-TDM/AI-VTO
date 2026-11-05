"""Live project-collection and PDF V2 acceptance; exact-ID cleanup ledger.

prepare -> chain -> design -> inspect every preview -> approve --reviewed -> cleanup.
Only the recorded disposable users/projects are removed. Never print cookies.
"""

import argparse
import asyncio
import hashlib
import io
import json
import secrets
import shutil
import sys
import time
from pathlib import Path
from uuid import uuid4

import httpx
from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import delete, text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))
from app.config import Settings
from app.database.connection import create_database
from app.database.models import PresentationRevision, User
from app.domain.presentation import ACTIVE

LEDGER = ROOT / ".local/collection-v2-run.json"


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "phase",
        choices=[
            "prepare",
            "chain",
            "design",
            "rerender",
            "modern",
            "logo",
            "approve",
            "verify",
            "cleanup",
        ],
    )
    parser.add_argument("--reviewed", action="store_true")
    args = parser.parse_args()
    if args.phase == "prepare" and LEDGER.exists():
        raise SystemExit("Finish or clean the existing ledger before another prepare")
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
            "migration_from_legacy": False,
        }
    )
    output = ROOT / ".local/collection-v2-acceptance" / state["run"]
    output.mkdir(parents=True, exist_ok=True)
    engine, sessions = create_database(Settings().database_url)

    def persist():
        LEDGER.write_text(json.dumps(state, ensure_ascii=False, indent=2), "utf-8")

    async def call(c, method, url, **kwargs):
        response = await c.request(method, url, **kwargs)
        response.raise_for_status()
        return (
            response.json()
            if response.headers.get("content-type", "").startswith("application/json")
            else response.content
        )

    async def query(c, p, q):
        return (
            await call(c, "POST", f"/api/projects/{p}/requests", json={"query": q})
        )["research"]

    async def collection(c, p):
        return await call(c, "GET", f"/api/projects/{p}/collection")

    async def add(c, p, id, source=None):
        body = {"offer_id": id}
        if source:
            body["source_research_id"] = source
        return await call(c, "POST", f"/api/projects/{p}/collection/items", json=body)

    async def clear(c, p):
        current = await collection(c, p)
        return await call(
            c,
            "POST",
            f"/api/projects/{p}/collection/clear",
            json={"version": current["version"], "confirmed": True},
        )

    async def new_project(c, name, user=0):
        chat = await call(c, "POST", "/api/chats", json={"project_name": name})
        p = next(
            p
            for p in await call(c, "GET", "/api/projects")
            if p["chat_id"] == chat["id"]
        )
        state["projects"].append(p | {"user": user})
        persist()
        return p

    async def save(c, doc, **settings):
        draft = doc["draft"]
        return await call(
            c,
            "PATCH",
            "/api/presentations/" + doc["id"],
            json={
                "version": doc["version"],
                "settings": draft["settings"] | settings,
                "items": [
                    {
                        k: i[k]
                        for k in ["offer_id", "display_name", "caption", "image_url"]
                    }
                    for i in draft["items"]
                ],
                "category_order": draft["category_order"],
                "original_fallback_ids": draft["original_fallback_ids"],
            },
        )

    async def render(c, doc, label):
        revision = await call(
            c,
            "POST",
            f"/api/presentations/{doc['id']}/generate",
            json={"version": doc["version"]},
        )
        started = time.perf_counter()
        while True:
            await asyncio.sleep(0.5)
            doc = await call(c, "GET", "/api/presentations/" + doc["id"])
            rev = next(r for r in doc["revisions"] if r["id"] == revision["id"])
            if rev["status"] not in ACTIVE:
                break
            if time.perf_counter() - started > 330:
                raise TimeoutError(label)
        result = rev["result"]
        assert result.get("pdf_url") and not result.get("issues"), (label, rev)
        assert sorted(result["placements"]) == sorted(
            i["offer_id"] for i in doc["draft"]["items"]
        )
        folder = output / label
        folder.mkdir(exist_ok=True)
        for page in result["pages"]:
            (folder / f"page-{page['page']}.png").write_bytes(
                await call(c, "GET", page["preview"])
            )
        async with sessions() as db:
            row = await db.get(PresentationRevision, rev["id"])
            metrics = {
                k: row.result.get(k)
                for k in [
                    "asset_seconds",
                    "ai_seconds",
                    "ai_used",
                    "fallback_reason",
                    "render_seconds",
                    "preview_seconds",
                    "total_seconds",
                    "peak_rss_bytes",
                    "bytes",
                    "sha256",
                    "page_count",
                    "design_plan",
                ]
            }
        state.setdefault("renders", {})[label] = {
            "document": doc["id"],
            "revision": rev["id"],
            "version": rev["source_version"],
            "metrics": metrics,
        }
        persist()
        print(
            label,
            json.dumps({k: v for k, v in metrics.items() if k != "design_plan"}),
            flush=True,
        )
        return doc

    try:
        async with httpx.AsyncClient(
            base_url="http://127.0.0.1:8000",
            cookies=state["users"][0]["cookies"] if state["users"] else {},
            timeout=120,
        ) as c:
            p = state["projects"][0]["id"] if state["projects"] else None
            if args.phase == "prepare":
                user = await call(
                    c,
                    "POST",
                    "/api/auth/register",
                    json={
                        "name": "collection-v2-" + state["run"],
                        "password": secrets.token_urlsafe(24),
                    },
                )
                state["users"].append({"id": user["id"], "cookies": dict(c.cookies)})
                persist()
                project = await new_project(c, "Collection V2 acceptance")
                r = await query(c, project["id"], "ручки")
                ids = [i["id"] for i in r["products"][:2]]
                if len(ids) != 2:
                    raise ValueError(
                        "The live catalog needs two pen variants for this acceptance"
                    )
                for id in ids:
                    await add(c, project["id"], id, r["id"])
                state.update(
                    legacy_chat=project["chat_id"],
                    legacy_research=r["id"],
                    legacy_ids=ids,
                )
                async with sessions() as db:
                    state["catalog_before"] = dict(
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
                    state["documents_before"] = [
                        dict(r)
                        for r in (
                            await db.execute(
                                text(
                                    "select id,md5(draft::text) fingerprint from presentation_documents"
                                )
                            )
                        ).mappings()
                    ]
                persist()
                print("Fresh exact-ID acceptance ledger prepared")
            elif args.phase == "chain":
                current = await collection(c, p)
                assert current["selected_ids"] == state["legacy_ids"], current
                state["preexisting_selection_verified"] = True
                await clear(c, p)
                pens = await query(c, p, "ручки")
                ids = state["legacy_ids"]
                for id in ids:
                    await add(c, p, id, pens["id"])
                bag = await query(c, p, "рюкзаки")
                assert bag["view"]["selected_ids"] == ids
                bag_id = "vto:1823109"
                current = await add(c, p, bag_id, bag["id"])
                ids = ids + [bag_id]
                assert current["selected_ids"] == ids
                bottle = await query(c, p, "бутылки")
                bottle_id = bottle["products"][0]["id"]
                current = await add(c, p, bottle_id, bottle["id"])
                ids = ids + [bottle_id]
                hidden = await call(
                    c,
                    "PATCH",
                    f"/api/researches/{bottle['id']}/view",
                    json={
                        "version": bottle["view"]["version"],
                        "filters": {"min_price": 999999999},
                    },
                )
                assert hidden["products"] == []
                duplicate = await add(c, p, ids[0])
                assert (
                    duplicate["version"] == current["version"]
                    and duplicate["selected_ids"] == ids
                )
                assert (await collection(c, p))["selected_ids"] == ids
                doc = await call(
                    c,
                    "POST",
                    f"/api/projects/{p}/presentations",
                    json={"collection_version": current["version"]},
                )
                state["documents"].append(doc["id"])
                persist()
                assert [i["offer_id"] for i in doc["draft"]["items"]] == ids
                assert (await collection(c, p))["selected_ids"] == ids
                await call(c, "DELETE", f"/api/projects/{p}/collection/items/{ids[0]}")
                state["chain_ids"] = ids[1:]
                assert (await collection(c, p))["selected_ids"] == state["chain_ids"]
                other = await new_project(c, "Independent collection")
                assert not (await collection(c, other["id"]))["selected_ids"]
                assert (await collection(c, p))["selected_ids"] == state["chain_ids"]
                # Late search responses cannot replace the authoritative collection.
                old = await call(c, "GET", "/api/researches/" + pens["id"])
                assert old["view"]["selected_ids"] == state["chain_ids"]
                late = await c.patch(
                    f"/api/researches/{pens['id']}/view",
                    json={"version": old["view"]["version"], "selected_ids": []},
                )
                assert late.status_code == 409
                async with httpx.AsyncClient(
                    base_url="http://127.0.0.1:8000",
                    cookies=state["users"][0]["cookies"],
                    timeout=60,
                ) as tab:
                    await asyncio.gather(add(c, p, ids[0]), add(tab, p, "vto:1823108"))
                assert len((await collection(c, p))["selected_ids"]) == 5
                for id in [ids[0], "vto:1823108"]:
                    await call(c, "DELETE", f"/api/projects/{p}/collection/items/{id}")
                await call(
                    c,
                    "PATCH",
                    f"/api/researches/{bottle['id']}/view",
                    json={"version": hidden["view"]["version"], "restore": "undo"},
                )
                assert (await collection(c, p))["selected_ids"] == state["chain_ids"]
                invalid = await c.post(
                    f"/api/projects/{p}/collection/items",
                    json={"offer_id": "vto:does-not-exist"},
                )
                assert invalid.status_code == 422
                async with httpx.AsyncClient(
                    base_url="http://127.0.0.1:8000", timeout=60
                ) as foreign:
                    user = await call(
                        foreign,
                        "POST",
                        "/api/auth/register",
                        json={
                            "name": "collection-v2-other-" + state["run"],
                            "password": secrets.token_urlsafe(24),
                        },
                    )
                    state["users"].append(
                        {"id": user["id"], "cookies": dict(foreign.cookies)}
                    )
                    persist()
                    assert (
                        await foreign.get(f"/api/projects/{p}/collection")
                    ).status_code == 404
                    assert (
                        await foreign.get("/api/presentations/" + doc["id"])
                    ).status_code == 404
                state["chain_result"] = {
                    "queries": ["ручки", "рюкзаки", "бутылки"],
                    "snapshot_ids": ids,
                    "final_ids": state["chain_ids"],
                    "two_tabs": True,
                    "foreign": 404,
                    "late_replace": 409,
                }
                persist()
                print("Twelve-step real collection chain verified", flush=True)
            elif args.phase == "design":
                design_project = await new_project(c, "PDF V2 control")
                dp = design_project["id"]
                ids12 = [
                    "vto:1823109",
                    "vto:1823108",
                    "vto:1823107",
                    "vto:1963692",
                    "vto:1963685",
                    "vto:1948520",
                    "vto:1950874",
                    "vto:1961375",
                    "vto:1957351",
                    "vto:1963311",
                    "vto:1967797",
                    "vto:1966794",
                ]
                for count in [1, 3, 12]:
                    await clear(c, dp)
                    for id in ids12[:count]:
                        await add(c, dp, id)
                    current = await collection(c, dp)
                    doc = await call(
                        c,
                        "POST",
                        f"/api/projects/{dp}/presentations",
                        json={"collection_version": current["version"]},
                    )
                    state["documents"].append(doc["id"])
                    persist()
                    for style in ["editorial", "premium-dark", "modern-graphic"]:
                        doc = await save(
                            c,
                            doc,
                            style=style,
                            design_seed=0,
                            palette=["#163E52", "#C9AB78"],
                            client="Контроль VTO",
                            title="",
                        )
                        doc = await render(c, doc, f"{count}-{style}")
                    if count == 3:
                        state["three_document"] = doc["id"]
                        doc = await save(c, doc, style="editorial", auto_design=True)
                        doc = await render(c, doc, "3-ai")
                        doc = await call(
                            c,
                            "POST",
                            f"/api/presentations/{doc['id']}/alternate",
                            json={"version": doc["version"]},
                        )
                        doc = await render(c, doc, "3-alternate")
                    persist()
            elif args.phase == "rerender":
                state.setdefault("initial_design_metrics", state["renders"].copy())
                for count in [1, 3, 12]:
                    entry = state["renders"][f"{count}-editorial"]
                    doc = await call(
                        c, "GET", "/api/presentations/" + entry["document"]
                    )
                    for style in ["editorial", "premium-dark", "modern-graphic"]:
                        doc = await save(
                            c, doc, style=style, design_seed=0, auto_design=False
                        )
                        doc = await render(c, doc, f"{count}-{style}")
                    if count == 3:
                        doc = await save(c, doc, style="editorial", auto_design=True)
                        doc = await render(c, doc, "3-ai")
                        doc = await call(
                            c,
                            "POST",
                            f"/api/presentations/{doc['id']}/alternate",
                            json={"version": doc["version"]},
                        )
                        await render(c, doc, "3-alternate")
            elif args.phase == "modern":
                for count in [1, 3, 12]:
                    doc = await call(
                        c,
                        "GET",
                        "/api/presentations/"
                        + state["renders"][f"{count}-modern-graphic"]["document"],
                    )
                    doc = await save(
                        c,
                        doc,
                        style="modern-graphic",
                        design_seed=0,
                        auto_design=False,
                        use_logo=False,
                    )
                    await render(c, doc, f"{count}-modern-graphic")
            elif args.phase == "logo":
                doc = await call(
                    c, "GET", "/api/presentations/" + state["three_document"]
                )
                image = Image.new("RGBA", (500, 160), (0, 0, 0, 0))
                draw = ImageDraw.Draw(image)
                draw.rounded_rectangle((10, 10, 490, 150), radius=18, fill="#163E52")
                draw.text(
                    (42, 48),
                    "STUDIO / TEST",
                    fill="white",
                    font=ImageFont.truetype(
                        str(ROOT / "apps/api/app/assets/fonts/NotoSans-Bold.ttf"), 48
                    ),
                )
                data = io.BytesIO()
                image.save(data, format="PNG")
                doc = await call(
                    c,
                    "POST",
                    f"/api/presentations/{doc['id']}/logo",
                    params={"version": doc["version"]},
                    content=data.getvalue(),
                )
                for use in [True, False]:
                    doc = await save(
                        c,
                        doc,
                        style="premium-dark",
                        design_seed=0,
                        auto_design=False,
                        use_logo=use,
                    )
                    doc = await render(c, doc, "3-logo-on" if use else "3-logo-off")
            elif args.phase == "approve":
                assert args.reviewed, (
                    "Visually inspect every rendered page before --reviewed"
                )
                for label, entry in state["renders"].items():
                    doc = await call(
                        c, "GET", "/api/presentations/" + entry["document"]
                    )
                    rev = next(
                        r for r in doc["revisions"] if r["id"] == entry["revision"]
                    )
                    if rev["status"] == "NEEDS_REVIEW":
                        await call(
                            c,
                            "POST",
                            f"/api/presentations/{doc['id']}/revisions/{rev['id']}/approve",
                            json={
                                "asset_hashes": [
                                    a["hash"]
                                    for a in rev["result"]["assets"].values()
                                    if a["review_required"]
                                ]
                            },
                        )
                    data = await call(
                        c, "GET", rev["result"]["pdf_url"] + "?historical=true"
                    )
                    assert hashlib.sha256(data).hexdigest() == rev["result"]["sha256"]
                    (output / label / "presentation.pdf").write_bytes(data)
                state["reviewed"] = True
                persist()
                print("Reviewed PDFs downloaded with matching hashes")
            elif args.phase == "verify":
                assert (await collection(c, p))["selected_ids"] == state["chain_ids"]
                async with sessions() as db:
                    after = dict(
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
                    assert after == state["catalog_before"], after
                    for entry in state["documents_before"]:
                        found = (
                            await db.execute(
                                text(
                                    "select md5(draft::text) from presentation_documents where id=:id"
                                ),
                                {"id": entry["id"]},
                            )
                        ).scalar_one()
                        assert found == entry["fingerprint"], (
                            "Existing document changed"
                        )
                print(
                    "Collection, catalog and existing documents preserved after restart"
                )
            else:
                assert state["users"]
                for project in state["projects"]:
                    await call(c, "DELETE", "/api/projects/" + project["id"])
                async with sessions.begin() as db:
                    await db.execute(
                        delete(User).where(
                            User.id.in_([u["id"] for u in state["users"]])
                        )
                    )
                storage = Settings().presentation_storage.resolve()
                for id in state["documents"]:
                    target = (storage / "documents" / id).resolve()
                    assert (
                        target.parent == storage / "documents"
                        and target.is_relative_to(storage)
                    )
                    if target.exists():
                        shutil.rmtree(target)
                assert (
                    output.parent
                    == (ROOT / ".local/collection-v2-acceptance").resolve()
                )
                shutil.rmtree(output)
                LEDGER.unlink()
                print("Exact-ID V2 acceptance cleanup completed")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
