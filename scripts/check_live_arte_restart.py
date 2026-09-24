"""Kill a real ArteGifts worker; expire only its proven-dead lease and resume."""
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import func, select, update

from app.application.background_index import BackgroundIndex
from app.browser import BrowserEngine
from app.config import Settings
from app.database.connection import create_database
from app.database.index import IndexRepository
from app.database.models import CatalogSyncJob, SupplierOffer, SupplierSyncState
from app.domain.models import utcnow
from app.providers.artegifts import ArteGiftsProvider

FOLDER=Path('.local/final')


async def child(marker):
    settings=Settings(research_job_timeout_seconds=45,provider_retries=0,browser_timeout_ms=30000)
    engine,sessions=create_database(settings.database_url)
    browser=BrowserEngine(settings);repo=IndexRepository(sessions,settings)
    host=BackgroundIndex(repo,[ArteGiftsProvider(browser,settings)],settings)
    identifier=await repo.enqueue('artegifts','CATALOG_DISCOVERY',priority=50)
    try:
        job=None
        for _ in range(120):
            job=await repo.claim(host.owner,[identifier])
            if job:
                break
            await asyncio.sleep(.5)
        if not job:
            raise RuntimeError('Live supplier lease unavailable')
        Path(marker).write_text(json.dumps({'id':identifier,'owner':host.owner,'run_id':job['checkpoint'].get('run_id'),
                                           'roots':len(job['checkpoint'].get('roots',[])),
                                           'first_route':job['checkpoint'].get('branches',[{}])[0].get('route')}),encoding='utf-8')
        await host.run(job)
    finally:
        await browser.close();await engine.dispose()


async def main():
    marker=FOLDER/'arte-kill-marker.json'; resumed=FOLDER/'arte-resume-marker.json'
    for path in (marker,resumed):
        path.unlink(missing_ok=True)
    env={**os.environ,'PYTHONPATH':'apps/api','PYTHONIOENCODING':'utf-8'}
    log=(FOLDER/'arte-restart-child.log').open('w',encoding='utf-8')
    process=subprocess.Popen([sys.executable,__file__,'child',str(marker)],env=env,stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW)
    for _ in range(140):
        if marker.exists() or process.poll() is not None:
            break
        await asyncio.sleep(.5)
    if not marker.exists():
        process.terminate();process.wait();log.close()
        raise RuntimeError('No live claim; see child log')
    before=json.loads(marker.read_text())
    await asyncio.sleep(10)
    process.terminate();process.wait()
    settings=Settings();engine,sessions=create_database(settings.database_url)
    async with sessions() as db,db.begin():
        state=await db.get(SupplierSyncState,'artegifts')
        assert state.lease_owner==before['owner']
        await db.execute(update(SupplierSyncState).where(SupplierSyncState.supplier=='artegifts',SupplierSyncState.lease_owner==before['owner']).values(lease_until=utcnow()))
        row=await db.get(CatalogSyncJob,before['id'])
        saved={'run_id':row.checkpoint.get('run_id'),'roots':len(row.checkpoint.get('roots',[]))}
    replacement=subprocess.Popen([sys.executable,__file__,'child',str(resumed)],env=env,stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW)
    code=await asyncio.to_thread(replacement.wait,timeout=120)
    log.close()
    assert code==0 and resumed.exists()
    after=json.loads(resumed.read_text())
    assert before['id']==after['id'] and saved['run_id']==after['run_id'] and saved['roots']==after['roots']
    async with sessions() as db:
        row=await db.get(CatalogSyncJob,before['id'])
        total=await db.scalar(select(func.count()).select_from(SupplierOffer).where(SupplierOffer.supplier=='artegifts'))
        distinct=await db.scalar(select(func.count(func.distinct(SupplierOffer.id))).where(SupplierOffer.supplier=='artegifts'))
        assert total==distinct
        report={'kind':'LIVE ArteGifts ordinary Chromium and PostgreSQL','real_worker_killed':True,
                'lease_of_proven_dead_owner_expired':True,'before':before,'resumed':after,'saved_checkpoint':saved,
                'checkpoint_preserved':True,'offers':total,'unique_offers':distinct,'status':row.status,
                'error':row.error,'metrics':row.metrics,'full_catalog_complete':False}
    (FOLDER/'arte-live-restart.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2));await engine.dispose()


if __name__=='__main__':asyncio.run(child(sys.argv[2]) if len(sys.argv)>1 else main())
