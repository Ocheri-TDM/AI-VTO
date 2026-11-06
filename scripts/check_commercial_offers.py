"""Real local HTTP/PostgreSQL acceptance. Ledger contains secrets; never print it.

prepare -> run -> check_commercial_offers_browser.py -> cleanup.
Only exact ledger-owned users/projects/documents are disposable.
"""

import argparse
import asyncio
import copy
import hashlib
import io
import json
import secrets
import shutil
import sys
import time
from contextlib import closing
from pathlib import Path
from uuid import uuid4

import httpx
import openpyxl
import pypdfium2 as pdfium
from sqlalchemy import delete, select, text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))
from app.config import Settings
from app.database.connection import create_database
from app.database.models import PresentationDocument, PresentationRevision, User

LEDGER = ROOT / ".local/commercial-offers-ledger.json"
PUBLIC = ROOT / "docs/screenshots/commercial-offers"
FIXTURES = ROOT / "apps/api/tests/fixtures/commercial-offers"


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), "utf-8")


def edit_body(d):
    return {
        "version": d["version"],
        "title": d["draft"]["settings"]["title"],
        "company": d["draft"]["settings"]["client"],
        "use_logo": True,
        "use_banner": True,
        "items": [
            {
                k: i[k]
                for k in (
                    "id",
                    "title",
                    "sku",
                    "sku_note",
                    "unit_price",
                    "quantity",
                    "main_image",
                )
            }
            for i in d["draft"]["items"]
        ],
    }


async def call(c, method, path, **kw):
    r = await c.request(method, path, **kw)
    if not r.is_success:
        # URLs may contain a capability; do not print request/response objects.
        raise AssertionError(
            f"HTTP {r.status_code}; route family {path.split('/')[2:3]}; {r.text[:300] if '/public/' not in path else 'public request failed'}"
        )
    return r.json() if r.status_code != 204 else None


async def fingerprint(sessions):
    async with sessions() as db:
        return dict(
            (
                await db.execute(
                    text(
                        "SELECT count(*) offers,md5(string_agg(o.id||o.product_id||o.source_url||coalesce(o.payload::text,'')||coalesce(p.payload::text,'')||coalesce(p.observed_at::text,''),'' ORDER BY o.id)) fingerprint FROM supplier_offers o LEFT JOIN product_observations p ON p.offer_id=o.id"
                    )
                )
            )
            .mappings()
            .one()
        )


async def immutable_data(sessions):
    result = {}
    async with sessions() as db:
        for table in (
            "product_observations",
            "availability_observations",
            "presentation_documents",
            "presentation_revisions",
        ):
            order = "offer_id" if table == "product_observations" else "id"
            result[table] = dict(
                (
                    await db.execute(
                        text(
                            f"SELECT count(*) count, md5(string_agg(row_to_json(t)::text,'' ORDER BY {order})) fingerprint FROM {table} t"
                        )
                    )
                )
                .mappings()
                .one()
            )
    return result


