import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        context = await browser.new_context(java_script_enabled=False)
        await context.route('**/*', lambda route: route.abort())
        for site in ['artegifts', 'happygifts', 'portobello']:
            page = await context.new_page()
            await page.set_content((Path('.local/research/six') / site / 'product.html').read_text(encoding='utf-8'))
            data = await page.evaluate('''() => ({
                fields: [...document.querySelectorAll('*')].filter(x=>x.children.length===0 && ['Материал','Материал товара','Цвет','Объем','Размер','Бренд'].includes(x.textContent.trim())).map(x=>x.parentElement.outerHTML.slice(0,2200)),
                images: [...document.querySelectorAll('img')].filter(x=>x.src.includes('upload') || x.src.includes('portobello-catalog')).slice(0,8).map(x=>x.outerHTML),
                variants: [...document.querySelectorAll('a[href]')].filter(x=>x.href.includes('active_sku_id') || x.href.includes('/color_')).slice(0,12).map(x=>x.outerHTML.slice(0,800))
            })''')
            (Path('.local/research/six') / site / 'dom-details.json').write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
            print(site, json.dumps(data, ensure_ascii=False)[:8000])
            await page.close()
        await browser.close()


if __name__ == '__main__':
    asyncio.run(main())
