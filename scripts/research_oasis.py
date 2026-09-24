"""Bounded, read-only Playwright research of Oasis public pages.

Run from the repository root: .venv/Scripts/python scripts/research_oasis.py
Outputs public page evidence to .local/research/oasis (never credentials/cookies).
"""
import argparse
import asyncio
import json
from pathlib import Path
from urllib.parse import urlsplit

from playwright.async_api import async_playwright


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="https://www.oasiscatalog.com/")
    parser.add_argument("--label", default="home")
    parser.add_argument("--search")
    args = parser.parse_args()
    output = Path(".local/research/oasis")
    output.mkdir(parents=True, exist_ok=True)
    network = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(locale="ru-RU")
        page = await context.new_page()
        async def capture(response):
            if urlsplit(response.url).hostname == "www.oasiscatalog.com" and response.request.resource_type in {"xhr", "fetch"}:
                record = {"url": response.url, "status": response.status}
                if "json" in response.headers.get("content-type", ""):
                    try:
                        record["body"] = await response.json()
                    except Exception:
                        pass
                network.append(record)
        page.on("response", capture)
        await page.goto(args.url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(3000)
        if args.search:
            input_box = page.locator('form[action="/srch"] input[name="q"]').first
            await input_box.fill(args.search)
            await input_box.press("Enter")
            await page.wait_for_timeout(5000)
        result = await page.evaluate("""() => ({
            url: location.href, title: document.title,
            inputs: [...document.querySelectorAll('input')].map(x => ({outer: x.outerHTML, form: x.closest('form')?.outerHTML.slice(0,2000)})),
            scripts: [...document.scripts].map(x => ({src: x.src, id: x.id, type:x.type, text: x.src ? '' : x.textContent})),
            links: [...document.querySelectorAll('a[href]')].map(x => ({href:x.href,text:x.innerText,classes:x.className})),
            windowKeys: Object.keys(window).filter(x => /data|state|nuxt|store/i.test(x)),
        })""")
        result["network"] = network
        (output / f"{args.label}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        (output / f"{args.label}.html").write_text(await page.content(), encoding="utf-8")
        (output / f"{args.label}.txt").write_text(await page.locator("body").inner_text(), encoding="utf-8")
        print(json.dumps({"url":page.url,"title":await page.title(),"network": [{"url": x["url"],"status":x["status"]} for x in network],"scripts": [{"src":x["src"],"id":x["id"],"type":x["type"],"length":len(x["text"])} for x in result["scripts"]]}, ensure_ascii=False, indent=2))
        await context.close()
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
