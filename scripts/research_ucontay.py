"""Bounded, read-only Chromium inspection; supplier snapshots stay in .research/."""
import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright


async def main() -> None:
    target = Path('.research/ucontay')
    target.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(locale='ru-RU')
        responses = []
        page.on('response', lambda response: responses.append({
            'url': response.url, 'status': response.status,
            'type': response.request.resource_type,
        }) if response.request.resource_type in {'xhr', 'fetch', 'document'} else None)
        for name, url in [
            ('home', 'https://ucontay.kz/'),
            ('product', 'https://ucontay.kz/product/termokruzhka-radmir-soft-touch'),
        ]:
            try:
                await page.goto(url, wait_until='domcontentloaded', timeout=45000)
                await page.wait_for_timeout(2500)
                target.joinpath(name + '.html').write_text(await page.content(), encoding='utf-8')
                snapshot = await page.evaluate('''() => ({
                    url: location.href,
                    title: document.title,
                    text: document.body.innerText,
                    forms: Array.from(document.forms).map(f => ({action:f.action,method:f.method,html:f.outerHTML.slice(0,12000)})),
                    scripts: Array.from(document.scripts).map(s => ({src:s.src,type:s.type,text:s.textContent.slice(0,500000)})),
                    links: Array.from(document.querySelectorAll('a[href]')).map(a=>({text:a.textContent.trim(),href:a.href})),
                    globals: Object.keys(window).filter(k => /product|insale|variant/i.test(k))
                })''')
                target.joinpath(name + '.json').write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding='utf-8')
                print(json.dumps({'name':name, 'title':snapshot['title'], 'globals':snapshot['globals'], 'forms':[{k:f[k] for k in ['action','method']} for f in snapshot['forms']]},ensure_ascii=False))
            except Exception as exc:
                print(json.dumps({'name':name, 'error':str(exc)},ensure_ascii=False))
        target.joinpath('network.json').write_text(json.dumps(responses, ensure_ascii=False, indent=2), encoding='utf-8')
        await browser.close()


if __name__ == '__main__':
    asyncio.run(main())
