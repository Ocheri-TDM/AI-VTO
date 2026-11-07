"""Bounded acceptance with exact disposable IDs; credentials stay in .local."""

import asyncio
import hashlib
import json
import secrets
import sys
from pathlib import Path

import httpx
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [
    str(ROOT / "apps/api"),
    str(ROOT / "apps/api/tests"),
    str(ROOT / "scripts"),
]
from app.config import Settings
from app.database.connection import create_database
from check_chat_workflow import call, save

LEDGER = ROOT / ".local/nav-delete-recovery-ledger.json"
PUBLIC = ROOT / "docs/screenshots/nav-delete-recovery"


async def catalog(db):
    now = await db.scalar(text("SELECT now()"))
    result = dict(
        (
            await db.execute(
                text(
                    "SELECT count(*) offers,count(DISTINCT product_id) families,count(*) FILTER (WHERE payload->'metadata'->>'vto_variation_id' IS NOT NULL) real_variants FROM supplier_offers WHERE supplier='vto'"
                )
            )
        )
        .mappings()
        .one()
    )
    result["at"] = str(now)
    result["jobs"] = []
    for row in (
        await db.execute(
            text(
                "SELECT kind,status,error,available_at,updated_at,checkpoint,metrics FROM catalog_sync_jobs WHERE supplier='vto'"
            )
        )
    ).mappings():
        cp, metrics = row["checkpoint"], row["metrics"]
        result["jobs"].append(
            {
                k: row[k]
                for k in ("kind", "status", "error", "available_at", "updated_at")
            }
            | {
                "seen_parents": len(cp.get("seen_parent_ids", [])),
                "page": cp.get("page"),
                "per_page": cp.get("per_page"),
                "failed_listing_pages": cp.get("failed_listing_pages", {}),
                "listing_probe_attempted": cp.get("listing_probe_attempted", False),
                "pending": len(cp.get("pending_products", [])),
                "failed": len(cp.get("failed_products", {})),
                "reported_total": cp.get("reported_total"),
                "stages": cp.get("stages"),
                "metrics": metrics,
            }
        )
    result["state"] = dict(
        (
            await db.execute(
                text(
                    "SELECT retry_after,lease_until,circuit_open_until,error,failures,lease_owner IS NOT NULL AND lease_until>now() active_lease FROM supplier_sync_states WHERE supplier='vto'"
                )
            )
        )
        .mappings()
        .one()
    )
    return result


async def prepare():
    assert not LEDGER.exists()
    settings = Settings()
    engine, sessions = create_database(settings.database_url)
    state = {
        "run": secrets.token_hex(8),
        "users": [],
        "projects": [],
        "documents": [],
        "before": {},
        "columns": {},
    }
    try:
        async with sessions() as db:
            await db.execute(
                text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            )
            for table, order in [
                ("users", "id"),
                ("projects", "id"),
                ("messages", "id"),
                ("project_collection_items", "project_id,offer_id"),
                ("presentation_documents", "id"),
                ("presentation_revisions", "id"),
                ("commercial_offer_shares", "document_id"),
            ]:
                state["columns"][table] = list(
                    await db.scalars(
                        text(
                            "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=:table ORDER BY ordinal_position"
                        ),
                        {"table": table},
                    )
                )
                state["before"][table] = dict(
                    (
                        await db.execute(
                            text(
                                f"SELECT count(*) count,md5(string_agg(row_to_json(t)::text,'' ORDER BY {order})) fingerprint FROM {table} t"
                            )
                        )
                    )
                    .mappings()
                    .one()
                )
            state["catalog_before"] = await catalog(db)
            state["catalog_identities"] = dict(
                (
                    await db.execute(
                        text(
                            "SELECT id,product_id FROM supplier_offers WHERE supplier='vto'"
                        )
                    )
                ).all()
            )
            original_docs = list(
                await db.scalars(text("SELECT id FROM presentation_documents"))
            )
        state["key_hash"] = hashlib.sha256(
            (settings.presentation_storage / "keys/commercial-offers.key").read_bytes()
        ).hexdigest()
        state["files"] = {}
        for identifier in original_docs:
            folder = settings.presentation_storage / "documents" / identifier
            if folder.exists():
                for file in folder.rglob("*"):
                    if file.is_file():
                        state["files"][
                            str(file.relative_to(settings.presentation_storage))
                        ] = hashlib.sha256(file.read_bytes()).hexdigest()
        save(LEDGER, state)
        save(PUBLIC / "before-import.json", state["catalog_before"])
        async with httpx.AsyncClient(
            base_url="http://127.0.0.1:8000", timeout=60
        ) as client:
            for n in range(2):
                client.cookies.clear()
                name = f"nav-recovery-{state['run']}-{n}"
                password = secrets.token_urlsafe(24)
                user = await call(
                    client,
                    "POST",
                    "/api/auth/register",
                    json={"name": name, "password": password},
                )
                state["users"].append(
                    {
                        "id": user["id"],
                        "name": name,
                        "password": password,
                        "cookies": dict(client.cookies),
                    }
                )
                save(LEDGER, state)
        print(
            "Baseline saved; original data",
            {k: v["count"] for k, v in state["before"].items()},
            "catalog",
            state["catalog_before"]["offers"],
        )
    finally:
        await engine.dispose()


async def main():
    phase = sys.argv[1]
    if phase == "prepare":
        return await prepare()
    from quality.nav_delete_recovery import run

    return await run(phase, json.loads(LEDGER.read_text("utf-8")), ROOT, LEDGER, PUBLIC)


if __name__ == "__main__":
    asyncio.run(main())
