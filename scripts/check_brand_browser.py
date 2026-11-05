"""Main frontend acceptance with installed Edge and an isolated CDP profile."""

import asyncio
import json
from pathlib import Path

from check_palette_browser import Browser

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / ".local/brand-search-acceptance.json"


async def main():
    state = json.loads(LEDGER.read_text("utf-8"))
    b = Browser()
    await b.connect(state["users"][0]["cookies"])
    results = []

    async def check(label, expression):
        await b.wait(expression)
        results.append({"check": label, "passed": True})

    async def submit(query):
        await b.js(
            "document.querySelector('.research-composer textarea').focus();document.querySelector('.research-composer textarea').select()"
        )
        await b.call("Input.insertText", {"text": query})
        await b.call(
            "Input.dispatchKeyEvent",
            {
                "type": "keyDown",
                "key": "Enter",
                "code": "Enter",
                "windowsVirtualKeyCode": 13,
            },
        )
        await b.call(
            "Input.dispatchKeyEvent",
            {
                "type": "keyUp",
                "key": "Enter",
                "code": "Enter",
                "windowsVirtualKeyCode": 13,
            },
        )
        await b.wait(
            "document.querySelector('.research-composer button').textContent!=='Обрабатываю…' && document.querySelector('.research-composer textarea').value==='' && !document.querySelector('[role=alert]')"
        )

    try:
        await b.call("Page.navigate", {"url": "http://127.0.0.1:3000/"})
        await b.wait("document.querySelector('.research-composer textarea')")
        projects = await b.js("fetch('/api/projects').then(r=>r.json())")
        state["browser_projects"] = [
            p["id"] for p in projects if p["id"] not in state["projects"]
        ]
        LEDGER.write_text(json.dumps(state, ensure_ascii=False, indent=2), "utf-8")
        project = next(p for p in projects if p["id"] == state["projects"][0])
        await b.js(
            f"localStorage.setItem('forma-chat-id',{json.dumps(project['chat_id'])})"
        )
        await b.call("Page.reload", {"ignoreCache": True})
        await b.wait(
            "document.querySelector('.research-main .product-card') && document.querySelector('.research-composer textarea')"
        )
        await submit("Ручка шариковая Tippo")
        await check(
            "Tippo exact variants and visible model",
            "document.querySelectorAll('.research-main .product-card').length===6 && [...document.querySelectorAll('.research-main .product-name')].every(e=>e.textContent.includes('Tippo')) && document.querySelector('[aria-label=\"Активные условия\"]').textContent.includes('Модель: Tippo')",
        )
        await b.shot(ROOT / ".local/brand-browser-tippo.png")
        await submit("Molti")
        await check(
            "Brand standalone and locked source checkbox",
            "document.querySelectorAll('.research-main .product-card').length===64 && [...document.querySelectorAll('.research-brand-options label')].some(e=>e.textContent.includes('Molti')&&e.querySelector('input').checked&&e.querySelector('input').disabled)",
        )
        await submit("Оставь только чёрные")
        await b.wait(
            "[...document.querySelectorAll('button')].some(b=>b.textContent==='Сбросить фильтры')"
        )
        await b.click("Сбросить фильтры")
        await check(
            "Reset keeps source brand",
            "document.querySelectorAll('.research-main .product-card').length===64 && document.querySelector('[aria-label=\"Активные условия\"]').textContent.includes('Бренд: Molti')",
        )
        await b.call("Page.reload", {"ignoreCache": True})
        await check(
            "F5 restores effective brand and collection",
            "document.querySelectorAll('.research-main .product-card').length===64 && document.querySelector('.research-selected-count').textContent==='1'",
        )
        await submit("ручки")
        await b.js(
            "document.querySelector('.research-brand-select').open=true;[...document.querySelectorAll('.research-brand-options label')].find(e=>/^B1\\s*·/.test(e.textContent.trim())).querySelector('input').click()"
        )
        await check(
            "Actual HUD B1 selection matches HTTP count",
            "document.querySelectorAll('.research-main .product-card').length===7 && !document.querySelector('[role=status]')",
        )
        await submit("Рюкзак Melango")
        await check(
            "Other family model remains mandatory",
            "document.querySelectorAll('.research-main .product-card').length===4 && [...document.querySelectorAll('.research-main .product-name')].every(e=>e.textContent.includes('Melango'))",
        )
        await submit("ручки модель UnknownModel729")
        await check(
            "Unknown model explained without fallback",
            "document.querySelectorAll('.research-main .product-card').length===0 && document.body.textContent.includes('Нет вариантов модели')",
        )
        await b.click("История")
        await b.wait("document.querySelector('.workspace-history button')")
        await b.js(
            "[...document.querySelectorAll('.workspace-history button')].find(e=>e.querySelector('strong').textContent==='Рюкзак Melango').click()"
        )
        await check(
            "History restores model and variants",
            "!document.querySelector('.workspace-history') && document.querySelectorAll('.research-main .product-card').length===4 && document.querySelector('[aria-label=\"Активные условия\"]').textContent.includes('Модель: Melango')",
        )
        await submit("ROLY")
        await check(
            "Full pool first page",
            "document.querySelectorAll('.research-main .product-card').length===100 && document.body.textContent.includes('всего 344')",
        )
        for expected in (200, 300, 344):
            await b.js(
                "[...document.querySelectorAll('button')].find(e=>e.textContent.startsWith('Показать ещё 100')).click()"
            )
            await b.wait(
                f"document.querySelectorAll('.research-main .product-card').length==={expected}"
            )
        await check(
            "All 344 variants reachable",
            "document.querySelectorAll('.research-main .product-card').length===344 && ![...document.querySelectorAll('button')].some(e=>e.textContent.startsWith('Показать ещё 100'))",
        )
        await submit("Ручка шариковая Tippo")
        await b.call(
            "Emulation.setDeviceMetricsOverride",
            {"width": 390, "height": 844, "deviceScaleFactor": 1, "mobile": True},
        )
        await check(
            "Mobile model and collection preserved",
            "document.querySelectorAll('.research-main .product-card').length===6 && document.documentElement.scrollWidth<=390",
        )
        await b.shot(ROOT / ".local/brand-browser-mobile.png")
        state["browser_checks"] = results
        LEDGER.write_text(json.dumps(state, ensure_ascii=False, indent=2), "utf-8")
        print(f"Installed Edge: {len(results)} DOM checks passed")
    finally:
        await b.ws.close()


if __name__ == "__main__":
    asyncio.run(main())
