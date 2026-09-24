"""Kill a real worker process; recover its PostgreSQL checkpoint with fixture supplier data."""
import asyncio
import json
import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

from sqlalchemy import update
from sqlalchemy.engine import make_url

from app.application.background_index import BackgroundIndex
from app.config import Settings
from app.database.connection import create_database
from app.database.index import IndexRepository
from app.database.models import SupplierSyncState
from app.domain.models import Product, utcnow
from app.domain.research import ListingPage

MARKER=Path('.local/final/restart-marker.json')


class Provider:
    supplier='restart-fixture'
    def __init__(self, child=False): self.child=child;self.requests=[]
    async def research_listing(self, branch):
        return ListingPage(urls=[f'https://fixture.invalid/{i}' for i in range(20)],exhausted=True)
    async def research_products(self,url):
        number=int(url.rsplit('/',1)[-1]);self.requests.append(number)
        if self.child and number==10:
            MARKER.write_text(json.dumps({'committed':10}),encoding='utf-8')
            await asyncio.sleep(3600)
        return [Product(id=f'restart:{number}',supplier=self.supplier,source_url=url,name=f'Карандаш {number}',
            original_price=100,original_currency='KZT',stock_quantity=400)]


async def main():
    settings=Settings(research_job_timeout_seconds=3600)
    # Isolated benchmark database was created by benchmark_final_index.py.
    url=make_url(settings.database_url).set(database='souvenir_final_benchmark').render_as_string(hide_password=False)
    engine,sessions=create_database(url);index=IndexRepository(sessions,settings)
    provider=Provider('child' in sys.argv);host=BackgroundIndex(index,[provider],settings)
    async def immediate(_job,call,_metric):return await call()
    host.request=immediate
    if provider.child:
        identifier=await index.enqueue(provider.supplier,'TARGETED_RESEARCH',{'query':'restart'})
        job=await index.claim(host.owner,[identifier]);await host.run(job)
    else:
        if MARKER.exists():MARKER.unlink()
        process=subprocess.Popen([sys.executable,__file__,'child'],env=os.environ,creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            async with asyncio.timeout(30):
                while not MARKER.exists():await asyncio.sleep(.1)
            process.terminate();await asyncio.to_thread(process.wait,10)
        finally:
            if process.poll() is None:process.terminate()
        async with sessions() as db,db.begin():
            await db.execute(update(SupplierSyncState).where(SupplierSyncState.supplier==provider.supplier)
                .values(lease_until=utcnow()-timedelta(seconds=1)))
        identifier=await index.enqueue(provider.supplier,'TARGETED_RESEARCH',{'query':'restart'})
        job=await index.claim(host.owner,[identifier]);await host.run(job)
        offers=await index.offers(provider.supplier)
        assert len(offers)==20 and provider.requests[0]==10
        report={'real_process_killed':True,'database':'isolated PostgreSQL','supplier':'synthetic fixture',
                'lease_expiry_accelerated':True,'resume_first_product':provider.requests[0],
                'unique_offers':len(offers),'restarted_at_page_one':False}
        Path('.local/final/restart.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        print(json.dumps(report))
    await engine.dispose()


if __name__=='__main__':asyncio.run(main())
