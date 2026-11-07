"""Frontend-only acceptance. Exact disposable IDs; never changes importer state."""

import asyncio
import hashlib
import json
import secrets
import shutil
import sys
from pathlib import Path

import httpx
import psutil
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
from quality.nav_recovery_cleanup import bound, protected

LEDGER = ROOT / ".local/clean-start-ledger.json"
PUBLIC = ROOT / "docs/screenshots/clean-start"


async def prepare():
    assert not LEDGER.exists()
    settings = Settings()
    assert ":55432/" in settings.database_url
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
            state["catalog_identities"] = dict(
                (
                    await db.execute(text("SELECT id,product_id FROM supplier_offers"))
                ).all()
            )
            docs = list(await db.scalars(text("SELECT id FROM presentation_documents")))
        state["files"] = {}
        for identifier in docs:
            for file in (
                settings.presentation_storage / "documents" / identifier
            ).rglob("*"):
                if file.is_file():
                    state["files"][
                        str(file.relative_to(settings.presentation_storage))
                    ] = hashlib.sha256(file.read_bytes()).hexdigest()
        state["key_hash"] = hashlib.sha256(
            (settings.presentation_storage / "keys/commercial-offers.key").read_bytes()
        ).hexdigest()
        state["backend"] = {
            str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for folder in ["apps/api/app", "apps/api/alembic"]
            for p in (ROOT / folder).rglob("*.py")
        }
        save(LEDGER, state)
        async with httpx.AsyncClient(
            base_url="http://127.0.0.1:8000", timeout=60
        ) as client:
            name = "clean-start-" + state["run"]
            user = await call(
                client,
                "POST",
                "/api/auth/register",
                json={"name": name, "password": secrets.token_urlsafe(24)},
            )
            state["users"].append(
                {"id": user["id"], "name": name, "cookies": dict(client.cookies)}
            )
            save(LEDGER, state)
        print(
            "Read-only baseline saved; isolated account created. Original offers:",
            len(state["catalog_identities"]),
        )
    finally:
        await engine.dispose()


async def verify(state, cleanup=False):
    settings = Settings()
    engine, sessions = create_database(settings.database_url)
    owners = [u["id"] for u in state["users"]]
    try:
        async with sessions() as db:
            await db.execute(
                text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            )
            projects = list(
                await db.scalars(
                    bound("SELECT id FROM projects WHERE user_id IN :ids"),
                    {"ids": owners},
                )
            )
            chats = list(
                await db.scalars(
                    bound("SELECT id FROM chats WHERE user_id IN :ids"), {"ids": owners}
                )
            )
            docs = list(
                await db.scalars(
                    bound(
                        "SELECT id FROM presentation_documents WHERE user_id IN :ids"
                    ),
                    {"ids": owners},
                )
            )
            assert set(projects) == set(state["projects"]) and not docs
            proof, offers = await protected(
                db,
                state,
                projects or ["clean-start-no-id"],
                chats or ["clean-start-no-id"],
                docs or ["clean-start-no-id"],
            )
        assert all(
            (ROOT / k).is_file()
            and hashlib.sha256((ROOT / k).read_bytes()).hexdigest() == v
            for k, v in state["backend"].items()
        )
        assert (
            hashlib.sha256(
                (
                    settings.presentation_storage / "keys/commercial-offers.key"
                ).read_bytes()
            ).hexdigest()
            == state["key_hash"]
        )
        assert all(
            hashlib.sha256((settings.presentation_storage / k).read_bytes()).hexdigest()
            == v
            for k, v in state["files"].items()
        )
        evidence = {
            "status": "PASS",
            "original_records": proof,
            "original_offer_identities": len(state["catalog_identities"]),
            "offers_now": offers,
            "backend_source_files_unchanged": len(state["backend"]),
            "original_document_files_unchanged": len(state["files"]),
            "encryption_key_unchanged": True,
            "cleanup": False,
        }
        if cleanup:
            async with sessions() as db, db.begin():
                for u in state["users"]:
                    assert await db.scalar(
                        text("SELECT display_name FROM users WHERE id=:id"),
                        {"id": u["id"]},
                    ) == u["name"] and u["name"].startswith(
                        "clean-start-" + state["run"]
                    )
                for identifier in projects:
                    await db.execute(
                        bound("DELETE FROM projects WHERE id=:id AND user_id IN :ids"),
                        {"id": identifier, "ids": owners},
                    )
                for identifier in chats:
                    await db.execute(
                        bound("DELETE FROM chats WHERE id=:id AND user_id IN :ids"),
                        {"id": identifier, "ids": owners},
                    )
                for identifier in owners:
                    await db.execute(
                        text("DELETE FROM users WHERE id=:id"), {"id": identifier}
                    )
            if state.get("edge_pid") and psutil.pid_exists(state["edge_pid"]):
                process = psutil.Process(state["edge_pid"])
                profile = Path(state["edge_profile"]).resolve()
                assert (
                    profile.parent == (ROOT / ".local").resolve()
                    and profile.name == "clean-start-edge-" + state["run"]
                )
                assert "--user-data-dir=" + str(profile) in process.cmdline()
                tree = [process, *process.children(recursive=True)]
                for p in reversed(tree):
                    try:
                        p.terminate()
                    except psutil.NoSuchProcess:
                        pass
                _, alive = await asyncio.to_thread(psutil.wait_procs, tree, timeout=5)
                for p in alive:
                    p.kill()
                await asyncio.to_thread(psutil.wait_procs, alive, timeout=5)
                if profile.exists():
                    shutil.rmtree(profile)
            async with sessions() as db:
                await db.execute(text("SET TRANSACTION READ ONLY"))
                evidence["original_records"], _ = await protected(
                    db,
                    state,
                    projects or ["clean-start-no-id"],
                    chats or ["clean-start-no-id"],
                    docs or ["clean-start-no-id"],
                )
                assert not await db.scalar(
                    bound("SELECT count(*) FROM users WHERE id IN :ids"),
                    {"ids": owners},
                )
            evidence.update(
                cleanup=True,
                removed={
                    "users": len(owners),
                    "projects": len(projects),
                    "documents": 0,
                },
            )
            LEDGER.unlink()
        async with httpx.AsyncClient() as client:
            evidence["health"] = {
                label: (await client.get(url)).status_code
                for label, url in [
                    ("api", "http://127.0.0.1:8000/api/health"),
                    ("frontend", "http://127.0.0.1:3000/"),
                    ("proxy", "http://127.0.0.1:3000/api/health"),
                ]
            }
        assert all(v == 200 for v in evidence["health"].values())
        save(PUBLIC / ("cleanup.json" if cleanup else "preservation.json"), evidence)
        print("Preservation verified; cleanup:", cleanup)
    finally:
        await engine.dispose()


async def main():
    phase = sys.argv[1]
    if phase == "prepare":
        return await prepare()
    state = json.loads(LEDGER.read_text("utf-8"))
    if phase == "web":
        from quality.chat_workflow_acceptance import run

        return await run("web", ROOT, LEDGER, PUBLIC)
    if phase == "browser":
        from check_clean_start_browser import run

        return await run(state, ROOT, LEDGER, PUBLIC)
    if phase == "controls":
        from check_clean_start_controls import run

        return await run(state, ROOT, LEDGER, PUBLIC)
    if phase == "fresh":
        from check_clean_start_fresh import run

        return await run(state, ROOT, LEDGER, PUBLIC)
    if phase in ("verify", "cleanup"):
        return await verify(state, phase == "cleanup")
    raise ValueError(phase)


if __name__ == "__main__":
    asyncio.run(main())
