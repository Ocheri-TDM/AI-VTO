"""Synthetic load in a dedicated PostgreSQL database, never the user's catalog."""
import asyncio
import json
import os
import statistics
import subprocess
import sys
import time
import tracemalloc
from datetime import timedelta
from pathlib import Path

import asyncpg
from sqlalchemy import insert, text, update
from sqlalchemy.engine import make_url

from app.api.research import response, page_products
from app.application.index_query import IndexQueryService
from app.application.research_analysis import current_products, SimilarityService
from app.application.research_planner import ResearchPlanner
from app.config import Settings
from app.database.connection import create_database
from app.database.conversations import ConversationRepository
from app.database.index import IndexRepository
from app.database.models import IndexedProduct, ProductObservation, SupplierOffer
from app.database.research import ResearchRepository
from app.domain.models import Product, utcnow
from app.domain.research import ResearchSession


async def main():
    original = Settings()
    url = make_url(original.database_url).set(database='souvenir_final_large_index')
    admin = await asyncpg.connect(make_url(original.database_url).set(drivername='postgresql',database='postgres').render_as_string(hide_password=False))
    if not await admin.fetchval("SELECT 1 FROM pg_database WHERE datname='souvenir_final_large_index'"):
        await admin.execute('CREATE DATABASE souvenir_final_large_index')
    await admin.close()
    env = {**os.environ,'DATABASE_URL':url.render_as_string(hide_password=False),'PYTHONPATH':'apps/api'}
    completed = await asyncio.to_thread(subprocess.run,[sys.executable,'-m','alembic','-c','apps/api/alembic.ini','upgrade','head'],env=env,capture_output=True,text=True)
    if completed.returncode:
        raise RuntimeError(completed.stderr)
    settings = original.model_copy(update={'database_url':url.render_as_string(hide_password=False)})
    engine,sessions = create_database(settings.database_url)
    index = IndexRepository(sessions,settings)
    async with engine.begin() as conn:
        count = (await conn.execute(text('SELECT count(*) FROM supplier_offers'))).scalar_one()
        if not count:
            for start in range(0,10000,500):
                await conn.execute(insert(IndexedProduct),[dict(id=f'load:{i}',name=f'Карандаш Model{i}' if i%2 else f'Бутылка Model{i}',
                    category='pencil' if i%2 else 'bottle',search_document=f'карандаш деревянный model{i}' if i%2 else f'бутылка металл model{i}',metadata_payload={}) for i in range(start,start+500)])
            for start in range(0,25000,500):
                offers,observations = [],[]
                for i in range(start,start+500):
                    p = Product(id=f'load:{i}',supplier='gifts',source_url=f'https://gifts.ru/id/{i}',
                        name=f'Карандаш Model{i%10000}' if i%2 else f'Бутылка Model{i%10000}',
                        category='pencil' if i%2 else 'bottle',material='дерево' if i%2 else 'металл',
                        price_kzt=1000+i%9000,original_price=1000+i%9000,original_currency='KZT',stock_quantity=500)
                    payload=p.model_dump(mode='json')
                    offers.append(dict(id=p.id,product_id=f'load:{i%10000}',supplier='gifts',source_url=p.source_url,payload=payload))
                    observations.append(dict(offer_id=p.id,price_kzt=p.price_kzt,stock=500,observed_at=utcnow(),expires_at=utcnow()+timedelta(hours=1),status='FRESH',payload=payload))
                await conn.execute(insert(SupplierOffer),offers)
                await conn.execute(insert(ProductObservation),observations)
        else:
            # Reset only this isolated synthetic fixture's clock for a repeat.
            # Live supplier observations are never extended without a real fetch.
            await conn.execute(update(ProductObservation).values(
                observed_at=utcnow(),expires_at=utcnow()+timedelta(hours=1),status='FRESH'))
            await conn.execute(text("UPDATE product_observations SET payload=jsonb_set(payload::jsonb, '{fetched_at}', to_jsonb(CAST(:stamp AS text)))"),{'stamp':utcnow().isoformat()})
    intent = await ResearchPlanner().parse('Карандаши 300 шт')
    samples = []
    for _ in range(8):
        start=time.perf_counter()
        products,_=await IndexQueryService(index).search(intent)
        samples.append((time.perf_counter()-start)*1000)
    chat = await ConversationRepository(sessions).create('Synthetic load benchmark')
    research = ResearchSession(chat_id=chat,intent=intent,products=products,source_mode='index',indexed_offer_ids=[p.id for p in products])
    repo=ResearchRepository(sessions)
    start=time.perf_counter();await repo.save(research);save_ms=(time.perf_counter()-start)*1000
    start=time.perf_counter();dto=response(research);response_ms=(time.perf_counter()-start)*1000
    page=page_products(research,current_products(research),dto['next_cursor'])
    assert len(products)==12500 and dto['matching_count']==12500 and len(page['products'])==50, (len(products),dto['matching_count'],len(page['products']))
    start=time.perf_counter()
    similarity=SimilarityService()
    ranked=sorted(products,key=lambda product:similarity.score(product,[products[0]]),reverse=True)
    similarity_ms=(time.perf_counter()-start)*1000
    assert len(ranked)==12500
    research.view.filters.max_price=2000
    start=time.perf_counter(); filtered=current_products(research); filter_ms=(time.perf_counter()-start)*1000
    assert filtered and all(product.price_kzt<=2000 for product in filtered)
    delta={'event':'research.updated','id':research.id,'matching_count':len(filtered),'version':research.view.version}
    tracemalloc.start()
    await IndexQueryService(index).search(intent)
    _,peak=tracemalloc.get_traced_memory();tracemalloc.stop()
    async with sessions() as db:
        stored=(await db.execute(text('SELECT octet_length(payload::text) FROM research_sessions WHERE id=:id'),{'id':research.id})).scalar_one()
    report={'kind':'SYNTHETIC PostgreSQL isolated database','indexed_products':10000,'offers':25000,
        'fresh_migration':completed.returncode==0,'matching_pool':len(products),'query_ms':samples,
        'p50_ms':statistics.median(samples),'p95_ms':sorted(samples)[-1], 'session_save_ms':save_ms,
        'session_bytes':stored,'first_page_and_facets_ms':response_ms,'first_response_bytes':len(json.dumps(dto)),
        'pagination_checked':True,'browser_invoked':False,'similarity_ms':similarity_ms,
        'filter_ms':filter_ms,'filtered_count':len(filtered),'delta_bytes':len(json.dumps(delta)),
        'python_query_peak_allocated_mb':peak/1024/1024,'memory_measurement':'tracemalloc extra query, not process RSS'}
    Path('.local/final/large-index.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))
    await engine.dispose()


if __name__=='__main__':asyncio.run(main())
