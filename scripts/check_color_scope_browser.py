"""Installed Edge, main HTTP UI, exact disposable owner from color-scope ledger."""

import asyncio
import json
import shutil
import subprocess

import httpx
import psutil
from check_color_scope import LEDGER, PUBLIC, QUERY, ROOT, save
from check_palette_browser import Browser


async def main():
    state = json.loads(LEDGER.read_text("utf-8"))
    folder = (ROOT / ".local" / ("color-scope-edge-" + state["run"])).resolve()
    assert folder.parent == (ROOT / ".local").resolve() and not folder.exists()
    assert not any(
        c.status == "LISTEN" and c.laddr.port == 9226
        for c in psutil.net_connections(kind="tcp")
        if c.laddr
    )
    folder.mkdir()
    edge = b = None
    results = []
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:8000",
        cookies=state["users"][0]["cookies"],
        timeout=180,
    ) as c:
        projects = (await c.get("/api/projects")).json()
        project = next(p for p in projects if p["id"] == state["browser_project"])
        before = (await c.get(f"/api/projects/{project['id']}/collection")).json()
        current = (await c.get("/api/researches/chat/" + project["chat_id"])).json()
        reset = await c.patch(
            f"/api/researches/{current['id']}/view",
            json={
                "version": current["view"]["version"],
                "restore": "reset",
                "branch_actions": [{"action": "show_all"}],
            },
        )
        reset.raise_for_status()
        fresh = await c.post(
            f"/api/projects/{project['id']}/requests", json={"query": QUERY}
        )
        fresh.raise_for_status()
        history_query = fresh.json()["query"]["id"]
        fresh = await c.post(
            f"/api/projects/{project['id']}/requests", json={"query": QUERY}
        )
        fresh.raise_for_status()
        try:
            with (folder / "edge.log").open("ab") as log:
                edge = await asyncio.to_thread(
                    subprocess.Popen,
                    [
                        "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
                        "--headless=new",
                        "--disable-extensions",
                        "--remote-debugging-port=9226",
                        "--user-data-dir=" + str(folder / "profile"),
                        "--no-first-run",
                        "--no-default-browser-check",
                        "about:blank",
                    ],
                    stdout=log,
                    stderr=log,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            async with httpx.AsyncClient() as probe:
                for _ in range(80):
                    try:
                        if (
                            await probe.get("http://127.0.0.1:9226/json/list")
                        ).status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    await asyncio.sleep(0.1)
            b = Browser()
            await b.connect(state["users"][0]["cookies"])
            helper = "window.dc=(label)=>[...document.querySelectorAll('.ui-select')].find(e=>e.querySelector(':scope > label')?.textContent===label)?.querySelector(':scope > button');"
            await b.call(
                "Page.addScriptToEvaluateOnNewDocument",
                {
                    "source": "if(location.origin==='http://127.0.0.1:3000')localStorage.setItem('forma-chat-id',"
                    + json.dumps(project["chat_id"])
                    + ");"
                    + helper
                },
            )

            async def check(label, expression):
                try:
                    await b.wait(expression)
                except Exception:
                    await b.shot(PUBLIC / "browser-failure.png")
                    raise
                results.append({"check": label, "status": "PASS"})
                print("PASS", label, flush=True)

            async def shot(name):
                await b.js("document.fonts.ready.then(()=>true)")
                await asyncio.sleep(0.3)
                await b.shot(PUBLIC / (name + ".png"))

            async def submit(query):
                await b.js(
                    "document.querySelector('.research-composer textarea').focus();document.querySelector('.research-composer textarea').select()"
                )
                await b.call("Input.insertText", {"text": query})
                for kind in ["keyDown", "keyUp"]:
                    await b.call(
                        "Input.dispatchKeyEvent",
                        {
                            "type": kind,
                            "key": "Enter",
                            "code": "Enter",
                            "windowsVirtualKeyCode": 13,
                        },
                    )
                await b.wait(
                    "document.querySelector('.research-composer textarea')?.value==='' && !document.querySelector('[role=alert]') && document.querySelector('.research-composer button')?.textContent!=='Обрабатываю…'"
                )

            async def key(value):
                for kind in ["keyDown", "keyUp"]:
                    await b.call(
                        "Input.dispatchKeyEvent",
                        {
                            "type": kind,
                            "key": value,
                            "code": value,
                            "windowsVirtualKeyCode": 27 if value == "Escape" else 13,
                        },
                    )

            await b.call("Page.navigate", {"url": "http://127.0.0.1:3000/"})
            await check(
                "Source color in actual dropdown",
                "dc('Цвет')?.textContent==='Тёмно-синий'",
            )
            await check(
                "All four branch constraints visible",
                "document.querySelectorAll('.query-category-chip').length===4 && [...document.querySelectorAll('.query-category-caption small')].filter(e=>e.textContent.includes('Тёмно-синий')&&e.textContent.includes('250')).length===4",
            )
            await check(
                "No residual model and consistent shopper label",
                "!document.querySelector('.research-main').textContent.includes('Модель: Темно') && !document.querySelector('.research-main h1').textContent.includes('Темно') && [...document.querySelectorAll('[aria-label=\"Активные условия\"] span')].some(e=>e.textContent.startsWith('Сумки-шопперы:'))",
            )
            accepted = json.loads((PUBLIC / "after.json").read_text("utf-8"))["cases"][
                "A_fresh"
            ]
            expected = accepted["offer_ids"]
            after_hide = len(
                set().union(
                    *(set(ids) for ids in accepted["persisted"]["branch_offer_ids"][1:])
                )
            )
            actual = await b.js(
                "[...document.querySelectorAll('.research-main [data-offer-id]')].map(e=>e.dataset.offerId)"
            )
            assert set(actual) == set(expected), (actual, expected)
            results.append(
                {
                    "check": "Rendered exact SupplierOffer IDs",
                    "status": "PASS",
                    "offer_ids": actual,
                }
            )
            await check(
                "All rendered variants show requested shade",
                "[...document.querySelectorAll('.research-main .product-color')].every(e=>e.textContent==='Тёмно-синий')",
            )
            await shot("hud-current")
            await b.js("dc('Цвет').click()")
            await check(
                "Source option selected",
                "document.querySelector('.ui-select-popup:popover-open [aria-selected=true]')?.textContent.includes('Тёмно-синий')",
            )
            await shot("color-dropdown")
            await key("Escape")
            await b.js("dc('Тема интерфейса').click()")
            await b.wait(
                "!!document.querySelector('.theme-switch .ui-select-popup:popover-open')"
            )
            await b.js(
                "document.querySelectorAll('.theme-switch [role=option]')[1].click()"
            )
            await shot("hud-pulse")
            await b.js(
                "document.querySelector('.query-category-chip .query-category-action').click()"
            )
            await check(
                "Hide keeps common shade",
                "!!document.querySelector('.query-category-chip.is-hidden') && dc('Цвет').textContent==='Тёмно-синий'",
            )
            await check(
                "Hide saved before reload",
                f"document.querySelectorAll('.research-main .product-card').length==={after_hide} && !document.querySelector('.query-category-action:disabled')",
            )
            await b.call("Page.reload")
            await check(
                "F5 restores hidden branch and collection",
                "!!document.querySelector('.query-category-chip.is-hidden') && dc('Цвет')?.textContent==='Тёмно-синий' && document.querySelector('.research-selected-count')?.textContent==='1'",
            )
            await b.click("Показать все")
            await check(
                "Show restores four branches",
                "!document.querySelector('.query-category-chip.is-hidden')",
            )
            await b.js(
                "document.querySelectorAll('.research-inline-filters input[type=number]')[0].focus()"
            )
            await b.call("Input.insertText", {"text": "999999999"})
            await check(
                "Empty pool retains source color",
                "!!document.querySelector('.research-empty') && dc('Цвет').textContent==='Тёмно-синий'",
            )
            await b.js("dc('Цвет').click()")
            await check(
                "Zero facet keeps selected shade",
                "[...document.querySelectorAll('.ui-select-popup:popover-open [role=option]')].some(e=>e.textContent.includes('Тёмно-синий')&&e.querySelector('.ui-select-count')?.textContent==='0')",
            )
            await shot("zero-results")
            await key("Escape")
            await b.click("Сбросить фильтры")
            await check(
                "Reset preserves query shade",
                "!!document.querySelector('.research-main .product-card') && dc('Цвет').textContent==='Тёмно-синий'",
            )
            await submit("теперь красные")
            await check(
                "Chat replacement updates all four branches",
                "dc('Цвет')?.textContent==='Красный' && [...document.querySelectorAll('.query-category-caption small')].filter(e=>e.textContent.includes('Красный')).length===4",
            )
            await shot("red-refinement")
            await submit("Красные ручки и синие термокружки")
            await check(
                "Mixed category colors are explicit",
                "dc('Цвет')?.textContent==='Разные по категориям' && document.querySelectorAll('.query-category-chip').length===2",
            )
            await shot("mixed-colors")
            await b.click("История")
            await b.wait("!!document.querySelector('.workspace-history')")
            await b.js(
                "(async()=>{const queries=await fetch('/api/projects/"
                + project["id"]
                + "/queries').then(r=>r.json());const index=queries.findIndex(q=>q.id==="
                + json.dumps(history_query)
                + ");if(index<0)throw Error('Missing original history entry');document.querySelectorAll('.workspace-history button')[index].click()})()"
            )
            await check(
                "History restores corrected source plan",
                "!document.querySelector('.workspace-history') && dc('Цвет')?.textContent==='Тёмно-синий' && document.querySelectorAll('.query-category-chip').length===4",
            )
            await submit("цвет не важен")
            await check(
                "Explicit removal is reflected in HUD",
                "dc('Цвет')?.textContent==='Все'",
            )
            after = (await c.get(f"/api/projects/{project['id']}/collection")).json()
            assert before == after
            results.append(
                {
                    "check": "Project collection unchanged",
                    "status": "PASS",
                    "offer_ids": after["selected_ids"],
                }
            )
            await submit(QUERY)
            await check(
                "Final standalone request restores exact requirement",
                "dc('Цвет')?.textContent==='Тёмно-синий' && document.querySelectorAll('.query-category-chip').length===4",
            )
            save(
                PUBLIC / "browser.json",
                {
                    "status": "PASS",
                    "browser": "Installed Microsoft Edge",
                    "checks": results,
                },
            )
        finally:
            if b:
                await b.ws.close()
            if edge:
                try:
                    parent = psutil.Process(edge.pid)
                    owned = parent.children(recursive=True) + [parent]
                    for p in owned:
                        try:
                            p.terminate()
                        except psutil.NoSuchProcess:
                            pass
                    _, alive = psutil.wait_procs(owned, timeout=5)
                    assert not alive
                except psutil.NoSuchProcess:
                    pass
            assert folder.parent == (ROOT / ".local").resolve()
            shutil.rmtree(folder)
    print("Browser PASS", len(results), "checks")


if __name__ == "__main__":
    asyncio.run(main())
