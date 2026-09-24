"""Real six-source research acceptance; never assert completeness from result count."""
import asyncio
import json
import time
from pathlib import Path

from app.application.research import ResearchService
from app.browser import BrowserEngine
from app.config import Settings
from app.database.connection import create_database
from app.database.conversations import ConversationRepository
from app.database.research import ResearchRepository
from app.domain.research import ResearchBudget
from app.providers.oasis import OasisProvider
from app.providers.gifts import GiftsProvider
from app.providers.ucontay import UcontayProvider
from app.providers.portobello import PortobelloProvider
from app.providers.happygifts import HappyGiftsProvider
from app.providers.artegifts import ArteGiftsProvider


async def main():
    settings=Settings()
    engine,sessions=create_database(settings.database_url)
    browser=BrowserEngine(settings)
    providers=[cls(browser,settings) for cls in (OasisProvider,UcontayProvider,GiftsProvider,
                                                PortobelloProvider,HappyGiftsProvider,ArteGiftsProvider)]
    service=ResearchService(ResearchRepository(sessions),providers,settings)
    conversations=ConversationRepository(sessions)
    folder=Path('.local/research/acceptance');folder.mkdir(parents=True,exist_ok=True)
    try:
        for label,query in [('bottles','Синие бутылки, 300 шт.'),
                            ('conference','Синий мерч для IT конференции, 300 человек')]:
            chat=await conversations.create('Research acceptance '+label)
            started=time.monotonic()
            value=await service.start(chat,query,ResearchBudget(max_seconds=180,max_query_expansions=1))
            print('START',label,value.id,flush=True)
            while not service.tasks[value.id].done():
                await asyncio.sleep(10)
                print('PROGRESS',label,len(value.products),sum(b.products_seen for b in value.coverage),flush=True)
            await service.tasks[value.id]
            result=await service.get(value.id)
            report={'research_id':result.id,'chat_id':chat,'query':query,'duration_seconds':round(time.monotonic()-started,1),
                    'pool_count':len(result.products),'status':result.job_status,
                    'coverage':[b.model_dump(mode='json',exclude={'cursor'}) for b in result.coverage]}
            (folder/f'{label}.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            print('DONE',label,report['pool_count'],report['status'],flush=True)
    finally:
        await service.close();await browser.close();await engine.dispose()


if __name__=='__main__':asyncio.run(main())
