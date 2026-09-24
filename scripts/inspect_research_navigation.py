import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        for site, url, selector in [
            ('portobello', 'https://portobello.ru/', 'input[placeholder="Поиск"]'),
            ('happygifts', 'https://happygifts.ru/', '#search-field'),
            ('artegifts', 'https://kz.artegifts.by/', 'input.js-ajax-search'),
        ]:
            page = await browser.new_page(locale='ru-RU')
            try:
                await page.goto(url, wait_until='domcontentloaded', timeout=45000)
                await page.locator(selector).first.fill('бутылка')
                await page.locator(selector).first.press('Enter')
                await page.wait_for_timeout(5000)
                folder = Path('.local/research/six') / site
                (folder/'search.html').write_text(await page.content(), encoding='utf-8')
                print(site, page.url)
                if site == 'portobello':
                    buttons = await page.locator('button').evaluate_all('(xs)=>xs.filter(x=>x.innerText.trim()==="2").map(x=>x.outerHTML)')
                    print('pagination', buttons)
                    if buttons:
                        await page.get_by_role('button', name='2', exact=True).last.click()
                        await page.wait_for_timeout(4000)
                        print('page2', page.url)
                        (folder/'search-page2.html').write_text(await page.content(), encoding='utf-8')
                (folder/'search-url.txt').write_text(page.url, encoding='utf-8')
            except Exception as e:
                print(site, type(e).__name__, str(e)[:180])
            await page.close()
        await browser.close()


if __name__ == '__main__':
    asyncio.run(main())
