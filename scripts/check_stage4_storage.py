"""Real PostgreSQL queue lease/recovery checks using isolated, disposable test jobs."""
import asyncio
import json
from datetime import timedelta
from pathlib import Path
from uuid import uuid4
from sqlalchemy import delete, select, text, update
from app.config import Settings
from app.database.connection import create_database
from app.database.index import IndexRepository
from app.database.models import CatalogSyncJob, SupplierSyncState
from app.domain.models import utcnow


async def main():
    settings=Settings()
    engine,sessions=create_database(settings.database_url)
    repository=IndexRepository(sessions,settings)
    supplier='test-'+uuid4().hex[:20]
    try:
        identifier=await repository.enqueue(supplier,'TARGETED_RESEARCH',{'query':'lease-test'})
        results=await asyncio.gather(repository.claim('one',[identifier]),repository.claim('two',[identifier]))
        assert sum(r is not None for r in results)==1
        async with sessions() as db,db.begin():
            await db.execute(update(SupplierSyncState).where(SupplierSyncState.supplier==supplier)
                             .values(lease_until=utcnow()-timedelta(seconds=1)))
        recovered=await repository.claim('recovered',[identifier])
        assert recovered and recovered['id']==identifier
        async with sessions() as db:
            version=(await db.execute(text('SELECT version_num FROM alembic_version'))).scalar_one()
            indexes=(await db.execute(text("SELECT indexname FROM pg_indexes WHERE tablename IN ('indexed_products','product_observations','catalog_sync_jobs')"))).scalars().all()
        report={'migration':version,'concurrent_claims':1,'expired_lease_recovered':True,'indexes':indexes}
        Path('.local/stage4/storage.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        print(json.dumps(report))
    finally:
        async with sessions() as db,db.begin():
            await db.execute(delete(CatalogSyncJob).where(CatalogSyncJob.supplier==supplier))
            await db.execute(delete(SupplierSyncState).where(SupplierSyncState.supplier==supplier))
        await engine.dispose()


if __name__=='__main__':asyncio.run(main())
