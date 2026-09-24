"""Observed navigation/listing/detail contract and latency, never claims full coverage."""
import asyncio
import json
import time
import sys
from pathlib import Path

from app.browser import BrowserEngine
from app.config import Settings
from app.domain.research import ResearchCoverage
from app.providers.artegifts import ArteGiftsProvider
from app.providers.gifts import GiftsProvider
from app.providers.happygifts import HappyGiftsProvider
from app.providers.oasis import OasisProvider
from app.providers.portobello import PortobelloProvider
from app.providers.ucontay import UcontayProvider


async def main():
    settings = Settings(browser_timeout_ms=45000)
    browser = BrowserEngine(settings)
    report = []
    try:
        for cls in (GiftsProvider,UcontayProvider,PortobelloProvider,HappyGiftsProvider,ArteGiftsProvider,OasisProvider):
            provider = cls(browser,settings)
            if len(sys.argv)>1 and provider.supplier not in sys.argv[1:]:
                continue
            row = {'supplier':provider.supplier,'coverage':'PARTIAL'}
            start = time.perf_counter()
            try:
                async with asyncio.timeout(120):
                    roots = await provider.catalog_navigation()
                    row['roots'] = [r.model_dump() for r in roots.routes]
                    row['navigation_ms'] = round((time.perf_counter()-start)*1000,2)
                    for route in roots.routes[:3]:
                        branch = ResearchCoverage(supplier=provider.supplier,category='',query='',route=route.url)
                        branch.cursor.listing_url = route.url
                        try:
                            listing = await provider.research_listing(branch)
                            row['listing'] = listing.model_dump()
                            if listing.urls:
                                products = await provider.research_products(listing.urls[0])
                                row['sample'] = [p.model_dump(mode='json') for p in products]
                                break
                        except Exception as exc:
                            row.setdefault('branch_errors',[]).append({'url':route.url,'error':getattr(exc,'code',type(exc).__name__), 'detail':str(exc)[:800]})
                    if 'sample' not in row:
                        row['contract'] = 'INCOMPLETE'
                    else:
                        row['contract'] = 'OBSERVED'
            except Exception as exc:
                row['error'] = getattr(exc,'code',type(exc).__name__)
                row['coverage'] = 'FAILED'
            row['duration_ms'] = round((time.perf_counter()-start)*1000,2)
            row['browser'] = browser.metrics.get(provider.supplier,{})
            report.append(row)
            filename = 'provider-live' + ('-'+'-'.join(sys.argv[1:]) if len(sys.argv)>1 else '') + '.json'
            Path('.local/final/'+filename).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            print(provider.supplier,row.get('contract'),row.get('error'),flush=True)
    finally:
        await browser.close()


if __name__ == '__main__':
    asyncio.run(main())
