"""Real bounded background sync, then browser-free index acceptance and EXPLAIN."""
import asyncio
import json
import time
from pathlib import Path

from sqlalchemy import select, text

from app.application.background_index import BackgroundIndex
from app.application.index_query import IndexQueryService
from app.application.research_planner import ResearchPlanner
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
    settings = Settings(research_job_timeout_seconds=25, provider_retries=0)
    engine,sessions = create_database(settings.database_url)
    browser = BrowserEngine(settings)
    providers = [cls(browser,settings) for cls in (GiftsProvider,UcontayProvider,PortobelloProvider,
                                                  HappyGiftsProvider,ArteGiftsProvider,OasisProvider)]
    repository = IndexRepository(sessions,settings)
    host = BackgroundIndex(repository,providers,settings)
    folder = Path('.local/stage4'); folder.mkdir(parents=True,exist_ok=True)
    ids = []
    try:
        for category,query in [('pencil','карандаш'),('pen','ручка'),('bottle','бутылка')]:
            for provider in providers:
                ids.append(await repository.enqueue(provider.supplier,'TARGETED_RESEARCH',
                    {'query':query,'category':category},priority=30))
        # Claim/run explicitly once per job: profiling must not conceal retries or create an unbounded crawl.
        done = set()
        async def worker():
            while len(done)<len(ids):
                job = await repository.claim(host.owner,set(ids)-done)
                if not job:
                    await asyncio.sleep(.5)
                    continue
                if job['id'] in done:
                    await repository.finish(job,host.owner,'PENDING','PROFILE_ALREADY_ATTEMPTED')
                    await asyncio.sleep(.2)
                    continue
                done.add(job['id'])
                print('SYNC',job['supplier'],job['checkpoint'].get('query'),flush=True)
                await host.run(job)
        await asyncio.gather(worker(),worker())
        # Browser closed BEFORE user query measurements.
        await browser.close()
        results=[]
        for query in ['Карандаши 300шт','Ручки 300шт','Синие бутылки 300шт',
                      'Синий мерч для IT конференции 300шт','Антистресс 100 шт']:
            start=time.perf_counter()
            intent=await ResearchPlanner().parse(query)
            products,metrics=await IndexQueryService(repository).search(intent)
            results.append({'query':query,'intent':intent.model_dump(mode='json'),**metrics,
                            'response_ms':round((time.perf_counter()-start)*1000,2),
                            'names':[p.name for p in products]})
        async with sessions() as db:
            jobs=(await db.scalars(select(CatalogSyncJob).where(CatalogSyncJob.id.in_(ids)))).all()
            report={'queries':results,'jobs':[{'supplier':j.supplier,'status':j.status,'error':j.error,
                'metrics':j.metrics,'checkpoint':j.checkpoint} for j in jobs]}
            plan=(await db.execute(text("EXPLAIN ANALYZE SELECT o.id FROM supplier_offers o JOIN indexed_products p ON p.id=o.product_id JOIN product_observations v ON v.offer_id=o.id WHERE p.category='pencil' AND v.stock>=300 AND v.expires_at>now()"))).all()
            report['explain_analyze']=[r[0] for r in plan]
        (folder/'live.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(results,ensure_ascii=False),flush=True)
    finally:
        await host.close();await browser.close();await engine.dispose()


if __name__=='__main__':asyncio.run(main())
