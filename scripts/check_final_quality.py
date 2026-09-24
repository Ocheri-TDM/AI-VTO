import asyncio
import json
from pathlib import Path

from app.application.index_quality import IndexQualityService
from app.config import Settings
from app.database.connection import create_database
from app.database.index import IndexRepository
from app.application.research_planner import SUPPLIERS


async def main():
    settings=Settings();engine,sessions=create_database(settings.database_url)
    report=await IndexQualityService(IndexRepository(sessions,settings),SUPPLIERS).report()
    Path('.local/final/quality.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    for row in report['suppliers']:
        print(row['supplier'],row['offers_total'],row['fresh'],row['discovery_status'],row['pending_branches'])
    await engine.dispose()


if __name__=='__main__':asyncio.run(main())
