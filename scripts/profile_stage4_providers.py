"""Measure observed listing/detail latency and browser reuse, no invented batch APIs."""
import asyncio
import json
import time
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
    settings=Settings(browser_timeout_ms=15000)
    browser=BrowserEngine(settings)
    report=[]
    try:
        for cls in (GiftsProvider,UcontayProvider,PortobelloProvider,HappyGiftsProvider,ArteGiftsProvider,OasisProvider):
            provider=cls(browser,settings)
            row={'supplier':provider.supplier,'products':0}
            try:
                async with asyncio.timeout(45):
                    branch=ResearchCoverage(supplier=provider.supplier,category='pencil',query='карандаш')
                    start=time.perf_counter()
                    listing=await provider.research_listing(branch)
                    row['search_page_ms']=round((time.perf_counter()-start)*1000,2)
                    if listing.urls:
                        start=time.perf_counter()
                        products=await provider.research_products(listing.urls[0])
                        row['product_page_ms']=round((time.perf_counter()-start)*1000,2)
                        row['products']=len(products)
                    if listing.next_url:
                        branch.cursor.listing_url=listing.next_url
                        branch.cursor.page_number+=1
                        start=time.perf_counter()
                        await provider.research_listing(branch)
                        row['pagination_page_ms']=round((time.perf_counter()-start)*1000,2)
            except Exception as exc:
                row['error']=getattr(exc,'code',type(exc).__name__)
            row.update(browser.metrics.get(provider.supplier,{}))
            report.append(row)
            print(row,flush=True)
    finally:await browser.close()
    Path('.local/stage4/provider-profile.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':asyncio.run(main())
