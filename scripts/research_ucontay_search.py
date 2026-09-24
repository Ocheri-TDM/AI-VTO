"""Verify Ucontay's actual search form, pagination and variant identity."""
import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright


async def snapshot(page, name):
    data = await page.evaluate('''() => ({
      url:location.href, title:document.title, text:document.body.innerText,
      products:Array.from(document.querySelectorAll('[data-product-json]')).map(e=>JSON.parse(e.getAttribute('data-product-json'))),
      pagination:Array.from(document.querySelectorAll('a[href]')).filter(a=>/[?&]page=/.test(a.href)).map(a=>({text:a.textContent.trim(),href:a.href,html:a.outerHTML})),
      tables:Array.from(document.querySelectorAll('.var-status table')).map(e=>e.outerHTML),
      stock:Array.from(document.querySelectorAll('.js-product-card-quantity')).map(e=>e.textContent),
      selected:Array.from(document.querySelectorAll('select[name="variant_id"]')).map(e=>e.value)
    })''')
    Path('.research/ucontay/'+name+'.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'name':name,'url':data['url'],'products':len(data['products']),'pagination':data['pagination'][:4],'stock':data['stock'],'selected':data['selected']},ensure_ascii=False))
    return data


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(locale='ru-RU')
        async def save_response(response):
            if response.request.resource_type == 'document' and response.url.startswith('https://ucontay.kz/'):
                name = 'search-raw' if '/collection/' in response.url else 'variant-raw' if '/product/' in response.url else 'home-raw'
                Path('.research/ucontay/'+name+'.html').write_text(await response.text(), encoding='utf-8')
        page.on('response', save_response)
        await page.goto('https://ucontay.kz/',wait_until='domcontentloaded')
        await page.locator('form.header__search-form input[name="q"]').fill('термокружка')
        await page.locator('.js-location-checkbox').nth(1).check()
        async with page.expect_navigation(wait_until='domcontentloaded'):
            await page.locator('form.header__search-form button[type="submit"]').click()
        data = await snapshot(page,'search')
        if data['pagination']:
            next_url = data['pagination'][0]['href']
            await page.goto(next_url,wait_until='domcontentloaded')
            await snapshot(page,'page2')
        await page.goto('https://ucontay.kz/product/termokruzhka-radmir-soft-touch?variant_id=710296740',wait_until='domcontentloaded')
        await page.wait_for_timeout(1500)
        await snapshot(page,'variant')
        await browser.close()


if __name__ == '__main__':
    asyncio.run(main())
