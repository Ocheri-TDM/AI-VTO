"""A task-owned PostgreSQL cluster and cross-process lease acceptance."""

import asyncio
import json
import os
import socket
import subprocess
import sys

from app.config import Settings
from app.database.connection import create_database
from app.database.index import IndexRepository


async def run(state, root, ledger, public):
    from check_chat_workflow import save
    from check_search_safety import PG_BIN

    directory = (root / ".local" / ("nav-recovery-pg-" + state["run"])).resolve()
    assert directory.parent == (root / ".local").resolve() and not directory.exists()
    with socket.socket() as probe:
        assert probe.connect_ex(("127.0.0.1", 55433)) != 0
    directory.mkdir()
    data = directory / "data"
    initialized = await asyncio.to_thread(
        subprocess.run,
        [
            str(PG_BIN / "initdb.exe"),
            "-D",
            str(data),
            "-U",
            "quality_owner",
            "-A",
            "trust",
            "--encoding=UTF8",
            "--locale=C",
        ],
        capture_output=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert initialized.returncode == 0, "Isolated initdb failed"
    with (directory / "postgres.log").open("ab") as log:
        process = subprocess.Popen(
            [
                str(PG_BIN / "postgres.exe"),
                "-D",
                str(data),
                "-p",
                "55433",
                "-h",
                "127.0.0.1",
                "-c",
                "shared_buffers=64MB",
                "-c",
                "max_connections=40",
            ],
            stdout=log,
            stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    url = "postgresql+asyncpg://quality_owner@127.0.0.1:55433/postgres"
    state["isolated"] = {"directory": str(directory), "data": str(data), "pid": process.pid, "url": url}
    save(ledger, state)
    for _ in range(100):
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", 55433)) == 0:
                break
        await asyncio.sleep(0.1)
    env = dict(os.environ, DATABASE_URL=url, VTO_FAULT_POSTGRES_URL=url)
    migrated = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-m", "alembic", "-c", "apps/api/alembic.ini", "upgrade", "head"],
        cwd=root,
        env=env,
        capture_output=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert migrated.returncode == 0, migrated.stderr.decode("utf-8", errors="replace")[-1000:]
    with (root / ".local/nav-recovery-postgres-tests.log").open("wb") as log:
        tests = await asyncio.to_thread(
            subprocess.run,
            [
                sys.executable,
                "-m",
                "pytest",
                "apps/api/tests/test_vto_recovery.py",
                "apps/api/tests/test_vto_throughput.py",
                "-q",
            ],
            cwd=root,
            env=env,
            stdout=log,
            stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    assert tests.returncode == 0, "See .local/nav-recovery-postgres-tests.log"
    engine, sessions = create_database(url)
    try:
        index = IndexRepository(sessions, Settings(_env_file=None, database_url=url))
        identifier = await index.enqueue("vto", "FULL_CATALOG_RECONCILIATION", {"proof": "two_process_lease"})
        code = "import asyncio,json,sys;sys.path.insert(0,'apps/api');from app.config import Settings;from app.database.connection import create_database;from app.database.index import IndexRepository\nasync def main():\n e,s=create_database(sys.argv[1]);r=await IndexRepository(s,Settings(_env_file=None,database_url=sys.argv[1])).claim(sys.argv[2],job_ids=[sys.argv[3]]);print(json.dumps({'claimed':bool(r)}));await e.dispose()\nasyncio.run(main())"
        processes = [
            subprocess.Popen(
                [sys.executable, "-c", code, url, "process-" + str(n), identifier],
                cwd=root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            for n in range(2)
        ]
        results = await asyncio.gather(*[asyncio.to_thread(p.communicate, timeout=30) for p in processes])
        claims = [json.loads(out.decode()) for out, err in results]
        assert all(p.returncode == 0 for p in processes) and sum(p["claimed"] for p in claims) == 1
        save(
            public / "postgres.json",
            {
                "status": "PASS",
                "migrations": "fresh PostgreSQL to 0027",
                "provider_tests": "See permanent pytest output",
                "process_claims": claims,
                "live_vto_requests": 0,
            },
        )
        print("Isolated PostgreSQL regressions passed; two processes, one claim")
    finally:
        await engine.dispose()


async def document_race(state, public):
    from pathlib import Path
    from uuid import uuid4

    from check_chat_workflow import save

    from app.application.commercial_offers import CommercialOfferService
    from app.application.presentations import PresentationService
    from app.database.models import PresentationDocument, PresentationRevision, Project, User
    from app.domain.commercial_offer import OfferDelete

    url = state["isolated"]["url"]
    assert url == "postgresql+asyncpg://quality_owner@127.0.0.1:55433/postgres"
    engine, sessions = create_database(url)
    try:
        settings = Settings(
            _env_file=None,
            database_url=url,
            presentation_storage=Path(state["isolated"]["directory"]) / "documents-proof",
        )
        service = PresentationService(sessions, settings, None)
        offers = CommercialOfferService(service)
        statuses = []
        for _ in range(3):
            user, project, document, revision = [str(uuid4()) for _ in range(4)]
            async with sessions() as db, db.begin():
                db.add(
                    User(id=user, display_name="Isolated deletion", normalized_name=user, password_hash="!")
                )
                await db.flush()
                db.add(Project(id=project, user_id=user, name="Isolated lifecycle"))
                await db.flush()
                db.add(
                    PresentationDocument(
                        id=document,
                        project_id=project,
                        user_id=user,
                        is_primary_offer=True,
                        draft={"kind": "COMMERCIAL_OFFER", "items": [], "settings": {"title": "Isolated"}},
                    )
                )
                await db.flush()
                db.add(
                    PresentationRevision(
                        id=revision,
                        document_id=document,
                        source_version=1,
                        input={"kind": "COMMERCIAL_OFFER"},
                    )
                )
            claimed = await service.claim_next()
            assert claimed and claimed.id == revision
            await asyncio.gather(
                offers.delete(document, user, OfferDelete(version=1, confirmed=True)),
                service.publish(claimed, "READY", result={}),
            )
            async with sessions() as db:
                doc = await db.get(PresentationDocument, document)
                row = await db.get(PresentationRevision, revision)
                assert doc.deleted_at and not doc.is_primary_offer and row.status in {"READY", "CANCELLED"}
                assert await offers.owned(document, user) is None
                statuses.append(row.status)
        save(
            public / "postgres-document-race.json",
            {
                "status": "PASS",
                "iterations": 3,
                "terminal_revision_states": statuses,
                "network_calls": 0,
                "scope": "isolated PostgreSQL, real claim and concurrent delete/final publication",
            },
        )
        print("Isolated PostgreSQL document claim/delete/publish passed", statuses)
    finally:
        await engine.dispose()
