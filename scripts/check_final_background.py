"""Live execution slices and checkpoint resume against all six providers."""
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
    settings=Settings(research_job_timeout_seconds=45,provider_retries=0,browser_timeout_ms=30000)
    engine,sessions=create_database(settings.database_url)
    browser=BrowserEngine(settings)
    providers=[cls(browser,settings) for cls in (GiftsProvider,UcontayProvider,PortobelloProvider,HappyGiftsProvider,ArteGiftsProvider,OasisProvider)]
    requested = [name for name in sys.argv[1:] if name != 'refresh']
    if requested:
        providers = [provider for provider in providers if provider.supplier in requested]
    repository=IndexRepository(sessions,settings)
    host=BackgroundIndex(repository,providers,settings)
    kind='OBSERVATION_REFRESH' if 'refresh' in sys.argv else 'CATALOG_DISCOVERY'
    ids=[await repository.enqueue(p.supplier,kind,priority=30) for p in providers]
    report=[]
    try:
        for _ in range(2):
            for identifier in ids:
                job=await repository.claim(host.owner,[identifier])
                if not job:
                    continue
                before=dict(job['metrics'])
                await host.run(job)
                async with sessions() as db:
                    row=await db.get(CatalogSyncJob,identifier)
                    report.append({'supplier':row.supplier,'kind':kind,'status':row.status,'error':row.error,
                        'before':before,'after':row.metrics,'run_id':row.checkpoint.get('run_id'),
                        'roots':len(row.checkpoint.get('roots',[])),
                        'branches':[{k:b.get(k) for k in ('route','status','pages_scanned','products_seen','error_code')} for b in row.checkpoint.get('branches',[])],
                        'pending_refresh':len(row.checkpoint.get('pending',[]))})
                Path('.local/final/background-'+kind.lower()+'.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
                print(job['supplier'],report[-1]['status'],job['metrics'].get('products_seen',0),flush=True)
            await asyncio.sleep(6)
    finally:
        await host.close();await browser.close();await engine.dispose()


if __name__=='__main__':asyncio.run(main())
