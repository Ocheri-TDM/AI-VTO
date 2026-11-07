"""Read-only preservation evidence, then exact-run cleanup when requested."""

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
from quality.regression_acceptance import LEDGER, PUBLIC, ROOT, save


async def run(state, cleanup=False):
    settings = Settings()
    assert ":55432/" in settings.database_url
    engine, sessions = create_database(settings.database_url)
    owners = [u["id"] for u in state["users"]]
    assert len(owners) == 2

    def bound(sql, key="ids"):
        return text(sql).bindparams(bindparam(key, expanding=True))

    try:
        async with sessions() as db:
            await db.execute(text("SET TRANSACTION READ ONLY"))
            projects = (
                await db.scalars(bound("SELECT id FROM projects WHERE user_id IN :ids"), {"ids": owners})
            ).all()
            assert set(projects) == set(state["projects"])
            docs = (
                await db.scalars(
                    bound("SELECT id FROM presentation_documents WHERE user_id IN :ids"), {"ids": owners}
                )
            ).all()
            protected = {}
            for table, order, field, excluded in [
                ("users", "id", "id", owners),
                ("projects", "id", "user_id", owners),
                ("project_collection_items", "project_id,offer_id", "project_id", projects),
                ("presentation_documents", "id", "user_id", owners),
                ("presentation_revisions", "id", "document_id", docs),
                ("commercial_offer_shares", "document_id", "document_id", docs),
            ]:
                where = f"({field} IS NULL OR {field} NOT IN :ids)" if excluded else "TRUE"
                sql = f"SELECT count(*) count,md5(string_agg(row_to_json(t)::text,'' ORDER BY {order})) fingerprint FROM {table} t WHERE {where}"
                row = dict(
                    (
                        await db.execute(
                            bound(sql) if excluded else text(sql), {"ids": excluded} if excluded else {}
                        )
                    )
                    .mappings()
                    .one()
                )
                protected[table] = {
                    "before": state["baseline"]["protected"][table],
                    "after": row,
                    "unchanged": row == state["baseline"]["protected"][table],
                }
            assert all(p["unchanged"] for p in protected.values()), (
                "Protected user data changed; do not clean up before investigation"
            )
            catalog = dict(
                (
                    await db.execute(
                        text(
                            "SELECT count(*) offers,count(DISTINCT product_id) families,count(*) FILTER (WHERE payload->'metadata'->>'vto_enrichment'='PENDING') enrichment_pending FROM supplier_offers"
                        )
                    )
                )
                .mappings()
                .one()
            )
            quantities = dict(
                (
                    await db.execute(
                        text(
                            "SELECT count(*) FILTER (WHERE stock>0) positive,count(*) FILTER (WHERE stock=0) zero,count(*) FILTER (WHERE stock IS NULL) unknown FROM product_observations"
                        )
                    )
                )
                .mappings()
                .one()
            )
            jobs = []
            for row in (
                await db.execute(
                    text("SELECT id,kind,status,error,checkpoint,metrics FROM catalog_sync_jobs")
                )
            ).mappings():
                cp = row["checkpoint"]
                jobs.append(
                    {k: row[k] for k in ("id", "kind", "status", "error", "metrics")}
                    | {
                        "page": cp.get("page"),
                        "seen_parents": len(cp.get("seen_parent_ids", [])),
                        "reported_total": cp.get("reported_total"),
                        "coverage_complete": cp.get("coverage_complete", False),
                        "failed_cards": {
                            k: {a: v.get(a) for a in ("error", "attempts", "stage", "core_offer_ids")}
                            for k, v in cp.get("failed_products", {}).items()
                        },
                        "card_progress": {
                            k: {
                                "html_saved": bool(v.get("detail")),
                                "api_children": len(v.get("children", {}).get("rows", [])),
                                "api_children_complete": v.get("children", {}).get("complete", False),
                                "ajax_responses": len(v.get("ajax", {})),
                                "core_committed": v.get("core_committed", False),
                            }
                            for k, v in cp.get("card_progress", {}).items()
                        },
                    }
                )
            migration = await db.scalar(text("SELECT version_num FROM alembic_version"))
        key_unchanged = (
            hashlib.sha256(
                (settings.presentation_storage / "keys/commercial-offers.key").read_bytes()
            ).hexdigest()
            == state["key_hash"]
        )
        assert key_unchanged
        async with httpx.AsyncClient(
            base_url="http://127.0.0.1:8000", cookies=state["users"][0]["cookies"], timeout=15
        ) as client:
            runtime = await client.get("/api/dev/runtime")
            runtime_data = (
                runtime.json()
                if runtime.status_code == 200
                else {"status": "NOT AVAILABLE", "http_status": runtime.status_code}
            )
            health = (await client.get("/api/health")).status_code
        evidence = {
            "status": "PASS",
            "protected": protected,
            "encryption_key_unchanged": key_unchanged,
            "catalog": catalog,
            "quantities": quantities,
            "catalog_growth": catalog["offers"] - state["baseline"]["catalog"]["offers"],
            "jobs": jobs,
            "migration": migration,
            "runtime": runtime_data,
            "health": health,
            "cleanup": False,
        }
        save(PUBLIC / "preservation.json", evidence)
        if not cleanup:
            print("Read-only preservation verified; catalog", catalog, "runtime", runtime.status_code)
            return
        async with sessions() as db, db.begin():
            for user in state["users"]:
                name = await db.scalar(
                    text("SELECT display_name FROM users WHERE id=:id"), {"id": user["id"]}
                )
                assert name == user["name"] and name.startswith("understanding-" + state["run"] + "-")
                owned = (
                    await db.scalars(text("SELECT id FROM projects WHERE user_id=:id"), {"id": user["id"]})
                ).all()
                for project in owned:
                    assert project in state["projects"]
                    await db.execute(
                        text("DELETE FROM projects WHERE id=:p AND user_id=:u"),
                        {"p": project, "u": user["id"]},
                    )
                await db.execute(text("DELETE FROM users WHERE id=:id"), {"id": user["id"]})
        # Only these generated documents could have task-owned render folders.
        base = (settings.presentation_storage / "documents").resolve()
        for identifier in docs:
            target = (base / identifier).resolve()
            assert target.parent == base
            if target.exists():
                shutil.rmtree(target)
        from check_search_safety import PG_BIN, stop_isolated_api

        child = state["isolated"]
        directory = Path(child["directory"]).resolve()
        assert (
            directory.parent == (ROOT / ".local").resolve()
            and directory.name == "search-safety-" + child["run"]
        )
        assert child["run"] == state["run"] + "-regression" and child["pg_port"] == 55433
        if psutil.pid_exists(child["api_pid"]):
            stop_isolated_api(child)
        pg = psutil.Process(child["pg_pid"])
        assert str(Path(child["pg_data"])) in " ".join(pg.cmdline())
        stopped = await asyncio.to_thread(
            subprocess.run,
            [str(PG_BIN / "pg_ctl.exe"), "-D", child["pg_data"], "-m", "fast", "-w", "stop"],
            capture_output=True,
            timeout=20,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        assert stopped.returncode == 0
        if state.get("edge_pid") and psutil.pid_exists(state["edge_pid"]):
            parent = psutil.Process(state["edge_pid"])
            assert "--user-data-dir=" + state["edge_profile"] in parent.cmdline()
            processes = [parent, *parent.children(recursive=True)]
            for p in reversed(processes):
                try:
                    p.terminate()
                except psutil.NoSuchProcess:
                    pass
            _, alive = await asyncio.to_thread(psutil.wait_procs, processes, timeout=5)
            for p in alive:
                p.kill()
        profile = Path(state["edge_profile"]).resolve()
        assert (
            profile.parent == (ROOT / ".local").resolve()
            and profile.name == "understanding-edge-" + state["run"]
        )
        if profile.exists():
            shutil.rmtree(profile)
        shutil.rmtree(directory)
        child_ledger = Path(child["ledger"]).resolve()
        assert child_ledger == (ROOT / ".local/search-understanding-isolated.json").resolve()
        child_ledger.unlink(missing_ok=True)
        LEDGER.unlink()
        evidence.update(
            cleanup=True,
            deleted_task_users=len(owners),
            deleted_task_projects=len(projects),
            deleted_task_documents=len(docs),
            isolated_cluster_removed=True,
            browser_listener_closed=True,
        )
        save(PUBLIC / "preservation.json", evidence)
        print("Exact-run cleanup complete; original user data and main database retained")
    finally:
        await engine.dispose()
