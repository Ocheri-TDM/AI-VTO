"""Live cold/warm regression for the zero-result targeted-search path."""
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import asyncpg
from app.application.background_index import BackgroundIndex
from app.application.index_research import IndexedResearchService
from app.browser import BrowserEngine
from app.config import Settings
from app.database.connection import create_database
from app.database.conversations import ConversationRepository
from app.database.index import IndexRepository
from app.database.models import CatalogSyncJob
from app.database.research import ResearchRepository
from app.providers.happygifts import HappyGiftsProvider
from sqlalchemy import select
from sqlalchemy.engine import make_url


async def main():
    base = make_url(Settings().database_url)
    name = "souvenir_targeted_" + uuid4().hex[:10]
    admin_url = base.set(drivername="postgresql", database="postgres").render_as_string(hide_password=False)
    admin = await asyncpg.connect(admin_url)
    await admin.execute(f'CREATE DATABASE "{name}"')
    await admin.close()
    database_url = base.set(database=name).render_as_string(hide_password=False)
    env = {**os.environ, "DATABASE_URL": database_url, "PYTHONPATH": "apps/api"}
    migration = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-m", "alembic", "-c", "apps/api/alembic.ini", "upgrade", "head"],
        env=env, capture_output=True, text=True,
    )
    if migration.returncode:
        raise RuntimeError(migration.stderr)

    settings = Settings().model_copy(update={
        "database_url": database_url,
        "research_request_delay_seconds": 0.2,
        "research_provider_delays": {"happygifts": 0.2},
        "provider_retries": 0,
        "targeted_provider_timeout_seconds": 20,
        "background_research_enabled": False,
    })
    engine, sessions = create_database(database_url)
    browser = BrowserEngine(settings)
    provider = HappyGiftsProvider(browser, settings)
    index = IndexRepository(sessions, settings)
    conversations = ConversationRepository(sessions)
    service = IndexedResearchService(ResearchRepository(sessions), [provider], settings, None, index)
    worker = BackgroundIndex(index, [provider], settings)
    first_event = None

    async def update(job):
        nonlocal first_event
        synced = await service.sync(cold.id)
        if synced.products and first_event is None:
            first_event = time.perf_counter()

    worker.on_update = update
    started = time.perf_counter()
    chat = await conversations.create("Cold flask acceptance")
    cold = await service.start(chat, "фляжки")
    index_response = time.perf_counter()
    async with sessions() as db:
        jobs = (await db.scalars(select(CatalogSyncJob).where(
            CatalogSyncJob.id.in_(cold.background_job_ids)
        ))).all()
    happy_job = next(job for job in jobs if job.supplier == "happygifts")
    claimed_at = time.perf_counter()
    job = await index.claim(worker.owner, job_ids=[happy_job.id])
    job_started = time.perf_counter()
    await worker.run(job)
    completed = time.perf_counter()
    cold = await service.sync(cold.id)
    route = await index.category_route("happygifts", "фляжки")

    warm_chat = await conversations.create("Warm flask acceptance")
    warm_started = time.perf_counter()
    warm = await service.start(warm_chat, "фляжки")
    warm_ms = (time.perf_counter() - warm_started) * 1000
    async with sessions() as db:
        stored_job = await db.get(CatalogSyncJob, happy_job.id)
        metrics = dict(stored_job.metrics)
    report = {
        "query": "фляжки",
        "database": name,
        "cold": {
            "index_count": 0,
            "intent": cold.intent.model_dump(mode="json"),
            "index_query_and_enqueue_ms": round((index_response - started) * 1000, 2),
            "time_to_job_claim_ms": round((claimed_at - started) * 1000, 2),
            "time_to_job_start_ms": round((job_started - started) * 1000, 2),
            "time_to_first_UI_result_ms": round((first_event - started) * 1000, 2) if first_event else None,
            "targeted_completion_ms": round((completed - started) * 1000, 2),
            "products": [p.model_dump(mode="json") for p in cold.products],
            "job_metrics": metrics,
            "cached_graph_route": route,
        },
        "warm": {
            "matching_count": len(warm.products),
            "response_ms": round(warm_ms, 2),
            "browser_invoked": warm.timings.get("browser_invoked"),
            "targeted_jobs_created": len(warm.background_job_ids),
        },
    }
    path = Path(".local/final/targeted-flasks.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True, default=str))
    await browser.close()
    await engine.dispose()
    admin = await asyncpg.connect(admin_url)
    await admin.execute(f'DROP DATABASE "{name}"')
    await admin.close()


if __name__ == "__main__":
    asyncio.run(main())
