"""Read-only preservation proof and exact-run cleanup, never age-based cleanup."""

import asyncio
import hashlib
import shutil
import subprocess
from pathlib import Path

import httpx
import psutil
from sqlalchemy import bindparam, text

from app.config import Settings
from app.database.connection import create_database


def bound(sql):
    return text(sql).bindparams(bindparam("ids", expanding=True))


async def protected(db, state, projects, chats, docs):
    owners = [u["id"] for u in state["users"]]
    result = {}
    for table, field, ids, order in [
        ("users", "id", owners, "id"),
        ("projects", "user_id", owners, "id"),
        ("messages", "chat_id", chats, "id"),
        ("project_collection_items", "project_id", projects, "project_id,offer_id"),
        ("presentation_documents", "user_id", owners, "id"),
        ("presentation_revisions", "document_id", docs, "id"),
        ("commercial_offer_shares", "document_id", docs, "document_id"),
    ]:
        columns = ",".join(state["columns"][table])
        row = dict(
            (
                await db.execute(
                    bound(
                        f"SELECT count(*) count,md5(string_agg(row_to_json(t)::text,'' ORDER BY {order})) fingerprint FROM (SELECT {columns} FROM {table} WHERE {field} IS NULL OR {field} NOT IN :ids) t"
                    ),
                    {"ids": ids},
                )
            )
            .mappings()
            .one()
        )
        result[table] = {
            "before": state["before"][table],
            "after": row,
            "unchanged": row == state["before"][table],
        }
    assert all(v["unchanged"] for v in result.values()), result
    identities = dict(
        (await db.execute(text("SELECT id,product_id FROM supplier_offers WHERE supplier='vto'"))).all()
    )
    assert all(identities.get(k) == v for k, v in state["catalog_identities"].items())
    return result, len(identities)


async def run(state, root, ledger, public, cleanup=False):
    from check_chat_workflow import save
    from check_nav_delete_recovery import catalog

    settings = Settings()
    assert ":55432/" in settings.database_url
    engine, sessions = create_database(settings.database_url)
    owners = [u["id"] for u in state["users"]]
    try:
        async with sessions() as db:
            await db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
            projects = list(
                await db.scalars(bound("SELECT id FROM projects WHERE user_id IN :ids"), {"ids": owners})
            )
            chats = list(
                await db.scalars(bound("SELECT id FROM chats WHERE user_id IN :ids"), {"ids": owners})
            )
            docs = list(
                await db.scalars(
                    bound("SELECT id FROM presentation_documents WHERE user_id IN :ids"), {"ids": owners}
                )
            )
            assert set(projects) == set(state["projects"]) and set(docs) == set(state["documents"])
            proof, offers = await protected(db, state, projects, chats, docs)
            current = await catalog(db)
            pending = await db.scalar(
                bound(
                    "SELECT count(*) FROM presentation_revisions WHERE document_id IN :ids AND status IN ('QUEUED','PREPARING_ASSETS','RENDERING','VALIDATING')"
                ),
                {"ids": docs},
            )
            assert pending == 0
        key_ok = (
            hashlib.sha256(
                (settings.presentation_storage / "keys/commercial-offers.key").read_bytes()
            ).hexdigest()
            == state["key_hash"]
        )
        assert key_ok
        for key, fingerprint in state["files"].items():
            path = (settings.presentation_storage / key).resolve()
            path.relative_to(settings.presentation_storage.resolve())
            assert path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == fingerprint
        evidence = {
            "status": "PASS",
            "protected": proof,
            "original_files_verified": len(state["files"]),
            "encryption_key_unchanged": key_ok,
            "original_catalog_identities_preserved": len(state["catalog_identities"]),
            "offers": offers,
            "catalog": current,
            "cleanup": False,
        }
        save(public / "preservation.json", evidence)
        if not cleanup:
            print("Original data, files and catalog identities preserved", offers)
            return
        async with sessions() as db, db.begin():
            for u in state["users"]:
                name = await db.scalar(text("SELECT display_name FROM users WHERE id=:id"), {"id": u["id"]})
                assert name == u["name"] and name.startswith("nav-recovery-" + state["run"] + "-")
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
                await db.execute(text("DELETE FROM users WHERE id=:id"), {"id": identifier})
        base = (settings.presentation_storage / "documents").resolve()
        for identifier in docs:
            target = (base / identifier).resolve()
            assert target.parent == base
            if target.exists():
                shutil.rmtree(target)
        if state.get("edge_pid") and psutil.pid_exists(state["edge_pid"]):
            process = psutil.Process(state["edge_pid"])
            profile = Path(state["edge_profile"]).resolve()
            assert (
                profile.parent == (root / ".local").resolve()
                and profile.name == "nav-recovery-edge-" + state["run"]
            )
            assert "--user-data-dir=" + str(profile) in process.cmdline()
            owned = [process, *process.children(recursive=True)]
            for p in reversed(owned):
                try:
                    p.terminate()
                except psutil.NoSuchProcess:
                    pass
            _, alive = await asyncio.to_thread(psutil.wait_procs, owned, timeout=5)
            for p in alive:
                p.kill()
            await asyncio.to_thread(psutil.wait_procs, alive, timeout=5)
            if profile.exists():
                shutil.rmtree(profile)
        child = state.get("isolated")
        if child:
            from check_search_safety import PG_BIN

            directory = Path(child["directory"]).resolve()
            data = Path(child["data"]).resolve()
            assert (
                directory.parent == (root / ".local").resolve()
                and directory.name == "nav-recovery-pg-" + state["run"]
                and data.parent == directory
            )
            process = psutil.Process(child["pid"])
            assert str(data) in " ".join(process.cmdline())
            stopped = await asyncio.to_thread(
                subprocess.run,
                [str(PG_BIN / "pg_ctl.exe"), "-D", str(data), "-m", "fast", "-w", "stop"],
                capture_output=True,
                timeout=30,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            assert stopped.returncode == 0
            shutil.rmtree(directory)
        async with sessions() as db:
            await db.execute(text("SET TRANSACTION READ ONLY"))
            final, _ = await protected(db, state, projects, chats, docs)
            assert not await db.scalar(bound("SELECT count(*) FROM users WHERE id IN :ids"), {"ids": owners})
        async with httpx.AsyncClient() as client:
            health = {
                label: (await client.get(url)).status_code
                for label, url in [
                    ("api", "http://127.0.0.1:8000/api/health"),
                    ("frontend", "http://127.0.0.1:3000/"),
                    ("proxy", "http://127.0.0.1:3000/api/health"),
                ]
            }
        assert all(v == 200 for v in health.values())
        evidence.update(
            cleanup=True,
            removed={"users": len(owners), "projects": len(projects), "documents": len(docs)},
            protected=final,
            health=health,
        )
        save(public / "cleanup.json", evidence)
        ledger.unlink()
        print("Exact-run cleanup complete", evidence["removed"], health)
    finally:
        await engine.dispose()
