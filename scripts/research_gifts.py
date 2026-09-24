"""Read-only inspection; outputs public DOM evidence, never account responses."""
import argparse
import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="https://gifts.ru/")
    parser.add_argument("--label", default="home")
    parser.add_argument("--search")
    args = parser.parse_args()
    output = Path(".local/research/gifts")
    output.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page(locale="ru-RU")
        network = []
        page.on("response", lambda r: network.append({"url": r.url, "status": r.status}) if r.request.resource_type in ("xhr", "fetch") else None)
        response = await page.goto(args.url, wait_until="domcontentloaded", timeout=60000)
        (output / f"{args.label}-response.html").write_text(await response.text(), encoding="utf-8")
        await page.wait_for_timeout(1500)
        if args.search:
            await page.locator('#j_search_input').fill(args.search)
            async with page.expect_navigation(wait_until='domcontentloaded'):
                await page.locator('#j_search_input').press('Enter')
            await page.wait_for_timeout(1000)
        data = await page.evaluate("""() => ({url:location.href,title:document.title,
          inputs:[...document.querySelectorAll('input')].map(x=>({html:x.outerHTML,form:x.closest('form')?.outerHTML.slice(0,1200)})),
          links:[...document.querySelectorAll('a[href]')].map(x=>({url:x.href,text:x.innerText,cls:x.className})),
          scripts:[...document.scripts].map(x=>({src:x.src,type:x.type,text:x.src?'':x.textContent})),
          attrs:[...document.querySelectorAll('[data-product],[data-product-id],[data-id]')].slice(0,50).map(x=>x.outerHTML.slice(0,2000))})""")
        data['network'] = network
        data['gallery'] = await page.locator('g-gallery').first.locator('img').evaluate_all("xs => xs.map(x=>({src:x.src,html:x.outerHTML}))")
        data['article_keys'] = await page.evaluate("() => Object.keys(window.Logrus?.ArticlesData || {})")
        (output / f"{args.label}.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        (output / f"{args.label}.html").write_text(await page.content(), encoding="utf-8")
        (output / f"{args.label}.txt").write_text(await page.locator('body').inner_text(), encoding="utf-8")
        print(json.dumps({"status":response.status,"url":page.url,"attrs":data['attrs'][:2],"links":data['links'][-20:]},ensure_ascii=False))
        await browser.close()


if __name__ == '__main__':
    asyncio.run(main())
