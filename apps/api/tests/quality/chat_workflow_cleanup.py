"""Restart verification and removal of resources proved to belong to this run."""

import asyncio
import hashlib
import io
import json
import shutil
import statistics
import time
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import openpyxl
import psutil
import pypdfium2
from sqlalchemy import bindparam, text

from app.config import Settings
from app.database.connection import create_database


def bound(sql):
    return text(sql).bindparams(bindparam("ids", expanding=True))


async def preserved(db, state, projects, chats, docs):
    owners = [u["id"] for u in state["users"]]
    result = {}
    for table, order, field, excluded, projection in [
        (
            "users",
            "id",
            "id",
            owners,
            "id,display_name,normalized_name,password_hash,created_at,updated_at,last_login_at",
        ),
        (
            "projects",
            "id",
            "user_id",
            owners,
            "id,user_id,name,client_name,active_query_id,request_epoch,created_at",
        ),
        ("messages", "id", "chat_id", chats, None),
        ("project_collection_items", "project_id,offer_id", "project_id", projects, None),
        ("presentation_documents", "id", "user_id", owners, None),
        ("presentation_revisions", "id", "document_id", docs, None),
    ]:
        projection = projection or ",".join(state["columns"][table])
        sql = (
            f"SELECT count(*) count,md5(string_agg(row_to_json(t)::text,'' ORDER BY {order})) fingerprint "
            f"FROM (SELECT {projection} FROM {table} WHERE {field} IS NULL OR {field} NOT IN :ids) t"
        )
        current = dict((await db.execute(bound(sql), {"ids": excluded})).mappings().one())
        result[table] = {
            "before": state["before"][table],
            "after": current,
            "unchanged": current == state["before"][table],
        }
    assert all(value["unchanged"] for value in result.values()), result
    share_checks = []
    for old in state["before"]["shares"]:
        row = dict(
            (
                await db.execute(
                    text(
                        "SELECT document_id,md5(encrypted_token) token_fingerprint,active,epoch,activated_at,expires_at,activation_source "
                        "FROM commercial_offer_shares WHERE document_id=:id"
                    ),
                    {"id": old["document_id"]},
                )
            )
            .mappings()
            .one()
        )
        expected = dict(old)
        if old["active"] and old["activation_source"] in {"READY_ACTIVATION", "CONFIRMED_FIRST_READY"}:
            activated = datetime.fromisoformat(old["activated_at"])
            if datetime.fromisoformat(old["expires_at"]) == activated + timedelta(days=7):
                expected["expires_at"] = str(activated + timedelta(days=14))
        normalized = {k: str(v) if isinstance(v, datetime) else v for k, v in row.items()}
        assert normalized == expected, "Original share changed beyond authorized lifetime migration"
        share_checks.append(
            {
                "document_id": old["document_id"],
                "authorized_expiry_only": True,
                "activation_source": old["activation_source"],
                "expires_at": row["expires_at"],
            }
        )
    invalid = await db.scalar(
        text(
            "SELECT count(*) FROM commercial_offer_shares WHERE active AND "
            "(activated_at IS NULL OR expires_at IS NULL OR activated_at>now() OR expires_at<activated_at)"
        )
    )
    assert invalid == 0
    return result, share_checks


