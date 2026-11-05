"""Exact-ledger cleanup. Never resets or stops the main PostgreSQL or Ollama."""

import asyncio
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from uuid import UUID

import httpx
import psutil
from sqlalchemy import text

from app.config import Settings
from app.database.connection import create_database


def digest(values):
    return hashlib.sha256("\n".join(key + ":" + values[key] for key in sorted(values)).encode()).hexdigest()


async def run(state, root, pg_bin, ledger, public, stop_api):
    directory = Path(state["directory"]).resolve()
    local = (root / ".local").resolve()
    assert directory.parent == local and directory.name == "search-safety-" + state["run"]
    assert Path(state["pg_data"]).resolve().parent == directory and state["pg_port"] == 55433
    for process in psutil.process_iter(["cmdline"]):
        command = " ".join(process.info["cmdline"] or [])
        assert not ("-m pytest" in command and str(root).casefold() in command.casefold()), (
            "Wait for tests before cleanup"
        )
    settings = Settings()
    assert ":55432/" in settings.database_url
    engine, sessions = create_database(settings.database_url)
    users, docs = state["live_users"], []
    try:
        async with sessions.begin() as db:
            for user in users:
                value = (
                    await db.execute(text("SELECT display_name FROM users WHERE id=:id"), {"id": user["id"]})
                ).scalar_one()
                assert value == user["name"] and value.startswith("quality-live-")
                ids = (
                    await db.scalars(text("SELECT id FROM projects WHERE user_id=:id"), {"id": user["id"]})
                ).all()
                assert ids == [user["project"]["id"]]
                docs += (
                    await db.scalars(
                        text("SELECT id FROM presentation_documents WHERE project_id=:id"), {"id": ids[0]}
                    )
                ).all()
            owned_users = {u["id"] for u in users}
            preserved = {
                r.id: r.fingerprint
                for r in (
                    await db.execute(
                        text("SELECT id,user_id,md5(draft::text) fingerprint FROM presentation_documents")
                    )
                ).all()
                if r.user_id not in owned_users
            }
            # Projects first: deleting only a user would leave SET NULL projects.
            for user in users:
                await db.execute(
                    text("DELETE FROM projects WHERE id=:project AND user_id=:owner"),
                    {"project": user["project"]["id"], "owner": user["id"]},
                )
                await db.execute(text("DELETE FROM users WHERE id=:id"), {"id": user["id"]})
        async with sessions() as db:
            after_docs = {
                r.id: r.fingerprint
                for r in (
                    await db.execute(
                        text("SELECT id,md5(draft::text) fingerprint FROM presentation_documents")
                    )
                ).all()
            }
            assert after_docs == preserved
            for user in users:
                assert not await db.scalar(
                    text("SELECT count(*) FROM users WHERE id=:id"), {"id": user["id"]}
                )
                assert not await db.scalar(
                    text("SELECT count(*) FROM projects WHERE id=:id"), {"id": user["project"]["id"]}
                )
            commercial = {
                r.id: r.fingerprint
                for r in (
                    await db.execute(
                        text(
                            "SELECT o.id,md5(coalesce(o.payload::text,'')||coalesce(p.payload::text,'')) fingerprint FROM supplier_offers o LEFT JOIN product_observations p ON p.offer_id=o.id"
                        )
                    )
                ).all()
            }
            jobs = [
                dict(r)
                for r in (
                    await db.execute(
                        text("SELECT id,status,error,updated_at FROM catalog_sync_jobs ORDER BY id")
                    )
                ).mappings()
            ]
            head = await db.scalar(text("SELECT version_num FROM alembic_version"))
        before = {r["id"]: r["fingerprint"] for r in state["audit"]["commercial"]}
        changed = [key for key in before if commercial.get(key) != before[key]]
        added, removed = sorted(set(commercial) - set(before)), sorted(set(before) - set(commercial))
        result = {
            "status": "PASS" if not changed and not removed else "PARTIAL",
            "main_database_preserved": True,
            "offers_before": len(before),
            "offers_after": len(commercial),
            "commercial_changed_ids": changed,
            "added_offer_ids": added,
            "removed_offer_ids": removed,
            "commercial_sha256_before": digest(before),
            "commercial_sha256_after": digest(commercial),
            "deleted_test_users": len(users),
            "deleted_test_projects": len(users),
            "deleted_test_documents": len(docs),
            "other_document_drafts_unchanged": len(preserved),
            "alembic": head,
            "catalog_jobs": jobs,
        }
    finally:
        await engine.dispose()

    documents = (settings.presentation_storage.resolve() / "documents").resolve()
    for identifier in docs:
        UUID(identifier)
        target = (documents / identifier).resolve()
        assert target.parent == documents
        if target.exists():
            shutil.rmtree(target)
    stop_api(state)
    edge = psutil.Process(state["edge_pid"])
    assert "--user-data-dir=" + state["edge_profile"] in edge.cmdline()
    children = edge.children(recursive=True)
    for child in reversed(children):
        try:
            child.terminate()
        except psutil.NoSuchProcess:
            pass
    edge.terminate()
    _, alive = await asyncio.to_thread(psutil.wait_procs, [edge, *children], timeout=5)
    for process in alive:
        process.kill()
    pg = psutil.Process(state["pg_pid"])
    assert str(Path(state["pg_data"])) in " ".join(pg.cmdline())
    stopped = await asyncio.to_thread(
        subprocess.run,
        [str(pg_bin / "pg_ctl.exe"), "-D", state["pg_data"], "-m", "fast", "-w", "stop"],
        capture_output=True,
        timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert stopped.returncode == 0
    assert not any(
        c.status == "LISTEN" and c.laddr and c.laddr.port in (55433, 18000, 18001, 9226)
        for c in psutil.net_connections(kind="tcp")
    )
    targets = [directory, *[Path(p) for p in state.get("temporary_artifacts", [])]]
    removed_targets = 0
    for target in targets:
        target = target.resolve()
        assert target.parent == local and target != local
        if target.exists():
            if target.is_dir():
                shutil.rmtree("\\\\?\\" + str(target) if sys.platform == "win32" else target)
            else:
                target.unlink()
            removed_targets += 1
    result.update(
        disposable_cluster_removed=True,
        isolated_processes_stopped=True,
        temporary_targets_removed=removed_targets,
        private_ledger_removed=True,
    )
    async with httpx.AsyncClient(timeout=10) as c:
        result["health"] = {
            url: (await c.get(url)).status_code
            for url in [
                "http://127.0.0.1:8000/api/health",
                "http://127.0.0.1:3000/",
                "http://127.0.0.1:3000/api/health",
            ]
        }
    assert set(result["health"].values()) == {200}
    ports = {c.laddr.port for c in psutil.net_connections(kind="tcp") if c.status == "LISTEN" and c.laddr}
    assert {55432, 11434, 8000, 3000} <= ports
    (public / "cleanup.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str), "utf-8"
    )
    ledger.unlink()
    return result
