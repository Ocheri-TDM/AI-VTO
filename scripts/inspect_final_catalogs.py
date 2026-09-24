"""Public navigation evidence, including HTTP failures; never bypass protection."""
import asyncio
import json
import time
from pathlib import Path

from playwright.async_api import async_playwright

SITES = {
    'gifts': 'https://gifts.ru/',
    'ucontay': 'https://ucontay.kz/',
    'portobello': 'https://portobello.ru/',
    'happygifts': 'https://happygifts.ru/',
    'artegifts': 'https://kz.artegifts.by/',
    'oasis': 'https://www.oasiscatalog.com/',
}


async def main():
    folder = Path('.local/final/catalogs')
    folder.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        for supplier, url in SITES.items():
            page = await browser.new_page(locale='ru-RU')
            row = {'supplier': supplier, 'requested_url': url}
            started = time.perf_counter()
            try:
                response = await page.goto(url, wait_until='domcontentloaded', timeout=45000)
                row.update(status=response.status, final_url=page.url,
                           content_type=response.headers.get('content-type'), title=await page.title())
                row['links'] = await page.locator('a[href]').evaluate_all(
                    'xs=>xs.map(x=>({url:x.href,label:x.textContent.trim().slice(0,150)}))')
                (folder / (supplier + '.html')).write_text(await page.content(), encoding='utf-8')
            except Exception as exc:
                row['error'] = str(exc)[:500]
            row['duration_ms'] = round((time.perf_counter()-started)*1000, 2)
            (folder / (supplier + '.json')).write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding='utf-8')
            print(supplier, row.get('status'), row.get('title'), len(row.get('links', [])), flush=True)
            await page.close()
        await browser.close()


if __name__ == '__main__':
    asyncio.run(main())