async def run(state, root, ledger, public, cleanup=False):
    from check_chat_workflow import call, save

    settings = Settings()
    assert ":55432/" in settings.database_url
    owners = [u["id"] for u in state["users"]]
    assert len(owners) == 2
    engine, sessions = create_database(settings.database_url)
    try:
        async with sessions() as db:
            await db.execute(text("SET TRANSACTION READ ONLY"))
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
            assert set(projects) == set(state["projects"])
            assert set(docs) == set(state["documents"])
            protected, shares = await preserved(db, state, projects, chats, docs)
            catalog = await db.scalar(text("SELECT count(*) FROM supplier_offers"))
            jobs = [
                dict(r)
                for r in (
                    await db.execute(
                        text(
                            "SELECT id,kind,status,error,checkpoint->>'page' page,checkpoint->>'coverage_complete' coverage_complete FROM catalog_sync_jobs"
                        )
                    )
                ).mappings()
            ]
            migration = await db.scalar(text("SELECT version_num FROM alembic_version"))
            pending = await db.scalar(
                bound(
                    "SELECT count(*) FROM presentation_revisions WHERE document_id IN :ids AND status IN "
                    "('QUEUED','PREPARING_ASSETS','RENDERING','VALIDATING')"
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
        checks, timings = [], {}
        async with httpx.AsyncClient(
            base_url="http://127.0.0.1:8000", cookies=state["users"][0]["cookies"], timeout=60
        ) as owner:
            p = state["conversation_project"]
            history = await call(owner, "GET", f"/api/chats/{p['chat_id']}/history")
            assert len(history["messages"]) == 10 and all(m["operation_id"] for m in history["messages"])
            checks.append("Ten persisted conversation messages survive API restart")
            selected = await call(owner, "GET", f"/api/projects/{p['id']}/collection")
            assert len(selected["selected_ids"]) == 3
            checks.append("Project collection survives restart")
            document = await call(owner, "GET", "/api/offers/" + state["document"])
            assert (
                document["draft"]["items"][0]["unit_price"] == "35224.51"
                and document["draft"]["items"][0]["quantity"] == 77
            )
            assert document["share"]["local_preview_path"] == state["published"]["path"]
            assert document["share"]["expires_at"] == state["published"]["expires_at"]
            assert document["share"]["published_revision_id"] == state["published"]["revision"]
            checks.append("Same primary offer, manual values, key, revision and expiry after restart")
            for label, path in [("history", f"/api/chats/{p['chat_id']}/history"), ("offers", "/api/offers")]:
                samples = []
                for _ in range(5):
                    started = time.perf_counter()
                    await call(owner, "GET", path)
                    samples.append(round((time.perf_counter() - started) * 1000, 2))
                timings[label] = {
                    "samples_ms": samples,
                    "median_ms": statistics.median(samples),
                    "scope": "five sequential local reads, not load p95",
                }
        async with httpx.AsyncClient(base_url="http://127.0.0.1:8000", timeout=60) as guest:
            key = state["published"]["path"].rsplit("/", 1)[-1]
            response = await guest.get("/api/public/offers/" + key)
            assert response.status_code == 200, "Published read after restart failed"
            client = response.json()
            forbidden = [
                "owner_stock",
                "snapshot_quantity",
                "stock_quantity",
                "availability_evidence",
                "incoming_quantity",
            ]
            assert not any(word in json.dumps(client) for word in forbidden)
            for kind in ("pdf", "xlsx"):
                exported = await guest.get(client[kind + "_url"])
                assert exported.status_code == 200
                assert hashlib.sha256(exported.content).hexdigest() == state["export_hashes"][kind]
                if kind == "pdf":
                    with pypdfium2.PdfDocument(exported.content) as pdf:
                        content = "\n".join(page.get_textpage().get_text_range() for page in pdf)
                else:
                    book = openpyxl.load_workbook(io.BytesIO(exported.content), data_only=True)
                    content = "\n".join(
                        str(c.value) for sheet in book for row in sheet for c in row if c.value is not None
                    )
                    book.close()
                assert not any(word in content for word in forbidden)
                assert "Остаток по данным VTO" not in content and "В снимке предложения" not in content
            checks.append("Guest JSON/PDF/XLSX remain client-only and byte-identical after restart")
        evidence = {
            "status": "PASS",
            "checks": checks,
            "protected": protected,
            "shares": shares,
            "encryption_key_unchanged": key_ok,
            "catalog_before": state["before"]["catalog"],
            "catalog_after": catalog,
            "jobs": jobs,
            "migration": migration,
            "timings": timings,
            "cleanup": False,
        }
        save(public / "restart-preservation.json", evidence)
        if not cleanup:
            print(
                "Restart/preservation verified",
                len(checks),
                "checks; original documents and messages unchanged",
            )
            return
        async with sessions() as db, db.begin():
            for u in state["users"]:
                actual = await db.scalar(text("SELECT display_name FROM users WHERE id=:id"), {"id": u["id"]})
                assert actual == u["name"] and actual.startswith("chat-workflow-" + state["run"] + "-")
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
        profile = Path(state["edge_profile"]).resolve()
        assert (
            profile.parent == (root / ".local").resolve()
            and profile.name == "chat-workflow-edge-" + state["run"]
        )
        if psutil.pid_exists(state["edge_pid"]):
            parent = psutil.Process(state["edge_pid"])
            assert "--user-data-dir=" + str(profile) in parent.cmdline()
            processes = [parent, *parent.children(recursive=True)]
            for process in reversed(processes):
                try:
                    process.terminate()
                except psutil.NoSuchProcess:
                    pass
            _, alive = await asyncio.to_thread(psutil.wait_procs, processes, timeout=5)
            for process in alive:
                process.kill()
            await asyncio.to_thread(psutil.wait_procs, alive, timeout=5)
        if profile.exists():
            shutil.rmtree(profile)
        async with sessions() as db:
            await db.execute(text("SET TRANSACTION READ ONLY"))
            final, _ = await preserved(db, state, projects, chats, docs)
            assert not await db.scalar(bound("SELECT count(*) FROM users WHERE id IN :ids"), {"ids": owners})
        async with httpx.AsyncClient() as health:
            health_codes = {
                label: (await health.get(url)).status_code
                for label, url in [
                    ("api", "http://127.0.0.1:8000/api/health"),
                    ("frontend", "http://127.0.0.1:3000/"),
                    ("frontend_proxy", "http://127.0.0.1:3000/api/health"),
                ]
            }
        assert all(code == 200 for code in health_codes.values())
        assert not any(
            c.laddr and c.laddr.port == 9226 and c.status == "LISTEN"
            for c in psutil.net_connections(kind="tcp")
        )
        evidence.update(
            cleanup=True,
            removed={"users": len(owners), "projects": len(projects), "documents": len(docs)},
            health=health_codes,
            protected=final,
            browser_debug_listener_closed=True,
        )
        save(public / "cleanup.json", evidence)
        ledger.unlink()
        print("Exact-run cleanup complete", evidence["removed"], "health", health_codes)
    finally:
        await engine.dispose()
