"""Observe public catalog requests made by the site's own UI."""
import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright


async def main():
    folder = Path('.local/research/six/portobello')
    calls = []
    pending = set()
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page(locale='ru-RU')
        async def capture(response):
            if 'api.portobello.ru/graphql' in response.url:
                try:
                    calls.append({'request': response.request.post_data, 'response': await response.json()})
                except Exception:
                    pass
        def schedule(response):
            task = asyncio.create_task(capture(response))
            pending.add(task)
            task.add_done_callback(pending.discard)
        page.on('response', schedule)
        await page.goto('https://portobello.ru/catalog/termoproduktsiya-i-butylki-dlya-vody', wait_until='domcontentloaded')
        await page.wait_for_timeout(8000)
        (folder/'bottles.html').write_text(await page.content(), encoding='utf-8')
        (folder/'bottles.txt').write_text(await page.inner_text('body'), encoding='utf-8')
        links = await page.locator('a[href]').evaluate_all('(xs)=>xs.map(x=>({href:x.href,text:x.innerText}))')
        (folder/'bottles-links.json').write_text(json.dumps(links, ensure_ascii=False, indent=2), encoding='utf-8')
        if pending:
            await asyncio.gather(*pending)
        (folder/'graphql.json').write_text(json.dumps(calls, ensure_ascii=False, indent=2), encoding='utf-8')
        print('GraphQL calls:', len(calls))
        for item in calls:
            print(str(item['request'])[:260], list(item['response'].get('data', {})))
        await browser.close()


if __name__ == '__main__':
    asyncio.run(main())
