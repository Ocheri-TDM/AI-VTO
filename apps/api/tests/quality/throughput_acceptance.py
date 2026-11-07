"""Bounded sole-worker measurements against the existing populated database."""

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import psutil
from sqlalchemy import select, text

from app.application.background_index import BackgroundIndex
from app.config import Settings
from app.database.connection import create_database
from app.database.index import IndexRepository
from app.database.models import CatalogSyncJob
from app.providers.vto import VTOProvider
from quality.regression_acceptance import LEDGER, PUBLIC, ROOT, call, save


async def snapshot(sessions):
    async with sessions() as db:
        await db.execute(text("SET TRANSACTION READ ONLY"))
        jobs = []
        for row in (await db.scalars(select(CatalogSyncJob))).all():
            cp = row.checkpoint or {}
            jobs.append(
                {
                    "id": row.id,
                    "kind": row.kind,
                    "status": row.status,
                    "error": row.error,
                    "available_at": row.available_at,
                    "updated_at": row.updated_at,
                    "metrics": row.metrics,
                    "checkpoint": {
                        k: cp.get(k)
                        for k in (
                            "page",
                            "per_page",
                            "reported_total",
                            "reported_pages",
                            "start_total",
                            "coverage_complete",
                            "stages",
                        )
                    },
                    "processed_parents": len(cp.get("seen_parent_ids", [])),
                    "pending_parents": len(cp.get("pending_products", cp.get("pending", []))),
                    "failed_cards": len(cp.get("failed_products", {})),
                }
            )
        return {
            "at": await db.scalar(text("SELECT CURRENT_TIMESTAMP")),
            "jobs": jobs,
            "supplier_states": [
                dict(r) for r in (await db.execute(text("SELECT * FROM supplier_sync_states"))).mappings()
            ],
            "catalog": dict(
                (
                    await db.execute(
                        text(
                            "SELECT count(*) offers, count(DISTINCT product_id) families FROM supplier_offers"
                        )
                    )
                )
                .mappings()
                .one()
            ),
        }


