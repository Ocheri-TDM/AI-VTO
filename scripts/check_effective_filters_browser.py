"""Installed Edge acceptance of real controls, real requests and persisted state."""

import asyncio
import json
import shutil
import subprocess

import httpx
import psutil
from check_effective_filters import LEDGER, MIXED, PUBLIC, QUERY, ROOT, save
from check_palette_browser import Browser


async def main():
    state = json.loads(LEDGER.read_text("utf-8"))
    folder = (ROOT / ".local" / ("effective-edge-" + state["run"])).resolve()
    assert folder.parent == (ROOT / ".local").resolve() and not folder.exists()
    assert not any(
        c.status == "LISTEN" and c.laddr.port == 9226
        for c in psutil.net_connections("tcp")
    )
    folder.mkdir()
    edge = b = None
    checks = []
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:8000",
        cookies=state["users"][0]["cookies"],
        timeout=180,
    ) as c:
        project = next(
            p
            for p in (await c.get("/api/projects")).json()
            if p["id"] == state["browser_project"]
        )
        collection_path = f"/api/projects/{project['id']}/collection"
        collection = (await c.get(collection_path)).json()
        response = await c.post(
            f"/api/projects/{project['id']}/requests", json={"query": QUERY}
        )
        response.raise_for_status()
        history = response.json()["query"]["id"]
        response = await c.post(
            f"/api/projects/{project['id']}/requests", json={"query": QUERY}
        )
        response.raise_for_status()
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
            # CDP command calls already have a bounded timeout. Don't let the
            # WebSocket transport's shorter idle ping timeout abort a deliberate
            # quota cooldown on Windows.
            b.ws.ping_timeout = None
            helper = "window.dc=(label)=>[...document.querySelectorAll('.research-desktop-filters .ui-select')].find(e=>e.querySelector(':scope > label')?.textContent===label);window.ni=(label)=>[...document.querySelectorAll('.research-desktop-filters label')].find(e=>e.firstChild?.textContent===label)?.querySelector('input');"
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
                checks.append({"check": label, "status": "PASS"})
                print("PASS", label, flush=True)

            async def shot(name):
                await b.js("document.fonts.ready.then(()=>true)")
                await asyncio.sleep(0.2)
                await b.shot(PUBLIC / (name + ".png"))

            async def choose(label, option):
                await b.js(
                    "dc("
                    + json.dumps(label)
                    + ").querySelector(':scope > button').click()"
                )
                await b.wait(
                    "!!document.querySelector('.ui-select-popup:popover-open')"
                )
                await b.js(
                    "[...document.querySelectorAll('.ui-select-popup:popover-open [role=option]')].find(e=>e.querySelector('.ui-select-option-text')?.textContent==="
                    + json.dumps(option)
                    + ").click()"
                )

            async def number(label, value):
                await b.js(
                    "ni("
                    + json.dumps(label)
                    + ").focus();ni("
                    + json.dumps(label)
                    + ").select()"
                )
                for kind in ["keyDown", "keyUp"]:
                    await b.call(
                        "Input.dispatchKeyEvent",
                        {
                            "type": kind,
                            "key": "a",
                            "code": "KeyA",
                            "windowsVirtualKeyCode": 65,
                            "modifiers": 2,
                        },
                    )
                if value:
                    await b.call("Input.insertText", {"text": value})
                else:
                    await b.call(
                        "Input.dispatchKeyEvent",
                        {
                            "type": "keyDown",
                            "key": "Backspace",
                            "code": "Backspace",
                            "windowsVirtualKeyCode": 8,
                        },
                    )
                    await b.call(
                        "Input.dispatchKeyEvent",
                        {
                            "type": "keyUp",
                            "key": "Backspace",
                            "code": "Backspace",
                            "windowsVirtualKeyCode": 8,
                        },
                    )
                actual = await b.js(
                    "({value:ni("
                    + json.dumps(label)
                    + ").value,valid:ni("
                    + json.dumps(label)
                    + ").validity.valid,focused:document.activeElement===ni("
                    + json.dumps(label)
                    + ")})"
                )
                assert actual["value"] == value and actual["valid"], (
                    label,
                    value,
                    actual,
                )

            async def saved(field, mode, value=None, branch=None):
                # Human-paced polling stays inside the unchanged user quota.
                await asyncio.sleep(2)
                path = "/api/researches/chat/" + project["chat_id"]
                projection = (
                    "r.effective_filters.branches.find(b=>b.id==="
                    + json.dumps(branch)
                    + ").fields"
                    if branch
                    else "r.effective_filters.fields"
                )
                expression = (
                    "(async()=>{await new Promise(resolve=>setTimeout(resolve,1200));let r=await fetch("
                    + json.dumps(path)
                    + ").then(r=>r.json());let f=("
                    + projection
                    + ")["
                    + json.dumps(field)
                    + "];return f?.mode==="
                    + json.dumps(mode)
                )
                if value is not None:
                    expression += (
                        " && JSON.stringify("
                        + ("f.values" if isinstance(value, list) else "f.number")
                        + ")==="
                        + json.dumps(json.dumps(value, separators=(",", ":")))
                    )
                try:
                    await b.wait(expression + ";})()")
                except Exception:
                    await b.shot(PUBLIC / "browser-failure.png")
                    print(
                        json.dumps(
                            await b.js(
                                "({numbers:[...document.querySelectorAll('.research-desktop-filters input[type=number]')].map(e=>e.value),error:document.querySelector('[role=alert]')?.textContent,status:document.querySelector('.research-hud-status')?.textContent})"
                            ),
                            ensure_ascii=True,
                        ),
                        flush=True,
                    )
                    raise
                await b.wait(
                    "!document.querySelector('.research-hud-status [role=status]')"
                )

            async def submit(query):
                await b.wait(
                    "!document.querySelector('.research-hud-status [role=status]') && document.querySelector('.research-composer button')?.textContent!=='Обрабатываю…'"
                )
                await asyncio.sleep(2)
                await b.js(
                    "document.querySelector('.research-composer textarea').focus();document.querySelector('.research-composer textarea').select()"
                )
                await b.call("Input.insertText", {"text": query})
                await b.wait(
                    "!document.querySelector('.research-composer button')?.disabled && document.querySelector('.research-composer textarea')?.value==="
                    + json.dumps(query)
                )
                await b.js(
                    "document.querySelector('.research-composer button').click()"
                )
                await b.wait(
                    "document.querySelector('.research-composer textarea')?.value==='' && document.querySelector('.research-composer button')?.textContent!=='Обрабатываю…' && !document.querySelector('[role=alert]')"
                )

            await b.call("Page.navigate", {"url": "http://127.0.0.1:3000/"})
            await check(
                "Common controls hold actual DarkBlue and numeric250",
                "dc('Цвет')?.querySelector('.ui-select-value')?.textContent==='Тёмно-синий' && ni('Тираж от, шт.')?.value==='250'",
            )
            await check(
                "Four compact names with no conditions or counts",
                "document.querySelectorAll('.query-category-chip').length===4 && [...document.querySelectorAll('.query-category-caption')].every(e=>e.children.length===1&&e.querySelector('strong')&&!e.hasAttribute('title')) && !document.querySelector('.query-category-caption small') && !document.querySelector('.query-category-chip').textContent.includes('250')",
            )
            await check(
                "No duplicate common pills; collection remains separate",
                "!document.querySelector('[aria-label=\"Активные условия\"]') && !!document.querySelector('[data-testid=selected-products]') && !document.querySelector('.effective-branch-filters')",
            )
            expected = json.loads((PUBLIC / "http.json").read_text("utf-8"))["cases"][
                "common"
            ]["offer_ids"]
            actual = await b.js(
                "[...document.querySelectorAll('.research-main [data-offer-id]')].map(e=>e.dataset.offerId)"
            )
            assert set(actual) == set(expected)
            checks.append(
                {
                    "check": "Rendered exact saved SupplierOffer IDs",
                    "status": "PASS",
                    "offer_ids": actual,
                }
            )
            await shot("common")
            await b.js("dc('Цвет').querySelector('button').click()")
            await check(
                "Real selected option and active checkmark",
                "document.querySelector('.ui-select-popup:popover-open [aria-selected=true] .ui-select-option-text')?.textContent==='Тёмно-синий' && !!document.querySelector('.ui-select-popup:popover-open [aria-selected=true] .ui-select-check svg')",
            )
            await shot("common-dropdown")
            await b.call(
                "Input.dispatchKeyEvent",
                {
                    "type": "keyDown",
                    "key": "Escape",
                    "code": "Escape",
                    "windowsVirtualKeyCode": 27,
                },
            )
            await b.call("Page.reload")
            await check(
                "F5 restores common controls and collection",
                "ni('Тираж от, шт.')?.value==='250' && dc('Цвет')?.querySelector('.ui-select-value')?.textContent==='Тёмно-синий' && document.querySelector('.research-selected-count')?.textContent==="
                + json.dumps(str(len(collection["selected_ids"]))),
            )
            await choose("Цвет", "Красный")
            await saved("colors", "common", ["RED"])
            await number("Тираж от, шт.", "500")
            await saved("minimum_quantity", "common", 500)
            await check(
                "Edits replace effective requirements in actual controls",
                "dc('Цвет')?.querySelector('.ui-select-value')?.textContent==='Красный' && ni('Тираж от, шт.')?.value==='500'",
            )
            await choose("Цвет", "Все")
            await saved("colors", "none")
            await number("Тираж от, шт.", "")
            await saved("minimum_quantity", "none")
            await check(
                "Explicit removal leaves genuinely unconstrained fields",
                "ni('Тираж от, шт.')?.value==='' && dc('Цвет')?.querySelector('.ui-select-value')?.textContent==='Все'",
            )
            await b.click("Сбросить фильтры")
            await saved("colors", "common", ["DARK_BLUE"])
            await check(
                "Reset restores source fields", "ni('Тираж от, шт.')?.value==='250'"
            )
            await number("Цена от, ₸", "999999999")
            await saved("min_price", "common", 999999999)
            await check(
                "Zero results keep both controls and all branches",
                "!!document.querySelector('.research-empty') && dc('Цвет')?.querySelector('.ui-select-value')?.textContent==='Тёмно-синий' && ni('Тираж от, шт.')?.value==='250' && document.querySelectorAll('.query-category-chip').length===4",
            )
            await b.js("dc('Цвет').querySelector('button').click()")
            await check(
                "Missing facet retains selected DarkBlue",
                "document.querySelector('.ui-select-popup:popover-open [aria-selected=true] .ui-select-count')?.textContent==='0'",
            )
            await b.call(
                "Input.dispatchKeyEvent",
                {
                    "type": "keyDown",
                    "key": "Escape",
                    "code": "Escape",
                    "windowsVirtualKeyCode": 27,
                },
            )
            await b.click("Сбросить фильтры")
            await saved("min_price", "none")
            await b.js("document.querySelector('.query-category-action').click()")
            await b.wait(
                "document.querySelector('.query-category-chip.is-hidden') && !document.querySelector('.query-category-action:disabled')"
            )
            await b.call("Page.reload")
            await check(
                "F5 preserves hidden branch without losing common projection",
                "!!document.querySelector('.query-category-chip.is-hidden') && ni('Тираж от, шт.')?.value==='250' && dc('Цвет')?.querySelector('.ui-select-value')?.textContent==='Тёмно-синий'",
            )
            await b.click("Показать все")
            await check(
                "Show retains stable four branches",
                "!document.querySelector('.query-category-chip.is-hidden') && document.querySelectorAll('.query-category-chip').length===4",
            )
            print(
                "Common-state phase complete; cooling down inside the unchanged request quota",
                flush=True,
            )
            for _ in range(11):
                await asyncio.sleep(5)
                await b.js("true")
            await submit(MIXED)
            await check(
                "Mixed numeric field is truly empty, dropdown mixed",
                "ni('Тираж от, шт.')?.value==='' && dc('Цвет')?.querySelector('.ui-select-value')?.textContent==='Разные по категориям' && document.querySelector('.effective-filter-control')?.parentElement.textContent.includes('Разный по категориям')",
            )
            await b.js(
                "document.querySelector('.effective-branch-filters summary').click()"
            )
            await check(
                "Editable local real values",
                "ni('Тираж от, шт. · Ручки')?.value==='500' && ni('Тираж от, шт. · Бутылки')?.value==='100' && dc('Цвет · Ручки')?.querySelector('.ui-select-value')?.textContent==='Красный' && dc('Цвет · Бутылки')?.querySelector('.ui-select-value')?.textContent==='Синий'",
            )
            await shot("mixed")
            await number("Тираж от, шт. · Ручки", "501")
            branch = await b.js(
                "[...document.querySelectorAll('.effective-branch-row')].find(e=>e.querySelector('strong')?.textContent==='Ручки').dataset.filterBranch"
            )
            await saved("minimum_quantity", "common", 501, branch)
            await choose("Цвет · Ручки", "Зелёный")
            await saved("colors", "common", ["GREEN"], branch)
            await check(
                "Local edit preserves other branch and mixed global",
                "ni('Тираж от, шт. · Бутылки')?.value==='100' && dc('Цвет · Бутылки')?.querySelector('.ui-select-value')?.textContent==='Синий' && ni('Тираж от, шт.')?.value===''",
            )
            await b.js(
                "[...document.querySelectorAll('.query-category-chip')].find(e=>e.querySelector('strong')?.textContent==='Ручки').querySelectorAll('button')[1].click()"
            )
            await check(
                "Remove makes remaining branch common",
                "document.querySelectorAll('.query-category-chip').length===1 && ni('Тираж от, шт.')?.value==='100' && dc('Цвет')?.querySelector('.ui-select-value')?.textContent==='Синий'",
            )
            await b.click("История")
            await b.wait("!!document.querySelector('.workspace-history')")
            await b.js(
                "(async()=>{const q=await fetch('/api/projects/"
                + project["id"]
                + "/queries').then(r=>r.json());const i=q.findIndex(q=>q.id==="
                + json.dumps(history)
                + ");if(i<0)throw Error('Missing history');document.querySelectorAll('.workspace-history button')[i].click()})()"
            )
            await check(
                "History restores original actual fields",
                "!document.querySelector('.workspace-history') && ni('Тираж от, шт.')?.value==='250' && dc('Цвет')?.querySelector('.ui-select-value')?.textContent==='Тёмно-синий'",
            )
            await b.js(
                "window.__backProject=[...document.querySelectorAll('.research-projects .project-open')].findIndex(e=>e.hasAttribute('aria-current'));document.querySelector('.research-projects .project-item:not(.active) .project-open').click()"
            )
            await check(
                "Other project has an independent empty collection",
                "!document.querySelector('.query-category-chip') && document.querySelector('.research-selected-count')?.textContent==='0'",
            )
            await b.js(
                "document.querySelectorAll('.research-projects .project-open')[window.__backProject].click()"
            )
            await check(
                "Reopening project restores fields and selection",
                "ni('Тираж от, шт.')?.value==='250' && dc('Цвет')?.querySelector('.ui-select-value')?.textContent==='Тёмно-синий' && document.querySelector('.research-selected-count')?.textContent==="
                + json.dumps(str(len(collection["selected_ids"]))),
            )
            await submit("красные ручки от 500 шт")
            await check(
                "New standalone search does not retain old state",
                "document.querySelectorAll('.query-category-chip').length===1 && ni('Тираж от, шт.')?.value==='500' && dc('Цвет')?.querySelector('.ui-select-value')?.textContent==='Красный'",
            )
            assert (await c.get(collection_path)).json() == collection
            checks.append(
                {
                    "check": "Collection exact IDs and version unchanged",
                    "status": "PASS",
                    "offer_ids": collection["selected_ids"],
                }
            )
            await submit(QUERY)
            await b.call(
                "Emulation.setDeviceMetricsOverride",
                {"width": 390, "height": 844, "deviceScaleFactor": 1, "mobile": True},
            )
            await b.js(
                "document.querySelector('.research-mobile-filters-button').click()"
            )
            await check(
                "Mobile filter sheet holds same numeric value",
                "[...document.querySelectorAll('dialog[open] label')].some(e=>e.firstChild?.textContent==='Тираж от, шт.'&&e.querySelector('input')?.value==='250') && document.documentElement.scrollWidth<=innerWidth",
            )
            await shot("mobile-filters")
            save(
                PUBLIC / "browser.json",
                {
                    "status": "PASS",
                    "browser": "Installed Microsoft Edge",
                    "checks": checks,
                },
            )
        except Exception:
            if b:
                await b.shot(PUBLIC / "browser-failure.png")
                print(
                    json.dumps(
                        await b.js(
                            "({text:document.querySelector('.research-composer textarea')?.value,button:document.querySelector('.research-composer button')?.textContent,error:document.querySelector('[role=alert]')?.textContent,view:document.querySelector('.research-hud-status')?.textContent})"
                        ),
                        ensure_ascii=True,
                    ),
                    flush=True,
                )
            raise
        finally:
            if b:
                await b.ws.close()
            if edge:
                try:
                    parent = psutil.Process(edge.pid)
                    owned = parent.children(recursive=True) + [parent]
                    for process in owned:
                        try:
                            process.terminate()
                        except psutil.NoSuchProcess:
                            pass
                    _, alive = psutil.wait_procs(owned, timeout=5)
                    assert not alive
                except psutil.NoSuchProcess:
                    pass
            assert folder.parent == (ROOT / ".local").resolve()
            shutil.rmtree(folder)
    print("Browser PASS", len(checks), "checks")


if __name__ == "__main__":
    asyncio.run(main())
