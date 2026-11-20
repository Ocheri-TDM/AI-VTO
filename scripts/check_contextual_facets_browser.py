"""Installed Edge with real API/filter writes; no tunnel and no browser runtime install."""

import asyncio
import json
import socket
import subprocess

import httpx
from check_chat_workflow import save
from check_palette_browser import Browser


async def run(state, root, ledger, public):
    profile = root / ".local" / ("clean-start-edge-" + state["run"])
    if not state.get("edge_pid"):
        with socket.socket() as probe:
            assert probe.connect_ex(("127.0.0.1", 9226)) != 0
        process = await asyncio.to_thread(
            subprocess.Popen,
            [
                "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
                "--headless=new",
                "--disable-extensions",
                "--remote-debugging-port=9226",
                "--user-data-dir=" + str(profile),
                "--no-first-run",
                "--no-default-browser-check",
                "about:blank",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        state.update(edge_pid=process.pid, edge_profile=str(profile))
        save(ledger, state)
    async with httpx.AsyncClient() as client:
        for _ in range(100):
            try:
                if (
                    await client.get("http://127.0.0.1:9226/json/version")
                ).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.1)
    b = Browser()
    await b.connect(state["users"][0]["cookies"])
    checks = []

    def check(name, valid, **details):
        checks.append({"check": name, "status": "PASS" if valid else "FAIL", **details})
        save(
            public / "browser.json",
            {"checks": checks, "origin": "http://127.0.0.1:3000"},
        )
        print(("PASS " if valid else "FAIL ") + name, flush=True)
        assert valid, name

    async def latest():
        return await b.js(
            "fetch('/api/researches/chat/'+localStorage.getItem('forma-chat-id')).then(r=>r.json())"
        )

    async def control(scope, label=None):
        if label is None:
            return (
                "[...document.querySelectorAll("
                + json.dumps(scope)
                + ")].find(e=>e.getBoundingClientRect().width>0)"
            )
        return (
            "[...document.querySelectorAll("
            + json.dumps(scope)
            + ")].find(e=>e.getBoundingClientRect().width>0&&e.querySelector('.ui-select-label')?.textContent==="
            + json.dumps(label)
            + ")"
        )

    async def open_dropdown(scope, label=None):
        expression = await control(scope, label)
        await b.js(
            "(()=>{const e="
            + expression
            + ";e.scrollIntoView({block:'center'});e.querySelector('.ui-select-trigger').click()})()"
        )
        await b.wait("!!document.querySelector('.ui-select-popup:popover-open')")

    async def choose(scope, value, label=None):
        await open_dropdown(scope, label)
        await b.js(
            "[...document.querySelector('.ui-select-popup:popover-open').querySelectorAll('[role=option]')].find(e=>e.querySelector('.ui-select-option-text').textContent==="
            + json.dumps(value)
            + ").click()"
        )
        expression = await control(scope, label)
        await b.wait(
            "(()=>{const e="
            + expression
            + ";return e?.querySelector('.ui-select-value').textContent==="
            + json.dumps(value)
            + "&&!e.querySelector('.ui-select-trigger').disabled})()"
        )

    try:
        await b.call(
            "Page.addScriptToEvaluateOnNewDocument",
            {
                "source": "window.__facetReads=[];window.__facetHeld=0;const nativeFacetFetch=fetch;window.fetch=async(...args)=>{const url=String(args[0]);if(url.includes('/researches'))window.__facetReads.push({method:args[1]?.method||'GET'});const response=await nativeFacetFetch(...args);if(window.__holdFacetRead&&url.includes('/researches/chat/')){window.__facetHeld++;while(window.__holdFacetRead)await new Promise(r=>setTimeout(r,25));window.__facetHeld--;}return response;};"
            },
        )
        await b.call("Page.navigate", {"url": "http://127.0.0.1:3000/"})
        await b.wait("!!document.querySelector('.app-navbar')")
        await b.js(
            "localStorage.setItem('forma-chat-id'," + json.dumps(state["ui_chat"]) + ")"
        )
        await b.call("Page.reload")
        await b.wait(
            "document.querySelectorAll('.category-characteristics-group').length===3"
        )
        data = await latest()
        layout = await b.js(
            "(()=>{const p=document.querySelector('.research-desktop-filters .research-inline-filters'),s=p.querySelector('.category-characteristics'),groups=[...s.querySelectorAll('.category-characteristics-group')].map(e=>e.getBoundingClientRect());return {panel:p.clientWidth,section:s.getBoundingClientRect().width,rows:groups.map(r=>r.top)};})()"
        )
        check(
            "category groups occupy a full compact row below common filters",
            layout["section"] >= layout["panel"] - 1
            and max(layout["rows"]) - min(layout["rows"]) < 2,
            layout=layout,
        )
        branches = {v["concept"]: v["id"] for v in data["query_categories"]}
        bottle = (
            '[data-filter-branch="'
            + branches["bottle"]
            + '"] [data-attribute-id="capacity_ml"]'
        )
        mug = (
            '[data-filter-branch="'
            + branches["mug"]
            + '"] [data-attribute-id="capacity_ml"]'
        )
        check(
            "global original color and quantity remain visible",
            await b.js(
                "[...document.querySelectorAll('.effective-filter-control')].some(e=>e.querySelector('.ui-select-label')?.textContent==='Цвет'&&e.querySelector('.ui-select-value')?.textContent==='Красный')&&[...document.querySelectorAll('.effective-filter-control input')].some(e=>e.value==='100')"
            ),
        )
        check(
            "no universal size or polluted category chips",
            await b.js(
                "![...document.querySelectorAll('.research-results-hud .ui-select-label')].some(e=>e.textContent==='Размер')&&[...document.querySelectorAll('.query-category-caption')].every(e=>!e.textContent.includes('мл'))"
            ),
        )
        before_reads = await b.js("window.__facetReads.length")
        await open_dropdown(bottle)
        check(
            "expanded bottle dropdown has only contextual capacities and counts",
            await b.js(
                "[...document.querySelectorAll('.ui-select-popup:popover-open [role=option]')].every(e=>e.querySelector('.ui-select-option-text').textContent==='Все'||(/мл$/.test(e.querySelector('.ui-select-option-text').textContent)&&!!e.querySelector('.ui-select-count')))&&!document.querySelector('.ui-select-popup:popover-open').textContent.match(/JINGLE|V2|A4|A5|лет/)"
            ),
        )
        check(
            "opening a facet makes no additional research request",
            await b.js("window.__facetReads.length") == before_reads,
        )
        await b.shot(public / "bottle-dropdown.png")
        await b.call(
            "Input.dispatchKeyEvent",
            {"type": "keyDown", "key": "Escape", "windowsVirtualKeyCode": 27},
        )
        await choose(bottle, "500 мл")
        await choose(mug, "350 мл")
        data = await latest()
        check(
            "two real local filters preserve umbrella results",
            data["view"]["filters"]["branch_attributes"][branches["bottle"]][
                "capacity_ml"
            ]
            == ["500"]
            and data["view"]["filters"]["branch_attributes"][branches["mug"]][
                "capacity_ml"
            ]
            == ["350"]
            and next(
                c["count"]
                for c in data["query_categories"]
                if c["concept"] == "umbrella"
            )
            > 0,
        )
        check(
            "nested branch filters expose reset",
            await b.js(
                "[...document.querySelectorAll('.research-hud-status button')].some(e=>e.textContent==='Сбросить фильтры')"
            ),
        )
        await b.call("Page.reload")
        await b.wait(
            "[...document.querySelectorAll('.category-characteristics .ui-select-value')].some(e=>e.textContent==='500 мл')&&[...document.querySelectorAll('.category-characteristics .ui-select-value')].some(e=>e.textContent==='350 мл')"
        )
        check(
            "F5 restores both branch values and source requirements",
            (await latest())["view"]["filters"]["branch_attributes"]
            == data["view"]["filters"]["branch_attributes"],
        )
        await b.js(
            "document.querySelector('[data-branch-id=\""
            + branches["bottle"]
            + "\"] button').click()"
        )
        await b.wait(
            "!document.querySelector('.category-characteristics [data-filter-branch=\""
            + branches["bottle"]
            + "\"]')"
        )
        check(
            "hide removes only that category facet",
            await b.js(
                "!!document.querySelector('.category-characteristics [data-filter-branch=\""
                + branches["mug"]
                + "\"]')"
            ),
        )
        await b.js(
            "document.querySelector('[data-branch-id=\""
            + branches["bottle"]
            + "\"] button').click()"
        )
        await b.wait(
            "[...document.querySelectorAll('.category-characteristics .ui-select-value')].some(e=>e.textContent==='500 мл')"
        )
        check("show restores saved local filter", True)
        # Inject an allowed active value through the real revisioned API, then
        # reload the real UI: no mocked empty facets or fabricated catalog rows.
        data = await latest()
        body = {"version": data["view"]["version"], "filters": data["view"]["filters"]}
        body["filters"]["branch_attributes"][branches["bottle"]]["capacity_ml"] = [
            "999999"
        ]
        result = await b.js(
            "fetch('/api/researches/"
            + data["id"]
            + "/view',{method:'PATCH',headers:{'Content-Type':'application/json'},body:"
            + json.dumps(json.dumps(body))
            + "}).then(r=>r.status)"
        )
        check("active zero written through real API", result == 200)
        await b.call("Page.reload")
        await b.wait(
            "[...document.querySelectorAll('.category-characteristics .ui-select-value')].some(e=>e.textContent==='999999 мл')"
        )
        await open_dropdown(bottle)
        check(
            "selected zero is visible and not disabled",
            await b.js(
                "[...document.querySelectorAll('.ui-select-popup:popover-open [role=option]')].some(e=>e.querySelector('.ui-select-option-text').textContent==='999999 мл'&&e.getAttribute('aria-disabled')==='false'&&e.querySelector('.ui-select-count').textContent==='0')"
            ),
        )
        await b.shot(public / "active-zero.png")
        await b.call(
            "Input.dispatchKeyEvent",
            {"type": "keyDown", "key": "Escape", "windowsVirtualKeyCode": 27},
        )
        await b.js(
            "[...document.querySelectorAll('.research-hud-status button')].find(e=>e.textContent==='Сбросить фильтры').click()"
        )
        await b.wait(
            "!document.querySelector('.research-hud-status button')&&[...document.querySelectorAll('.category-characteristics .ui-select-value')].every(e=>e.textContent==='Все')"
        )
        check(
            "reset returns original red and quantity rather than all catalog",
            (await latest())["effective_filters"]["fields"]["colors"]["values"]
            == ["RED"]
            and (await latest())["effective_filters"]["fields"]["minimum_quantity"][
                "number"
            ]
            == 100,
        )
        for theme in ["current", "pulse"]:
            await b.js(
                "document.querySelector('.theme-switch .ui-select-trigger').click()"
            )
            await b.wait(
                "!!document.querySelector('.theme-switch .ui-select-popup:popover-open')"
            )
            await b.js(
                "[...document.querySelectorAll('.theme-switch [role=option]')].find(e=>e.querySelector('.ui-select-option-text').textContent==="
                + json.dumps("Pulse" if theme == "pulse" else "Текущая")
                + ").click()"
            )
            for width in [1440, 390]:
                await b.call(
                    "Emulation.setDeviceMetricsOverride",
                    {
                        "width": width,
                        "height": 1000 if width == 1440 else 844,
                        "deviceScaleFactor": 1,
                        "mobile": width == 390,
                    },
                )
                if width == 390:
                    await b.js(
                        "[...document.querySelectorAll('button')].find(e=>e.textContent==='Фильтры'&&e.getBoundingClientRect().width>0).click()"
                    )
                    await b.wait(
                        "!!document.querySelector('.sheet .category-characteristics')"
                    )
                await open_dropdown(bottle)
                geometry = await b.js(
                    "(()=>{const e=document.querySelector('.ui-select-popup:popover-open'),r=e.getBoundingClientRect();return {overflow:document.documentElement.scrollWidth>innerWidth+1,popupInside:r.left>=0&&r.right<=innerWidth+1&&r.top>=0&&r.bottom<=innerHeight+1,font:getComputedStyle(e).fontFamily}})()"
                )
                check(
                    f"{theme} {width}px dropdown, Geist and geometry",
                    not geometry["overflow"]
                    and geometry["popupInside"]
                    and "Geist" in geometry["font"],
                    geometry=geometry,
                )
                await b.shot(public / f"filters-{theme}-{width}.png")
                await b.call(
                    "Input.dispatchKeyEvent",
                    {"type": "keyDown", "key": "Escape", "windowsVirtualKeyCode": 27},
                )
                if width == 390:
                    await b.call(
                        "Input.dispatchKeyEvent",
                        {
                            "type": "keyDown",
                            "key": "Escape",
                            "windowsVirtualKeyCode": 27,
                        },
                    )
                    await b.wait("!document.querySelector('.sheet')")
        await b.call(
            "Emulation.setDeviceMetricsOverride",
            {"width": 1440, "height": 1000, "deviceScaleFactor": 1, "mobile": False},
        )
        check(
            "filtering retains project collection",
            bool(
                (
                    await b.js(
                        "fetch('/api/projects/"
                        + state["ui_project"]
                        + "/collection').then(r=>r.json())"
                    )
                )["selected_ids"]
            ),
        )
        before_groups = (await latest())["category_facets"]
        await choose(".research-view-setting .ui-select", "По моделям")
        check(
            "model grouping leaves facet context unchanged",
            (await latest())["category_facets"] == before_groups,
        )
        await choose(".research-view-setting .ui-select", "Все варианты")
        # Activate saved queries through the real History UI in the other owned
        # chat. Delayed network evidence uses the actual old response, not a mock
        # payload with fabricated facet values.
        cases = json.loads((public / "after.json").read_text("utf-8"))["cases"]
        shirt = next(c for c in cases if c["query"] == "Футболки")
        old = await b.js(
            "fetch('/api/researches/" + shirt["research_id"] + "').then(r=>r.json())"
        )

        async def switch_chat(identifier):
            await b.js(
                "fetch('/api/projects').then(r=>r.json()).then(ps=>{const index=ps.findIndex(p=>p.chat_id==="
                + json.dumps(identifier)
                + ");document.querySelectorAll('.research-projects .project-open')[index].click()})"
            )

        async def history_query(query):
            await b.click(
                "История", "document.querySelector('.workspace-query-actions')"
            )
            await b.wait("!!document.querySelector('.workspace-history button')")
            await b.js(
                "[...document.querySelectorAll('.workspace-history button')].find(e=>e.querySelector('strong').textContent==="
                + json.dumps(query)
                + ").click()"
            )
            await b.wait("!document.querySelector('.workspace-history')")

        await switch_chat(old["chat_id"])
        await b.wait(
            "localStorage.getItem('forma-chat-id')==="
            + json.dumps(old["chat_id"])
            + "&&!!document.querySelector('.category-characteristics')"
        )
        await history_query("Футболки")
        await b.wait("!!document.querySelector('[data-attribute-id=apparel_size]')")
        await open_dropdown("[data-attribute-id=apparel_size]")
        check(
            "single clothing category offers apparel sizes without drink volumes",
            await b.js(
                "!!document.querySelector('.ui-select-popup:popover-open')&&!document.querySelector('.ui-select-popup:popover-open').textContent.includes('мл')&&!document.querySelector('.category-characteristics-title')"
            ),
        )
        await b.call(
            "Input.dispatchKeyEvent",
            {"type": "keyDown", "key": "Escape", "windowsVirtualKeyCode": 27},
        )
        await b.js(
            "window.__holdFacetRead=true;window.dispatchEvent(new Event('focus'))"
        )
        await b.wait("window.__facetHeld>0")
        await history_query("Бутылки, кружки, зонты")
        await b.wait(
            "document.querySelectorAll('.category-characteristics-group').length===3&&!document.querySelector('[data-attribute-id=apparel_size]')"
        )
        await b.js("window.__holdFacetRead=false")
        await b.wait("window.__facetHeld===0")
        # Flush promise callbacks and a React frame after releasing the stale read.
        await b.js(
            "new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))"
        )
        check(
            "late clothing response cannot overwrite activated multi-category facets",
            await b.js(
                "document.querySelectorAll('.category-characteristics-group').length===3&&!document.querySelector('[data-attribute-id=apparel_size]')"
            ),
        )
        await b.shot(public / "history-after-clothing.png")
        await history_query("Футболки")
        await b.wait("!!document.querySelector('[data-attribute-id=apparel_size]')")
        await b.js(
            "(()=>{const opener=document.querySelector('[aria-label=\"Открыть чат\"]');if(opener)opener.click();})()"
        )
        await b.wait(
            "document.querySelector('.research-composer textarea')?.getBoundingClientRect().width>0"
        )
        await b.js(
            "(()=>{const field=document.querySelector('.research-composer textarea');Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(field,'Бутылки');field.dispatchEvent(new Event('input',{bubbles:true}));})()"
        )
        await b.wait("!document.querySelector('.research-composer button').disabled")
        await b.js(
            "(()=>{const nativeSearchFetch=window.fetch;window.__holdSearch=true;window.fetch=async(...args)=>{const response=await nativeSearchFetch(...args);if(String(args[0]).endsWith('/requests'))while(window.__holdSearch)await new Promise(r=>setTimeout(r,25));return response;};document.querySelector('.research-composer').requestSubmit();})()"
        )
        await b.wait(
            "[...document.querySelectorAll('.research-desktop-filters [role=status]')].some(e=>e.textContent==='Загружаю фильтры…')"
        )
        check(
            "pending standalone bottle search hides previous clothing controls",
            await b.js(
                "!document.querySelector('.research-desktop-filters [data-attribute-id=apparel_size]')&&!document.querySelector('.research-desktop-filters [role=option]')"
            ),
        )
        await b.js("window.__holdSearch=false")
        await b.wait(
            "!!document.querySelector('.category-characteristics.is-single [data-attribute-id=capacity_ml]')",
            timeout=90,
        )
        await open_dropdown(
            ".category-characteristics.is-single [data-attribute-id=capacity_ml]"
        )
        check(
            "standalone bottle search offers only its capacity facet",
            await b.js(
                "!document.querySelector('[data-attribute-id=apparel_size]')&&[...document.querySelectorAll('.ui-select-popup:popover-open [role=option]')].every(e=>e.querySelector('.ui-select-option-text').textContent==='Все'||e.querySelector('.ui-select-option-text').textContent.endsWith('мл'))"
            ),
        )
        await b.shot(public / "single-bottle.png")
        await b.call(
            "Input.dispatchKeyEvent",
            {"type": "keyDown", "key": "Escape", "windowsVirtualKeyCode": 27},
        )
        await switch_chat(state["ui_chat"])
        await b.wait(
            "localStorage.getItem('forma-chat-id')==="
            + json.dumps(state["ui_chat"])
            + "&&[...document.querySelectorAll('.effective-filter-control')].some(e=>e.querySelector('.ui-select-value')?.textContent==='Красный')"
        )
        check(
            "switching chat restores its independent source constraints",
            (await latest())["effective_filters"]["fields"]["minimum_quantity"][
                "number"
            ]
            == 100,
        )
    finally:
        b.ws.close_timeout = 1
        await b.ws.close()
