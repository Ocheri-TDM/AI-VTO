"""Controlled comparison against committed pre-fix code on owned PostgreSQL."""

import asyncio
import copy
import json
import subprocess
import time
import types
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import event, update

from app.config import Settings
from app.database.connection import create_database
from app.database.index import IndexRepository
from app.database.models import CatalogSyncJob, ProductObservation, SupplierSyncState
from app.domain.models import utcnow
from app.providers.vto.provider import normalize
from quality.regression_acceptance import PUBLIC, ROOT, save


async def run(state):
    child = state["isolated"]
    assert ":55433/" in child["database_url"]
    settings = Settings(database_url=child["database_url"], background_research_enabled=False)
    engine, sessions = create_database(settings.database_url)
    source = subprocess.run(
        ["git", "show", "dd18c69:apps/api/app/database/index.py"], cwd=ROOT, capture_output=True, check=True
    ).stdout.decode("utf-8")
    legacy = types.ModuleType("quality.legacy_index_replay")
    exec(compile(source, "legacy_index_replay", "exec"), legacy.__dict__)
    fixture = json.loads((ROOT / "apps/api/tests/fixtures/vto/melango.json").read_text("utf-8"))
    products = normalize(**fixture)
    for p in products:
        p.id = "vto:replay-" + p.id.split(":")[1]
        p.metadata["vto_product_id"] = 99000000001
    count = [0]

    def statement(*_):
        count[0] += 1

    event.listen(engine.sync_engine, "before_cursor_execute", statement)
    result = {
        "database": "isolated PostgreSQL :55433",
        "baseline_commit": "dd18c69",
        "commercial_fixture": "Melango 4 real variants, remapped only inside isolated DB",
        "ingest": {},
    }
    try:
        for label, klass in [("before", legacy.IndexRepository), ("after", IndexRepository)]:
            repo = klass(sessions, settings)
            await repo.ingest(copy.deepcopy(products))
            times = []
            queries = []
            for _ in range(7):
                before = count[0]
                started = time.perf_counter()
                await repo.ingest(copy.deepcopy(products))
                times.append(round(time.perf_counter() - started, 6))
                queries.append(count[0] - before)
            result["ingest"][label] = {
                "seconds": times,
                "sql_statements": queries,
                "median_seconds": sorted(times)[3],
            }
        # Delayed old refresh must lose to a newer zero even with concurrent
        # transactions. Locks are real PostgreSQL advisory transaction locks.
        old = copy.deepcopy(products[0])
        new = copy.deepcopy(old)
        new.fetched_at = old.fetched_at + timedelta(minutes=1)
        new.stock_quantity = new.total_quantity = 0
        new.availability_records = []
        new.availability_status = "UNAVAILABLE_NOW"
        index = IndexRepository(sessions, settings)
        await asyncio.gather(index.ingest([new]), index.ingest([old]))
        async with sessions() as db:
            assert (await db.get(ProductObservation, new.id)).stock == 0
        result["concurrent_late_refresh"] = "PASS: latest zero preserved"
        result["card_cooldown"] = {}
        for label, klass in [("before", legacy.IndexRepository), ("after", IndexRepository)]:
            repo = klass(sessions, settings)
            identifier = str(uuid4())
            async with sessions() as db, db.begin():
                state_row = await db.get(SupplierSyncState, "vto")
                if state_row is None:
                    db.add(SupplierSyncState(supplier="vto"))
                    await db.flush()
                await db.execute(
                    update(SupplierSyncState)
                    .where(SupplierSyncState.supplier == "vto")
                    .values(
                        lease_owner="replay",
                        lease_until=utcnow() + timedelta(minutes=1),
                        failures=0,
                        error=None,
                        retry_after=utcnow(),
                    )
                )
                db.add(
                    CatalogSyncJob(
                        id=identifier,
                        dedup_key=identifier,
                        supplier="vto",
                        kind="OBSERVATION_REFRESH",
                        status="RUNNING",
                        checkpoint={"test_run": state["run"]},
                        metrics={},
                    )
                )
            job = {"id": identifier, "supplier": "vto", "kind": "OBSERVATION_REFRESH", "metrics": {}}
            await repo.finish(job, "replay", "FAILED", "VTO_NOT_FOUND")
            async with sessions() as db:
                row = await db.get(SupplierSyncState, "vto")
                result["card_cooldown"][label] = {
                    "source_error": row.error,
                    "supplier_block_seconds": round(max(0, (row.retry_after - utcnow()).total_seconds()), 3),
                }
        result["note"] = (
            "Seven warmed ingest transactions; DB calls measured, not a production throughput multiplier. Cooldown compared as persisted eligibility timestamps, without sleeping 15 minutes."
        )
        save(PUBLIC / "import-replay.json", result)
        print("Isolated PostgreSQL replay complete: SQL counts, card cooldown, concurrent newer zero")
    finally:
        await engine.dispose()