async def ready(c, path, version):
    end = time.monotonic() + 330
    while time.monotonic() < end:
        d = await call(c, "GET", path)
        revisions = [r for r in d["revisions"] if r["source_version"] == version]
        if revisions and revisions[0]["status"] in ("READY", "FAILED", "CANCELLED"):
            assert revisions[0]["status"] == "READY", revisions[0]["error"]
            return d, revisions[0]
        await asyncio.sleep(1.4)
    raise TimeoutError("Document worker did not finish")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "phase", choices=["prepare", "run", "race", "verify", "cleanup"]
    )
    phase = parser.parse_args().phase
    state = (
        json.loads(LEDGER.read_text("utf-8"))
        if LEDGER.exists()
        else {"run": uuid4().hex, "users": [], "projects": [], "documents": []}
    )
    settings = Settings()
    engine, sessions = create_database(settings.database_url)
    evidence = {
        "checks": [],
        "local_only": True,
        "external_https": "NOT TESTED",
        "reference_pdf_xlsx": "NOT VERIFIED",
    }

    def check(label, valid):
        assert valid, label
        evidence["checks"].append({"check": label, "status": "PASS"})

    try:
        async with (
            httpx.AsyncClient(base_url="http://127.0.0.1:8000", timeout=180) as c,
            httpx.AsyncClient(base_url="http://127.0.0.1:8000", timeout=180) as guest,
        ):
            if phase == "prepare":
                assert not LEDGER.exists()
                state["catalog_before"] = await fingerprint(sessions)
                state["data_before"] = await immutable_data(sessions)
                for index in range(2):
                    c.cookies.clear()
                    user = await call(
                        c,
                        "POST",
                        "/api/auth/register",
                        json={
                            "name": "commercial-offer-"
                            + state["run"]
                            + "-"
                            + str(index),
                            "password": secrets.token_urlsafe(24),
                        },
                    )
                    state["users"].append(
                        {"id": user["id"], "cookies": dict(c.cookies)}
                    )
                    save(LEDGER, state)
                c.cookies.clear()
                c.cookies.update(state["users"][0]["cookies"])
                chat = await call(
                    c,
                    "POST",
                    "/api/chats",
                    json={"project_name": "Каталог выбранных товаров"},
                )
                project = next(
                    p
                    for p in await call(c, "GET", "/api/projects")
                    if p["chat_id"] == chat["id"]
                )
                state["project"] = project
                state["projects"].append(project["id"])
                save(LEDGER, state)
                selections = []
                for query, preferred in [
                    ("ручки от 250 шт", "vto:1934569"),
                    ("рюкзаки от 100 шт", "vto:1823109"),
                    ("бутылки", "vto:1883738"),
                ]:
                    started = time.perf_counter()
                    response = await call(
                        c,
                        "POST",
                        f"/api/projects/{project['id']}/requests",
                        json={"query": query},
                    )
                    r = response["research"]
                    products = r["products"]
                    assert products, "Expected populated catalog candidates"
                    item = next(
                        (i for i in products if i["id"] == preferred),
                        next(
                            i
                            for i in products
                            if i.get("primary_image") or i.get("images")
                        ),
                    )
                    selection = await call(
                        c,
                        "POST",
                        f"/api/projects/{project['id']}/collection/items",
                        json={"offer_id": item["id"], "source_research_id": r["id"]},
                    )
                    selections.append(
                        {
                            "query": query,
                            "offer_id": item["id"],
                            "research_id": r["id"],
                            "seconds": round(time.perf_counter() - started, 3),
                        }
                    )
                    state["selections"] = selections
                    save(LEDGER, state)
                assert len(selection["selected_ids"]) == 3
                print("Prepared isolated owner, second user and 3 real VTO selections")
                return
            assert state["users"] and state["projects"]
            c.cookies.update(state["users"][0]["cookies"])
            check(
                "exact disposable owner",
                (await call(c, "GET", "/api/auth/me"))["id"] == state["users"][0]["id"],
            )
            if phase == "race":
                collection = await call(
                    c, "GET", f"/api/projects/{state['project']['id']}/collection"
                )
                d = await call(
                    c,
                    "POST",
                    f"/api/projects/{state['project']['id']}/offers",
                    json={"collection_version": collection["version"]},
                )
                state["documents"].append(d["id"])
                save(LEDGER, state)
                path = "/api/offers/" + d["id"]
                body = edit_body(d)
                other = copy.deepcopy(body)
                other["title"] = "Проверка параллельного сохранения"
                responses = await asyncio.gather(
                    c.patch(path, json=body), c.patch(path, json=other)
                )
                check(
                    "real PostgreSQL simultaneous CAS 200/409",
                    sorted(r.status_code for r in responses) == [200, 409],
                )
                d = await call(c, "GET", path)
                action = {
                    "version": d["version"],
                    "request_id": str(uuid4()),
                    "share_epoch": 0,
                }
                responses = await asyncio.gather(
                    c.post(path + "/publish", json=action),
                    c.post(path + "/publish", json=action),
                )
                check(
                    "double publish both accepted",
                    all(r.status_code == 202 for r in responses),
                )
                d = await call(c, "GET", path)
                check(
                    "double publish single durable revision", len(d["revisions"]) == 1
                )
                await call(
                    c,
                    "POST",
                    path + "/revoke",
                    json={"version": d["version"], "epoch": 0},
                )
                d, _ = await ready(c, path, d["version"])
                check(
                    "worker cannot undo revoke",
                    not d["share"]["active"]
                    and d["share"]["pending_revision_id"] is None,
                )
                await call(c, "POST", path + "/publish", json=action)
                check(
                    "stale idempotent replay cannot resurrect link",
                    not (await call(c, "GET", path))["share"]["active"],
                )
                check(
                    "catalog preserved during concurrent exports",
                    await fingerprint(sessions) == state["catalog_before"],
                )
                save(PUBLIC / "concurrency.json", evidence)
                print(
                    "Real PostgreSQL concurrency acceptance:",
                    len(evidence["checks"]),
                    "checks",
                )
                return
            if phase == "verify":
                d = await call(c, "GET", "/api/offers/" + state["primary"])
                check(
                    "recover same link after API restart",
                    d["share"]["local_preview_path"] == state["public_path"],
                )
                check(
                    "anonymous link after restart",
                    (
                        await guest.get(
                            "/api/public/offers/"
                            + state["public_path"].rsplit("/", 1)[1]
                        )
                    ).status_code
                    == 200,
                )
                protected = json.loads(
                    (
                        ROOT
                        / "docs/screenshots/geist-ui/pdf-and-dependencies-before.json"
                    ).read_text("utf-8")
                )
                archive_stubs = {
                    "apps/api/app/application/presentation_render.py",
                    "apps/api/app/application/presentation_layouts.py",
                    "apps/api/app/application/presentation_design.py",
                    "apps/api/app/domain/presentation_design.py",
                }
                protected = {
                    p: digest
                    for p, digest in protected.items()
                    if p not in archive_stubs
                }
                check(
                    "historical assets, PDFs and dependencies preserved",
                    all(
                        (ROOT / p).is_file()
                        and hashlib.sha256((ROOT / p).read_bytes()).hexdigest()
                        == digest
                        for p, digest in protected.items()
                    ),
                )
                baseline = state["data_before"]
                stored = {}
                async with sessions() as db:
                    for table, predicate in [
                        ("product_observations", "TRUE"),
                        ("availability_observations", "TRUE"),
                        ("presentation_documents", "user_id != :user"),
                        (
                            "presentation_revisions",
                            "document_id IN (SELECT id FROM presentation_documents WHERE user_id != :user)",
                        ),
                    ]:
                        order = "offer_id" if table == "product_observations" else "id"
                        value = dict(
                            (
                                await db.execute(
                                    text(
                                        f"SELECT count(*) count, md5(string_agg(to_jsonb(t)::text,'' ORDER BY {order})) jsonb, md5(string_agg(row_to_json(t)::text,'' ORDER BY {order})) row_json FROM {table} t WHERE {predicate}"
                                    ),
                                    {"user": state["users"][0]["id"]},
                                )
                            )
                            .mappings()
                            .one()
                        )
                        stored[table] = value
                        check(
                            table + " baseline unchanged",
                            value["count"] == baseline[table]["count"]
                            and baseline[table]["fingerprint"]
                            in (value["jsonb"], value["row_json"]),
                        )
                save(
                    PUBLIC / "restart-preservation.json",
                    {
                        "checks": evidence["checks"],
                        "old_data": stored,
                        "protected_files": len(protected),
                        "intentional_archive_stubs": sorted(archive_stubs),
                    },
                )
                print("Restart, historical files and PostgreSQL preservation verified")
                return
            if phase == "cleanup":
                async with sessions() as db:
                    rows = (
                        await db.scalars(
                            select(PresentationDocument).where(
                                PresentationDocument.project_id.in_(state["projects"]),
                                PresentationDocument.user_id == state["users"][0]["id"],
                            )
                        )
                    ).all()
                    owned = [r.id for r in rows]
                for project in state["projects"]:
                    r = await c.delete("/api/projects/" + project)
                    assert r.status_code in (204, 404)
                async with sessions.begin() as db:
                    for recorded in state["users"]:
                        user = await db.get(User, recorded["id"])
                        assert user and user.display_name.startswith(
                            "commercial-offer-" + state["run"]
                        )
                        await db.execute(delete(User).where(User.id == user.id))
                storage = Path(settings.presentation_storage).resolve()
                for doc in owned:
                    folder = (storage / "documents" / doc).resolve()
                    assert folder.parent == storage / "documents" and folder.name == doc
                    if folder.exists():
                        shutil.rmtree(folder)
                after = await fingerprint(sessions)
                check("commercial catalog preserved", after == state["catalog_before"])
                save(
                    PUBLIC / "cleanup.json",
                    {
                        "catalog_before": state["catalog_before"],
                        "catalog_after": after,
                        "deleted_user_ids": [u["id"] for u in state["users"]],
                        "deleted_document_ids": owned,
                        "deleted_project_ids": state["projects"],
                    },
                )
                LEDGER.unlink()
                print("Exact disposable resources removed; catalog identity unchanged")
                return
            collection = await call(
                c, "GET", f"/api/projects/{state['project']['id']}/collection"
            )
            document = await call(
                c,
                "POST",
                f"/api/projects/{state['project']['id']}/offers",
                json={"collection_version": collection["version"]},
            )
            state["documents"].append(document["id"])
            state["primary"] = document["id"]
            save(LEDGER, state)
            path = "/api/offers/" + document["id"]
            check(
                "quantity from 3 searches",
                [i["quantity"] for i in document["draft"]["items"]] == [250, 100, 1],
            )
            check(
                "SKU and exact source prices",
                all(
                    i["sku"] and i["unit_price"] == i["catalog_price"]
                    for i in document["draft"]["items"]
                ),
            )
            initial = copy.deepcopy(document["draft"]["items"])
            body = edit_body(document)
            body["items"][1].update(unit_price="35224.51", quantity=100)
            document = await call(c, "PATCH", path, json=body)
            check(
                "Decimal example",
                document["draft"]["items"][1]["line_amount"] == "3522451.00",
            )
            check(
                "unpublished private",
                not document["share"]["active"]
                and document["share"]["local_preview_path"] is None,
            )
            check("guest cannot edit", (await guest.get(path)).status_code == 401)
            async with httpx.AsyncClient(
                base_url=c.base_url, cookies=state["users"][1]["cookies"]
            ) as other:
                check(
                    "other user denied editor",
                    (await other.get(path)).status_code == 404,
                )
                check(
                    "other user denied save",
                    (await other.patch(path, json=body)).status_code == 404,
                )
            started = time.perf_counter()
            await call(
                c,
                "POST",
                path + "/exports",
                json={"version": document["version"], "request_id": str(uuid4())},
            )
            document, revision = await ready(c, path, document["version"])
            state["primary_revision"] = revision["id"]
            save(LEDGER, state)
            evidence["private_export_seconds"] = round(time.perf_counter() - started, 3)
            check("private exports create no link", not document["share"]["active"])
            document = await call(
                c,
                "POST",
                path + "/publish",
                json={
                    "version": document["version"],
                    "request_id": str(uuid4()),
                    "share_epoch": document["share"]["epoch"],
                },
            )
            check(
                "ready export published without duplicate render",
                document["share"]["active"] and len(document["revisions"]) == 1,
            )
            state["public_path"] = document["share"]["local_preview_path"]
            save(LEDGER, state)
            public = "/api/public/offers/" + state["public_path"].rsplit("/", 1)[1]
            client = await call(guest, "GET", public)
            check(
                "published quantity and calculation",
                [i["quantity"] for i in client["items"]] == [250, 100, 1]
                and client["items"][1]["line_amount"] == "3522451.00",
            )
            check(
                "readonly method",
                (await guest.patch(public, json={})).status_code == 405,
            )
            check(
                "no public storage namespace",
                (
                    await guest.get(
                        "/api/presentation-storage/documents/" + document["id"]
                    )
                ).status_code
                == 404,
            )
            forbidden = {
                "catalog_price",
                "offer_id",
                "source_url",
                "stock",
                "user_id",
                "asset_keys",
                "quantity_context",
                "price_source",
            }

            def clean(value):
                if isinstance(value, dict):
                    assert not forbidden.intersection(value)
                    for v in value.values():
                        clean(v)
                elif isinstance(value, list):
                    for v in value:
                        clean(v)

            clean(client)
            check("allowlisted client DTO", True)
            FIXTURES.mkdir(parents=True, exist_ok=True)
            pdf = await guest.get(client["pdf_url"])
            xlsx = await guest.get(client["xlsx_url"])
            assert pdf.status_code == xlsx.status_code == 200
            (FIXTURES / "offer.pdf").write_bytes(pdf.content)
            (FIXTURES / "offer.xlsx").write_bytes(xlsx.content)
            with pdfium.PdfDocument(pdf.content) as pages:
                texts = []
                for number, page in enumerate(pages):
                    with closing(page.get_textpage()) as extracted:
                        texts.append(extracted.get_text_range())
                    page.render(scale=1.2).to_pil().save(
                        FIXTURES / f"page-{number + 1}.png"
                    )
                evidence["pdf_pages"] = len(pages)
            book = openpyxl.load_workbook(io.BytesIO(xlsx.content), data_only=True)
            rows = [row for row in book.active.iter_rows(min_row=2) if row[2].value]
            check(
                "PDF real SKUs",
                all(i["sku"] in "\n".join(texts) for i in client["items"]),
            )
            check(
                "XLSX SKU text/order",
                [row[2].value for row in rows] == [i["sku"] for i in client["items"]]
                and all(row[2].data_type == "s" for row in rows),
            )
            check(
                "XLSX cached numeric calculations",
                [str(row[8].value) for row in rows]
                == [str(float(i["line_amount"])) for i in client["items"]],
            )
            check(
                "XLSX locked/no external",
                book.active.protection.sheet
                and not book._external_links
                and len(book.sheetnames) == 1,
            )
            check(
                "XLSX actual thumbnails",
                len(book.active._images) == len(client["items"]),
            )
            formulas = openpyxl.load_workbook(io.BytesIO(xlsx.content), data_only=False)
            check(
                "no total rows",
                not any(
                    "SUM(" in str(cell.value) for row in formulas.active for cell in row
                ),
            )
            frozen = copy.deepcopy(client)
            body = edit_body(document)
            body["items"][1]["quantity"] = 101
            document = await call(c, "PATCH", path, json=body)
            check(
                "draft save does not change publication",
                (await call(guest, "GET", public)) == frozen,
            )
            await call(
                c,
                "POST",
                path + "/publish",
                json={
                    "version": document["version"],
                    "request_id": str(uuid4()),
                    "share_epoch": document["share"]["epoch"],
                },
            )
            check(
                "pending update retains prior version",
                (await call(guest, "GET", public)) == frozen,
            )
            document, _ = await ready(c, path, document["version"])
            check(
                "same URL publishes updated snapshot",
                (await call(guest, "GET", public))["items"][1]["quantity"] == 101,
            )
            check(
                "stale download pin rejected",
                (await guest.get(frozen["pdf_url"])).status_code == 409,
            )
            document = await call(
                c,
                "POST",
                path + "/revoke",
                json={
                    "version": document["version"],
                    "epoch": document["share"]["epoch"],
                },
            )
            for url in [
                public,
                frozen["pdf_url"],
                frozen["xlsx_url"],
                frozen["logo_url"],
                frozen["items"][0]["images"][0]["url"],
            ]:
                denied = await guest.get(url)
                check(
                    "revoked scope " + str(len(evidence["checks"])),
                    denied.status_code == 404
                    and "no-store" in denied.headers["cache-control"],
                )
            document = await call(
                c,
                "POST",
                path + "/rotate",
                json={
                    "version": document["version"],
                    "epoch": document["share"]["epoch"],
                },
            )
            check(
                "rotation kills previous key",
                document["share"]["active"]
                and document["share"]["local_preview_path"] != state["public_path"]
                and (await guest.get(public)).status_code == 404,
            )
            state["public_path"] = document["share"]["local_preview_path"]
            save(LEDGER, state)
            check(
                "catalog identity unchanged",
                state["catalog_before"] == await fingerprint(sessions),
            )
            check(
                "collection unchanged",
                (
                    await call(
                        c, "GET", f"/api/projects/{state['project']['id']}/collection"
                    )
                )
                == collection,
            )
            legacy = await call(
                c,
                "POST",
                f"/api/projects/{state['project']['id']}/presentations",
                json={"collection_version": collection["version"]},
            )
            state["documents"].append(legacy["id"])
            state["legacy"] = legacy["id"]
            save(LEDGER, state)
            old_path = "/api/presentations/" + legacy["id"]
            legacy = await call(
                c,
                "PATCH",
                old_path,
                json={
                    "version": legacy["version"],
                    "settings": legacy["draft"]["settings"],
                    "items": [
                        {
                            k: i[k]
                            for k in (
                                "offer_id",
                                "display_name",
                                "caption",
                                "image_url",
                            )
                        }
                        for i in legacy["draft"]["items"]
                    ],
                    "category_order": legacy["draft"]["category_order"],
                    "original_fallback_ids": collection["selected_ids"],
                },
            )
            await call(
                c,
                "POST",
                old_path + "/generate",
                json={"version": legacy["version"], "request_id": str(uuid4())},
            )
            for _ in range(220):
                legacy = await call(c, "GET", old_path)
                old_revision = legacy["revisions"][0]
                if old_revision["status"] in ("READY", "FAILED", "NEEDS_REVIEW"):
                    break
                await asyncio.sleep(1.4)
            check(
                "legacy archived renderer still generates",
                old_revision["status"] == "READY",
            )
            old_pdf = await c.get(old_revision["result"]["pdf_url"])
            check(
                "legacy owner PDF download",
                old_pdf.status_code == 200
                and hashlib.sha256(old_pdf.content).hexdigest()
                == old_revision["result"]["sha256"],
            )
            check(
                "legacy guest denied",
                (await guest.get(old_revision["result"]["pdf_url"])).status_code == 401,
            )
            async with sessions() as db:
                row = await db.get(PresentationRevision, revision["id"])
                evidence["worker"] = {
                    k: row.result.get(k)
                    for k in ("timings", "peak_rss_bytes", "snapshot_hash")
                }
            evidence["selections"] = state["selections"]
            evidence["initial_items"] = [
                {
                    k: i.get(k)
                    for k in (
                        "offer_id",
                        "sku",
                        "quantity",
                        "catalog_price",
                        "availability",
                    )
                }
                for i in initial
            ]
            evidence["files"] = {
                "pdf_sha256": hashlib.sha256(pdf.content).hexdigest(),
                "xlsx_sha256": hashlib.sha256(xlsx.content).hexdigest(),
            }
            save(PUBLIC / "http.json", evidence)
            save(
                FIXTURES / "manifest.json",
                {
                    "revision_id": revision["id"],
                    "snapshot_hash": client["snapshot_hash"],
                    "items": [
                        {
                            k: i[k]
                            for k in (
                                "title",
                                "sku",
                                "unit_price",
                                "quantity",
                                "line_amount",
                            )
                        }
                        for i in client["items"]
                    ],
                    **evidence["files"],
                },
            )
            print(
                f"Real HTTP acceptance: {len(evidence['checks'])} checks; PDF {evidence['pdf_pages']} pages; tokens withheld"
            )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
