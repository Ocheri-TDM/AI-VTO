"""Run/resume the VTO queue using the same supplier lease as the API scheduler."""

# The standalone command must add the API source directory before app imports.
# ruff: noqa: E402

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))

from app.application.background_index import BackgroundIndex
from app.config import Settings
from app.database.connection import create_database
from app.database.index import IndexRepository
from app.database.models import CatalogSyncJob
from app.providers.vto import VTOProvider


async def main():
    settings = Settings()
    engine, sessions = create_database(settings.database_url)
    provider = VTOProvider(settings)
    index = IndexRepository(sessions, settings)
    worker = BackgroundIndex(index, [provider], settings)
    job_id = await index.enqueue("vto", "FULL_CATALOG_RECONCILIATION", {"reason": "manual_vto_sync", "parser_version": provider.parser_version})
    try:
        while True:
            job = await index.claim(worker.owner, job_ids=[job_id])
            if job:
                await worker.run(job)
            async with sessions() as db:
                row = await db.get(CatalogSyncJob, job_id)
                print(json.dumps({"id": row.id, "status": row.status, "error": row.error,
                                  "metrics": row.metrics, "page": row.checkpoint.get("page"),
                                  "failed_products": len(row.checkpoint.get("failed_products", {}))}), flush=True)
                if row.status in ("COMPLETED", "FAILED", "CANCELLED"):
                    if row.status != "COMPLETED":
                        raise RuntimeError("Sync incomplete; see durable checkpoint")
                    break
            await asyncio.sleep(5)
    finally:
        await provider.close()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