async def run(stage):
    state = json.loads(LEDGER.read_text("utf-8"))
    if stage in ("restart-main", "restore-defaults"):
        parent = psutil.Process(state["api_wrapper"])
        assert Path(parent.cwd()).resolve() == (ROOT / "apps/api").resolve()
        assert "-m app" in " ".join(parent.cmdline())
        owned = [parent, *parent.children(recursive=True)]
        listeners = {
            c.pid
            for c in psutil.net_connections(kind="tcp")
            if c.laddr and c.laddr.port == 8000 and c.status == "LISTEN"
        }
        assert listeners and listeners <= {p.pid for p in owned}
        for p in reversed(owned):
            p.terminate()
        await asyncio.to_thread(psutil.wait_procs, owned, timeout=5)
        with (
            (ROOT / ".local/api-search-regression.log").open("ab") as out,
            (ROOT / ".local/api-search-regression.err.log").open("ab") as err,
        ):
            environment = dict(os.environ)
            if stage == "restart-main":
                environment.update(BACKGROUND_RESEARCH_ENABLED="false", DEBUG_AGENT=os.environ.get("UNDERSTANDING_DIAGNOSTICS", "false"))
            process = subprocess.Popen(
                [sys.executable, "-m", "app"],
                cwd=ROOT / "apps/api",
                stdout=out,
                stderr=err,
                env=environment,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        state["api_wrapper"] = process.pid
        save(LEDGER, state)
        async with httpx.AsyncClient() as client:
            for _ in range(80):
                try:
                    if (await client.get("http://127.0.0.1:8000/api/health")).status_code == 200:
                        print(
                            "Main API restarted with configured defaults" if stage == "restore-defaults" else "Main API restarted for the bounded index-only measurement"
                        )
                        return
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.25)
        raise RuntimeError("API health timeout")
    if stage == "browser":
        from quality.understanding_browser import run as browser_run

        return await browser_run(state)
    if stage == "audit-controls":
        from quality.controls_audit import run as controls_audit

        return await controls_audit(state)
    if stage in ("verify", "cleanup"):
        from quality.understanding_cleanup import run as preservation_run

        return await preservation_run(state, cleanup=stage == "cleanup")
    if stage == "vto-replay":
        from quality.throughput_replay import run as replay_run

        return await replay_run(state)
    if stage == "migration-chain":
        import asyncpg

        assert (
            state["isolated"]["database_url"] == "postgresql+asyncpg://quality_owner@127.0.0.1:55433/postgres"
        )
        name = "quality_migrations_" + state["run"]
        assert len(state["run"]) == 16 and all(c in "0123456789abcdef" for c in state["run"])
        connection = await asyncpg.connect(
            host="127.0.0.1", port=55433, user="quality_owner", database="postgres"
        )
        try:
            assert not await connection.fetchval("SELECT 1 FROM pg_database WHERE datname=$1", name)
            await connection.execute('CREATE DATABASE "' + name + '"')
        finally:
            await connection.close()
        state["isolated"]["migration_database"] = name
        save(LEDGER, state)
        result = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-m", "alembic", "-c", "apps/api/alembic.ini", "upgrade", "head"],
            cwd=ROOT,
            env=dict(os.environ, DATABASE_URL="postgresql+asyncpg://quality_owner@127.0.0.1:55433/" + name),
            capture_output=True,
            timeout=60,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")[-1500:]
        connection = await asyncpg.connect(host="127.0.0.1", port=55433, user="quality_owner", database=name)
        try:
            head = await connection.fetchval("SELECT version_num FROM alembic_version")
            assert head == "0025_offer_lifecycle"
        finally:
            await connection.close()
        save(
            PUBLIC / "migration-chain.json",
            {
                "status": "PASS",
                "database": "new disposable database on isolated :55433",
                "head": head,
                "main_database_migrated": False,
                "note": "Existing main database and its version remain untouched.",
            },
        )
        print("Fresh isolated PostgreSQL Alembic chain reached", head)
        return
    settings = Settings(
        research_job_timeout_seconds=int(os.environ.get("UNDERSTANDING_SLICE_SECONDS", "100"))
    )
    engine, sessions = create_database(settings.database_url)
    provider = VTOProvider(settings)
    try:
        before = await snapshot(sessions)
        if stage == "vto-baseline":
            save(PUBLIC / "import-before.json", before)
            print("Read-only VTO baseline captured", before["catalog"])
            return
        assert stage == "vto-live"
        # Main API is deliberately index-only during this measurement. This
        # command uses the normal durable claim and cannot steal an active lease.
        worker = BackgroundIndex(IndexRepository(sessions, settings), [provider], settings)
        existing = next(
            j
            for j in before["jobs"]
            if j["kind"] == "FULL_CATALOG_RECONCILIATION" and j["status"] == "PENDING"
        )
        job = await worker.repository.claim(worker.owner, job_ids=[existing["id"]])
        if not job:
            save(
                PUBLIC / "import-live.json",
                {"status": "BLOCKED_BY_EXISTING_LEASE_OR_SOURCE_BACKOFF", "before": before},
            )
            print("Existing supplier backoff/lease respected; no second crawler")
            return
        started = time.monotonic()
        search = []

        async def searches():
            async with httpx.AsyncClient(
                base_url="http://127.0.0.1:8000", cookies=state["users"][1]["cookies"], timeout=90
            ) as c:
                chat = await call(c, "POST", "/api/chats", json={"project_name": "Bounded import canary"})
                project = next(p for p in await call(c, "GET", "/api/projects") if p["chat_id"] == chat["id"])
                state["projects"].append(project["id"])
                save(LEDGER, state)
                queries = [
                    "ручки",
                    "рюкзаки",
                    "бутылки",
                    "чёрные ручки",
                    "блокноты",
                    "ручки и блокноты",
                    "бутылки и рюкзаки",
                    "ручки и кепки",
                    "что-то для записи",
                    "подарки для руководителей до 12000 тенге",
                ]
                for query in queries:
                    began = time.monotonic()
                    response = await c.post(f"/api/projects/{project['id']}/requests", json={"query": query})
                    data = response.json()
                    search.append(
                        {
                            "query": query,
                            "status": response.status_code,
                            "seconds": round(time.monotonic() - began, 4),
                            "mode": (data.get("research", {}).get("intent") or {}).get("mode"),
                        }
                    )
                    await asyncio.sleep(0.3)

        await asyncio.gather(worker.run(job), searches())
        elapsed = time.monotonic() - started
        after = await snapshot(sessions)
        previous = next(j for j in before["jobs"] if j["id"] == job["id"])
        latest = next(j for j in after["jobs"] if j["id"] == job["id"])
        parents = latest["processed_parents"] - previous["processed_parents"]
        save(
            PUBLIC / "import-live.json",
            {
                "status": "MEASURED",
                "seconds": round(elapsed, 3),
                "before": before,
                "after": after,
                "parents_delta": parents,
                "offers_delta": after["catalog"]["offers"] - before["catalog"]["offers"],
                "parents_per_hour_window": round(parents * 3600 / elapsed, 2),
                "searches": search,
                "search_p95_seconds_small_smoke": sorted(r["seconds"] for r in search)[-1],
                "http_delta": provider.requests,
                "errors_delta": provider.errors,
                "timings_ms": provider.timings,
                "concurrency": 2,
                "request_delay_seconds": settings.vto_request_delay_seconds,
                "note": "One bounded slice, not sustained throughput or production load p95. No before/after speedup inferred from unlike windows.",
            },
        )
        print(
            "Bounded sole-worker slice",
            parents,
            "parents",
            provider.requests,
            "HTTP; errors",
            provider.errors,
        )
    finally:
        await provider.close()
        await engine.dispose()
