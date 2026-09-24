"""Refresh real observations through persisted jobs, without touching observation timestamps manually."""
import asyncio
import json
import sys
from pathlib import Path
from sqlalchemy import select
from app.application.background_index import BackgroundIndex
from app.browser import BrowserEngine
from app.config import Settings
from app.database.connection import create_database
from app.database.index import IndexRepository
from app.database.models import CatalogSyncJob
from app.providers.artegifts import ArteGiftsProvider
from app.providers.gifts import GiftsProvider
from app.providers.happygifts import HappyGiftsProvider
from app.providers.oasis import OasisProvider
from app.providers.portobello import PortobelloProvider
from app.providers.ucontay import UcontayProvider


async def main():
    settings=Settings(research_job_timeout_seconds=90,provider_retries=0)
    engine,sessions=create_database(settings.database_url)
    browser=BrowserEngine(settings)
    providers=[cls(browser,settings) for cls in (GiftsProvider,UcontayProvider,PortobelloProvider,
                                                HappyGiftsProvider,ArteGiftsProvider,OasisProvider)]
    if len(sys.argv)>1:
        providers=[p for p in providers if p.supplier in sys.argv[1:]]
    repository=IndexRepository(sessions,settings)
    host=BackgroundIndex(repository,providers,settings)
    ids={await repository.enqueue(p.supplier,'OBSERVATION_REFRESH',priority=100) for p in providers}
    done=set()
    async def worker():
        while ids-done:
            job=await repository.claim(host.owner,ids-done)
            if not job:
                await asyncio.sleep(1)
                continue
            done.add(job['id'])
            print('REFRESH',job['supplier'],flush=True)
            await host.run(job)
    try:
        async with asyncio.timeout(600):
            await asyncio.gather(worker(),worker())
        async with sessions() as db:
            jobs=(await db.scalars(select(CatalogSyncJob).where(CatalogSyncJob.id.in_(ids)))).all()
            report=[{'supplier':j.supplier,'status':j.status,'error':j.error,'metrics':j.metrics} for j in jobs]
        label='refresh' if len(sys.argv)==1 else 'refresh-'+'-'.join(sys.argv[1:])
        Path(f'.local/stage4/{label}.json').write_text(json.dumps({'jobs':report,'browser':browser.metrics},
                                                        ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(report),flush=True)
    finally:
        await host.close();await browser.close();await engine.dispose()


if __name__=='__main__':asyncio.run(main())
