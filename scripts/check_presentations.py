"""Real VTO/HTTP acceptance with a ledger, review boundary and exact-ID cleanup.

prepare -> inspect rendered pages -> approve --reviewed -> logo -> cleanup.
The ledger contains disposable session cookies and is never printed or committed.
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
from sqlalchemy import delete, select, text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))
from app.config import Settings
from app.database.connection import create_database
from app.database.models import ResearchRecord, SupplierOffer, User

LEDGER = ROOT / ".local/presentation-acceptance-run.json"


def persist(state):
    LEDGER.write_text(json.dumps(state, ensure_ascii=False, indent=2), "utf-8")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "phase",
        choices=["prepare", "regenerate", "rerender", "approve", "logo", "cleanup"],
    )
    parser.add_argument("--prepared-offer")
    parser.add_argument("--prepared-file")
    parser.add_argument("--base", default="http://127.0.0.1:8001")
    parser.add_argument("--reviewed", action="store_true")
    args = parser.parse_args()
    engine, sessions = create_database(Settings().database_url)
    state = (
        json.loads(LEDGER.read_text("utf-8"))
        if LEDGER.exists()
        else {
            "users": [],
            "documents": [],
            "projects": [],
            "measurements": {},
            "run": uuid4().hex,
        }
    )
    output = ROOT / ".local/presentation-acceptance" / state["run"]
    output.mkdir(parents=True, exist_ok=True)

    async def call(c, method, path, **kwargs):
        result = await c.request(method, path, **kwargs)
        result.raise_for_status()
        return result

    async def fingerprint():
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

    async def account(label):
        c = httpx.AsyncClient(base_url=args.base, timeout=120)
        user = (
            await call(
                c,
                "POST",
                "/api/auth/register",
                json={
                    "name": "presentation-check-" + label + "-" + state["run"],
                    "password": secrets.token_urlsafe(24),
                },
            )
        ).json()
        state["users"].append({"id": user["id"], "cookies": dict(c.cookies)})
        await call(
            c, "POST", "/api/chats", json={"project_name": "Контроль VTO · " + label}
        )
        project = (await call(c, "GET", "/api/projects")).json()[0]["id"]
        state["projects"].append({"id": project, "user": len(state["users"]) - 1})
        persist(state)
        return c, project

    async def wait(c, doc):
        start = time.perf_counter()
        while True:
            value = (await call(c, "GET", f"/api/presentations/{doc}")).json()
            revision = value["revisions"][0]
            if revision["status"] not in [
                "QUEUED",
                "PREPARING_ASSETS",
                "RENDERING",
                "VALIDATING",
            ]:
                return value
            if time.perf_counter() - start > 420:
                raise TimeoutError("Presentation acceptance timed out")
            await asyncio.sleep(0.7)

    async def save_edit(c, doc, **settings):
        draft = doc["draft"]
        body = {
            "version": doc["version"],
            "settings": draft["settings"] | settings,
            "items": [
                {k: i[k] for k in ["offer_id", "display_name", "caption", "image_url"]}
                for i in draft["items"]
            ],
            "category_order": draft["category_order"],
            "original_fallback_ids": draft["original_fallback_ids"],
        }
        return (
            await call(c, "PATCH", f"/api/presentations/{doc['id']}", json=body)
        ).json()

    async def artifacts(c, doc, label):
        rev = doc["revisions"][0]
        folder = output / label
        folder.mkdir(exist_ok=True)
        for page in rev["result"].get("pages", []):
            data = (await call(c, "GET", page["preview"])).content
            (folder / f"page-{page['page']}.png").write_bytes(data)
        for offer, asset in rev["result"].get("assets", {}).items():
            data = (await call(c, "GET", asset["url"])).content
            (folder / (offer.replace(":", "-") + ".png")).write_bytes(data)
        value = {
            "status": rev["status"],
            "source_version": rev["source_version"],
            "items": len(doc["draft"]["items"]),
            "page_count": rev["result"].get("page_count"),
            "issues": rev["result"].get("issues"),
            "bytes": rev["result"].get("bytes"),
            "sha256": rev["result"].get("sha256"),
        }
        # Capture worker timings by exact revision ID, not estimated HTTP 202 time.
        async with sessions() as db:
            row = (
                await db.execute(
                    text("SELECT result FROM presentation_revisions WHERE id=:id"),
                    {"id": rev["id"]},
                )
            ).scalar_one()
            value.update(
                {
                    k: row.get(k)
                    for k in [
                        "asset_seconds",
                        "render_seconds",
                        "preview_seconds",
                        "total_seconds",
                        "peak_rss_bytes",
                    ]
                }
            )
        state["measurements"][label] = value
        persist(state)
        print(label, json.dumps(value, ensure_ascii=True))
        return rev

    try:
        if args.phase == "prepare":
            if state["users"]:
                raise ValueError(
                    "An acceptance ledger already exists; finish or clean it first"
                )
            state["commercial_before"] = await fingerprint()
            c, project = await account("main")
            other, other_project = await account("other")
            try:
                r = (
                    await call(
                        c,
                        "POST",
                        f"/api/projects/{project}/requests",
                        json={"query": "ручки бутылки рюкзаки термокружки"},
                    )
                ).json()["research"]
                page2 = (
                    await call(
                        c,
                        "GET",
                        f"/api/researches/{r['id']}/products",
                        params={"cursor": r["next_cursor"]},
                    )
                ).json()
                async with sessions() as db:
                    record = await db.get(ResearchRecord, r["id"])
                    pool = record.payload["pool_offer_ids"]
                    offers = (
                        await db.scalars(
                            select(SupplierOffer).where(SupplierOffer.id.in_(pool))
                        )
                    ).all()
                by_id = {o.id: o for o in offers}
                known = [
                    id
                    for id in ["vto:1823109", "vto:1823108", "vto:1823107"]
                    if id in by_id
                ]
                assert len(known) == 3, (
                    "Known Melango variants must exist in the real pool"
                )
                selected = list(known)
                selected.extend([r["products"][0]["id"], page2["products"][0]["id"]])
                for category in ["bottle", "pen", "backpack", "thermomug"]:
                    candidates = [
                        o.id
                        for o in offers
                        if o.payload.get("category") == category
                        and o.payload.get("primary_image")
                    ]
                    selected.extend(candidates[:8])
                selected = list(dict.fromkeys(selected))
                selected.extend(
                    o.id
                    for o in offers
                    if o.id not in selected and o.payload.get("primary_image")
                )
                state["research"] = r["id"]
                state["project"] = project
                state["selection_ids"] = selected[:30]
                persist(state)
                for count in [1, 3, 12, 30]:
                    ids = selected[:count]
                    r = (
                        await call(
                            c,
                            "PATCH",
                            f"/api/researches/{r['id']}/view",
                            json={
                                "version": r["view"]["version"],
                                "selected_ids": ids,
                                "filters": r["view"]["filters"]
                                | {"min_price": 999999999},
                            },
                        )
                    ).json()
                    assert r["hidden_selected_count"] == count
                    doc = (
                        await call(
                            c,
                            "POST",
                            f"/api/projects/{project}/presentations",
                            json={
                                "research_id": r["id"],
                                "selection_version": r["view"]["version"],
                            },
                        )
                    ).json()
                    assert [i["offer_id"] for i in doc["draft"]["items"]] == ids
                    state["documents"].append(
                        {"id": doc["id"], "label": str(count), "user": 0}
                    )
                    persist(state)
                    doc = await save_edit(
                        c,
                        doc,
                        title="Подарки для команды",
                        client="Контроль VTO",
                        palette=["#163E52"] if count == 1 else ["#163E52", "#B79B73"],
                    )
                    await call(
                        c,
                        "POST",
                        f"/api/presentations/{doc['id']}/generate",
                        json={"version": doc["version"]},
                    )
                    if count == 1:
                        # A second user's document queues independently while ordinary
                        # index search remains available during actual CPU preparation.
                        rr = (
                            await call(
                                other,
                                "POST",
                                f"/api/projects/{other_project}/requests",
                                json={"query": "рюкзаки"},
                            )
                        ).json()["research"]
                        rr = (
                            await call(
                                other,
                                "PATCH",
                                f"/api/researches/{rr['id']}/view",
                                json={
                                    "version": rr["view"]["version"],
                                    "selected_ids": ["vto:1823108"],
                                },
                            )
                        ).json()
                        foreign_doc = (
                            await call(
                                other,
                                "POST",
                                f"/api/projects/{other_project}/presentations",
                                json={
                                    "research_id": rr["id"],
                                    "selection_version": rr["view"]["version"],
                                },
                            )
                        ).json()
                        state["documents"].append(
                            {"id": foreign_doc["id"], "label": "other", "user": 1}
                        )
                        persist(state)
                        await call(
                            other,
                            "POST",
                            f"/api/presentations/{foreign_doc['id']}/generate",
                            json={"version": foreign_doc["version"]},
                        )
                        start = time.perf_counter()
                        check = await call(
                            other,
                            "POST",
                            f"/api/projects/{other_project}/requests",
                            json={"query": "бутылки"},
                        )
                        state["search_during_render_seconds"] = round(
                            time.perf_counter() - start, 3
                        )
                        assert (
                            check.status_code == 202
                            and (
                                await other.get(f"/api/presentations/{doc['id']}")
                            ).status_code
                            == 404
                        )
                    final = await wait(c, doc["id"])
                    await artifacts(c, final, str(count))
                foreign_final = await wait(other, foreign_doc["id"])
                await artifacts(other, foreign_final, "other")
                state["commercial_after_prepare"] = await fingerprint()
                persist(state)
            finally:
                await c.aclose()
                await other.aclose()
        else:
            clients = [
                httpx.AsyncClient(base_url=args.base, cookies=u["cookies"], timeout=120)
                for u in state["users"]
            ]
            try:
                if args.phase == "rerender":
                    from app.domain.presentation import short_display_name

                    state["previous_full_model_measurements"] = state[
                        "measurements"
                    ].copy()
                    for entry in state["documents"]:
                        c = clients[entry["user"]]
                        doc = (
                            await call(c, "GET", f"/api/presentations/{entry['id']}")
                        ).json()
                        body = {
                            "version": doc["version"],
                            "settings": doc["draft"]["settings"],
                            "category_order": doc["draft"]["category_order"],
                            "original_fallback_ids": doc["draft"][
                                "original_fallback_ids"
                            ],
                            "items": [
                                {
                                    k: i[k]
                                    for k in [
                                        "offer_id",
                                        "display_name",
                                        "caption",
                                        "image_url",
                                    ]
                                }
                                | {"display_name": short_display_name(i["source_name"])}
                                for i in doc["draft"]["items"]
                            ],
                        }
                        doc = (
                            await call(
                                c, "PATCH", f"/api/presentations/{doc['id']}", json=body
                            )
                        ).json()
                        await call(
                            c,
                            "POST",
                            f"/api/presentations/{doc['id']}/generate",
                            json={"version": doc["version"]},
                        )
                        doc = await wait(c, doc["id"])
                        await artifacts(c, doc, entry["label"])
                    persist(state)
                elif args.phase == "regenerate":
                    state["initial_small_model_measurements"] = state[
                        "measurements"
                    ].copy()
                    for entry in state["documents"]:
                        c = clients[entry["user"]]
                        doc = (
                            await call(c, "GET", f"/api/presentations/{entry['id']}")
                        ).json()
                        doc = (
                            await call(
                                c,
                                "POST",
                                f"/api/presentations/{doc['id']}/refresh",
                                json={"version": doc["version"]},
                            )
                        ).json()
                        if (
                            args.prepared_offer
                            and args.prepared_file
                            and any(
                                i["offer_id"] == args.prepared_offer
                                for i in doc["draft"]["items"]
                            )
                        ):
                            doc = (
                                await call(
                                    c,
                                    "POST",
                                    f"/api/presentations/{doc['id']}/items/{args.prepared_offer}/image",
                                    params={"version": doc["version"]},
                                    content=Path(args.prepared_file).read_bytes(),
                                    headers={"Content-Type": "image/png"},
                                )
                            ).json()
                        if entry["label"] == "1":
                            doc = await save_edit(c, doc, cover=False)
                        await call(
                            c,
                            "POST",
                            f"/api/presentations/{doc['id']}/generate",
                            json={"version": doc["version"]},
                        )
                        doc = await wait(c, doc["id"])
                        await artifacts(c, doc, entry["label"])
                    persist(state)
                elif args.phase == "approve":
                    if not args.reviewed:
                        raise ValueError(
                            "Inspect every rendered page first, then use --reviewed"
                        )
                    for entry in state["documents"]:
                        c = clients[entry["user"]]
                        doc = (
                            await call(c, "GET", f"/api/presentations/{entry['id']}")
                        ).json()
                        rev = doc["revisions"][0]
                        if rev["status"] == "NEEDS_REVIEW":
                            assert not rev["result"].get("issues"), rev["result"].get(
                                "issues"
                            )
                            hashes = [
                                a["hash"]
                                for a in rev["result"]["assets"].values()
                                if a["review_required"]
                            ]
                            doc = (
                                await call(
                                    c,
                                    "POST",
                                    f"/api/presentations/{doc['id']}/revisions/{rev['id']}/approve",
                                    json={"asset_hashes": hashes},
                                )
                            ).json()
                            rev = doc["revisions"][0]
                        assert rev["status"] == "READY"
                        data = (await call(c, "GET", rev["result"]["pdf_url"])).content
                        assert (
                            hashlib.sha256(data).hexdigest() == rev["result"]["sha256"]
                        )
                        (output / entry["label"] / "presentation.pdf").write_bytes(data)
                        assert (
                            await clients[1 - entry["user"]].get(
                                rev["result"]["pdf_url"]
                            )
                        ).status_code == 404
                    state["reviewed"] = True
                    persist(state)
                elif args.phase == "logo":
                    assert state.get("reviewed")
                    entry = next(e for e in state["documents"] if e["label"] == "3")
                    c = clients[0]
                    doc = (
                        await call(c, "GET", f"/api/presentations/{entry['id']}")
                    ).json()
                    before = doc["draft"]["items"]
                    image = Image.new("RGBA", (500, 160), (0, 0, 0, 0))
                    draw = ImageDraw.Draw(image)
                    draw.rounded_rectangle(
                        (15, 15, 485, 145), radius=24, fill="#163e52"
                    )
                    draw.text(
                        (45, 48),
                        "STUDIO / TEST",
                        fill="white",
                        font=ImageFont.truetype(
                            str(ROOT / "apps/api/app/assets/fonts/NotoSans-Bold.ttf"),
                            48,
                        ),
                    )
                    data = io.BytesIO()
                    image.save(data, format="PNG")
                    doc = (
                        await call(
                            c,
                            "POST",
                            f"/api/presentations/{doc['id']}/logo",
                            params={"version": doc["version"]},
                            content=data.getvalue(),
                            headers={"Content-Type": "image/png"},
                        )
                    ).json()
                    for label, use in [("logo-on", True), ("logo-off", False)]:
                        doc = await save_edit(c, doc, use_logo=use)
                        await call(
                            c,
                            "POST",
                            f"/api/presentations/{doc['id']}/generate",
                            json={"version": doc["version"]},
                        )
                        doc = await wait(c, doc["id"])
                        rev = await artifacts(c, doc, label)
                        assert rev["status"] == "READY", rev["status"]
                        data = (await call(c, "GET", rev["result"]["pdf_url"])).content
                        (output / label / "presentation.pdf").write_bytes(data)
                        assert doc["draft"]["items"] == before
                    r = (
                        await call(c, "GET", f"/api/researches/{state['research']}")
                    ).json()
                    assert (
                        r["view"]["selected_ids"] == state["selection_ids"]
                        and r["filtered_total"] == 0
                    )
                    state["checks"] = {
                        "logo_revisions": True,
                        "selection_unchanged": True,
                        "api_reload": True,
                        "cross_user_files": True,
                    }
                    state["commercial_after"] = await fingerprint()
                    persist(state)
                else:
                    for p in state["projects"]:
                        (
                            await call(
                                clients[p["user"]], "DELETE", f"/api/projects/{p['id']}"
                            )
                        )
                    async with sessions.begin() as db:
                        await db.execute(
                            delete(User).where(
                                User.id.in_([u["id"] for u in state["users"]])
                            )
                        )
                    storage = Settings().presentation_storage.resolve()
                    for entry in state["documents"]:
                        target = (storage / "documents" / entry["id"]).resolve()
                        assert (
                            target.parent == storage / "documents"
                            and target.is_relative_to(storage)
                        )
                        if target.exists():
                            shutil.rmtree(target)
                    shutil.rmtree(output)
                    LEDGER.unlink()
                    print("Exact-ID acceptance resources removed")
            finally:
                for c in clients:
                    await c.aclose()
    finally:
        await engine.dispose()
    if args.phase != "cleanup":
        print("evidence", output)


if __name__ == "__main__":
    asyncio.run(main())
