"""Installed Edge, real API searches plus explicitly labelled UI network fixtures."""

import asyncio
import json
import socket
import subprocess
import time

import httpx
from check_chat_workflow import save
from check_palette_browser import Browser

INSTRUMENT = r"""(()=>{
  const original=window.fetch.bind(window); window.__network=[]; window.__sources={created:0,live:0};
  window.fetch=async(...args)=>{
    const url=String(args[0] instanceof Request?args[0].url:args[0]),method=args[1]?.method||'GET';
    window.__network.push({url,method});
    if(window.__failNext&&method==='POST'&&url.endsWith('/requests')){window.__failNext=false;throw Error('Acceptance network failure');}
    if(window.__historyFixture&&url.includes('/history?'))return new Response(JSON.stringify(window.__historyFixture),{headers:{'Content-Type':'application/json'}});
    const result=await original(...args);
    if(method==='POST'&&url.endsWith('/chats'))while(window.__holdCreation)await new Promise(r=>setTimeout(r,20));
    if(method==='POST'&&url.endsWith('/requests'))while(window.__holdResponses)await new Promise(r=>setTimeout(r,20));
    if(method==='GET'&&(url.includes('/history?')||url.includes('/researches/chat/')||url.endsWith('/collection')))while(window.__holdHydration)await new Promise(r=>setTimeout(r,20));
    return result;
  };
  const NativeSource=window.EventSource;
  window.EventSource=class extends NativeSource{
    constructor(...args){super(...args);window.__sources.created++;window.__sources.live++;this.__closed=false;}
    close(){if(!this.__closed){this.__closed=true;window.__sources.live--;}super.close();}
  };
})();"""


async def run(state, root, ledger, public):
    public.mkdir(parents=True, exist_ok=True)
    if not state.get("edge_pid"):
        with socket.socket() as probe:
            assert probe.connect_ex(("127.0.0.1", 9226)) != 0
        profile = root / ".local" / ("clean-start-edge-" + state["run"])
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
                        await client.get("http://127.0.0.1:9226/json/list")
                    ).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.2)
    b = Browser()
    await b.connect(state["users"][0]["cookies"])
    evidence = []

    async def check(label, condition, **detail):
        evidence.append(
            {"check": label, "status": "PASS" if condition else "FAIL", **detail}
        )
        save(public / "browser.json", evidence)
        print(("PASS " if condition else "FAIL ") + label, flush=True)
        assert condition, label

    async def own_projects():
        values = await b.js("fetch('/api/projects').then(r=>r.json())")
        state["projects"] = [p["id"] for p in values]
        save(ledger, state)
        return values

    async def input_text(value):
        await b.js(
            "(()=>{const e=document.querySelector('.research-composer textarea');Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(e,"
            + json.dumps(value)
            + ");e.dispatchEvent(new Event('input',{bubbles:true}));})()"
        )

    async def theme(value):
        await b.js("document.querySelector('.theme-switch .ui-select-trigger').click()")
        await b.wait(
            "!!document.querySelector('.theme-switch .ui-select-popup:popover-open')"
        )
        await b.js(
            "Array.from(document.querySelectorAll('.theme-switch [role=option]')).find(e=>e.textContent.trim()==="
            + json.dumps("Pulse" if value == "pulse" else "Текущая")
            + ").click()"
        )
        await b.wait(
            "document.documentElement.dataset.siteTheme===" + json.dumps(value)
        )
        await asyncio.sleep(0.35)

    async def geometry():
        return await b.js(
            "(()=>{const main=document.querySelector('.research-main'),shell=document.querySelector('.research-shell');return {width:innerWidth,overflow:document.documentElement.scrollWidth>innerWidth,bodyOverflow:document.documentElement.scrollHeight>innerHeight+1,mainRight:main?.getBoundingClientRect().right,columns:shell?getComputedStyle(shell).gridTemplateColumns:null,titleNodes:document.querySelectorAll('[data-testid=header-project-title],.app-navbar-context,.research-top-project').length,forms:document.querySelectorAll('.research-composer').length,expanded:shell?.dataset.chatExpanded,navbar:document.querySelector('.app-navbar')?.getBoundingClientRect().height}})()"
        )

    async def new_chat():
        previous = await b.js("localStorage.getItem('forma-chat-id')")
        await b.js(
            "document.querySelector('.research-projects .project-list>button').click()"
        )
        await b.wait(
            "localStorage.getItem('forma-chat-id')!=="
            + json.dumps(previous)
            + "&&!!document.querySelector('#research-query')&&!document.querySelector('.research-chat')"
        )
        await own_projects()
        return await b.js("localStorage.getItem('forma-chat-id')")

    try:
        await b.call("Page.addScriptToEvaluateOnNewDocument", {"source": INSTRUMENT})
        await b.call("Page.navigate", {"url": "http://127.0.0.1:3000/"})
        await b.wait("!!document.querySelector('#research-query')")
        for value in ["current", "pulse"]:
            await theme(value)
            g = await geometry()
            await check(
                value
                + " empty workspace has no panel, zero summary or leftover column",
                await b.js(
                    "!document.querySelector('.research-chat,.research-chat-reopen,.research-hud-selected')"
                )
                and abs(g["mainRight"] - 1440) < 1
                and g["titleNodes"] == 0
                and not g["overflow"],
                geometry=g,
            )
            await check(
                value + " short sidebar has no unnecessary vertical scrollbar",
                await b.js(
                    "(()=>{const n=document.querySelector('.research-projects');return n.scrollHeight===n.clientHeight})()"
                ),
            )
            await b.shot(public / (value + "-empty.png"))
            await input_text("Рюкзак Melango")
            await check(
                value + " typing does not open chat",
                await b.js(
                    "!document.querySelector('.research-chat,.research-chat-reopen')"
                ),
            )
            await input_text("")
        # Use an explicitly created, recorded chat so recovery cannot miss owned IDs.
        first_chat = await new_chat()
        state["first_chat"] = first_chat
        save(ledger, state)
        await b.js("window.__holdResponses=true")
        await input_text("Рюкзак Melango")
        started = time.perf_counter()
        await b.js("document.querySelector('.research-composer').requestSubmit()")
        await b.wait(
            "document.querySelector('.research-chat [data-message-id^=\"local:\"]')?.textContent.includes('сохранение ещё не подтверждено')"
        )
        await check(
            "first submit displays local message before HTTP acknowledgement",
            await b.js(
                "document.querySelector('.research-chat').textContent.includes('Рюкзак Melango')"
            ),
            seconds=round(time.perf_counter() - started, 3),
        )
        await b.wait("!!document.querySelector('[aria-label=\"Свернуть чат\"]')")
        await b.js("document.querySelector('[aria-label=\"Свернуть чат\"]').click()")
        await b.wait("!document.querySelector('.research-chat')")
        await check(
            "collapse frees the full width and restores focus",
            await b.js(
                "document.activeElement.classList.contains('research-chat-reopen')"
            )
            and abs((await geometry())["mainRight"] - 1440) < 1,
        )
        await b.js("window.__holdResponses=false")
        await b.wait("document.querySelectorAll('.product-card').length>0", timeout=140)
        await check(
            "late server response does not reopen a manually closed chat",
            await b.js(
                "!document.querySelector('.research-chat')&&!!document.querySelector('.research-chat-reopen')"
            ),
        )
        await b.js("document.querySelector('.research-chat-reopen').click()")
        await b.wait(
            "document.querySelectorAll('.research-chat [data-message-id]').length===2"
        )
        await input_text("Сохранённый черновик")
        snapshot = await b.js(
            "({posts:__network.filter(n=>n.method==='POST').length,sources:{...__sources}})"
        )
        for _ in range(3):
            await b.js(
                "document.querySelector('[aria-label=\"Свернуть чат\"]').click()"
            )
            await b.wait("!document.querySelector('.research-chat')")
            await b.js("document.querySelector('.research-chat-reopen').click()")
            await b.wait("!!document.querySelector('.research-chat textarea')")
        after = await b.js(
            "({posts:__network.filter(n=>n.method==='POST').length,sources:{...__sources}})"
        )
        await check(
            "three toggles keep one composer, history and draft without POST or new subscriptions",
            snapshot == after
            and await b.js(
                "document.querySelectorAll('.research-composer textarea').length===1&&document.querySelector('#research-message').value==='Сохранённый черновик'&&document.querySelectorAll('[data-message-id]').length===2"
            ),
            before=snapshot,
            after=after,
        )
        await b.js(
            "document.querySelector('.product-card input[type=checkbox]').click()"
        )
        await b.wait(
            "document.querySelector('.research-selected-count')?.textContent==='1'"
        )
        for value in ["current", "pulse"]:
            await theme(value)
            await b.shot(public / (value + "-open.png"))
            await b.js(
                "document.querySelector('[aria-label=\"Свернуть чат\"]').click()"
            )
            await b.wait("!document.querySelector('.research-chat')")
            await b.shot(public / (value + "-collapsed.png"))
            await b.js("document.querySelector('.research-chat-reopen').click()")
            await b.wait("!!document.querySelector('.research-chat textarea')")
        await b.call("Page.reload", {"ignoreCache": True})
        await b.wait(
            "document.querySelector('.research-selected-count')?.textContent==='1'&&document.querySelectorAll('[data-message-id]').length===2"
        )
        await check(
            "F5 restores search, selected product, history and the controlled draft",
            await b.js(
                "document.querySelector('#research-message').value==='Сохранённый черновик'"
            ),
        )
        # Force delayed response reads while switching to a genuinely empty chat.
        second_chat = await new_chat()
        state["second_chat"] = second_chat
        save(ledger, state)
        await check(
            "new chat after an old one does not display old messages, products or selection",
            await b.js(
                "!document.querySelector('[data-message-id],.product-card,.research-hud-selected,.research-collection-shortcut')"
            ),
        )
        await b.js("window.__holdHydration=true")
        await b.js("document.querySelectorAll('.project-open')[1].click()")
        await b.wait(
            "document.querySelector('.research-main').textContent.includes('Восстанавливаю')"
        )
        await check(
            "slow history/research/collection hydration never impersonates an empty chat",
            await b.js(
                "!document.querySelector('#research-query,.product-card,.research-selected-count')"
            ),
        )
        await b.js("window.__holdHydration=false")
        await b.wait(
            "document.querySelector('.research-selected-count')?.textContent==='1'&&document.querySelectorAll('[data-message-id]').length===2"
        )
        # Long history is an explicit UI response fixture, never inserted into SQL.
        page = await b.js(
            "fetch('/api/chats/'+localStorage.getItem('forma-chat-id')+'/history?').then(r=>r.json())"
        )
        page.update(
            messages=[
                {
                    "id": f"fixture-{i:03}",
                    "role": "user" if i % 2 == 0 else "assistant",
                    "content": "UI fixture · длинная переписка. "
                    + ("Условия сохранены. " * 12),
                    "created_at": f"2026-11-07T10:{i // 60:02}:{i % 60:02}Z",
                    "operation_id": None,
                    "request_id": None,
                    "status": "COMPLETED",
                    "research_id": None,
                    "origin": "UI_FIXTURE",
                }
                for i in range(100)
            ],
            next_before=None,
            next_after=None,
        )
        await b.js("window.__historyFixture=" + json.dumps(page))
        await b.call("Page.reload", {"ignoreCache": True})
        # Reload clears the runtime fixture; install it into the next navigation only.
        fixture_script = await b.call(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": "window.__historyFixture=" + json.dumps(page)},
        )
        await b.call("Page.reload", {"ignoreCache": True})
        await b.wait("document.querySelectorAll('[data-message-id]').length===100")
        for value in ["current", "pulse"]:
            await theme(value)
            metrics = await b.js(
                "(()=>{const n=document.querySelector('.research-messages'),s=getComputedStyle(n),w=getComputedStyle(n,'::-webkit-scrollbar'),t=getComputedStyle(n,'::-webkit-scrollbar-thumb');return {height:n.clientHeight,scrollHeight:n.scrollHeight,width:w.width,thumb:t.backgroundColor,track:getComputedStyle(n,'::-webkit-scrollbar-track').backgroundColor,standard:s.scrollbarColor,bodyOverflow:document.documentElement.scrollHeight>innerHeight+1}})()"
            )
            await check(
                value
                + " long native history uses 10px themed scrollbar without body scrolling",
                metrics["scrollHeight"] > metrics["height"]
                and metrics["width"] == "10px"
                and metrics["standard"] == "auto"
                and not metrics["bodyOverflow"],
                metrics=metrics,
            )
            await b.shot(public / (value + "-long-history.png"))
        await b.js("document.querySelector('.research-messages').focus()")
        await b.call(
            "Input.dispatchKeyEvent",
            {
                "type": "keyDown",
                "key": "Home",
                "windowsVirtualKeyCode": 36,
                "modifiers": 2,
            },
        )
        await b.call(
            "Input.dispatchKeyEvent",
            {
                "type": "keyUp",
                "key": "Home",
                "windowsVirtualKeyCode": 36,
                "modifiers": 0,
            },
        )
        await asyncio.sleep(0.3)
        await check(
            "keyboard scroll remains native",
            await b.js("document.querySelector('.research-messages').scrollTop<100"),
        )
        await b.call("Page.bringToFront")
        await b.wait(
            "document.querySelector('.research-chat').getBoundingClientRect().x<innerWidth-300"
        )
        rect = await b.js(
            "(()=>{const r=document.querySelector('.research-messages').getBoundingClientRect();return {x:r.x+50,y:r.y+100}})()"
        )
        await check(
            "wheel coordinates hit the visible history",
            await b.js(
                "!!document.elementFromPoint("
                + str(rect["x"])
                + ","
                + str(rect["y"])
                + ")?.closest('.research-messages')"
            ),
            point=rect,
        )
        # Finish the keyboard gesture and give the compositor a stable baseline.
        await b.call(
            "Input.dispatchKeyEvent",
            {
                "type": "keyUp",
                "key": "Control",
                "windowsVirtualKeyCode": 17,
                "modifiers": 0,
            },
        )
        await b.js("document.querySelector('.research-messages').scrollTop=0")
        await b.shot(public / "keyboard-history.png")
        await b.call("Input.dispatchMouseEvent", {"type": "mouseMoved", **rect})
        await b.call(
            "Input.dispatchMouseEvent",
            {"type": "mouseWheel", **rect, "deltaX": 0, "deltaY": 240, "modifiers": 0},
        )
        await asyncio.sleep(0.3)
        await check(
            "native wheel scroll works",
            await b.js("document.querySelector('.research-messages').scrollTop>0"),
        )
        await b.call(
            "Emulation.setEmulatedMedia",
            {"features": [{"name": "prefers-reduced-motion", "value": "reduce"}]},
        )
        await check(
            "reduced motion disables the panel transition",
            await b.js(
                "getComputedStyle(document.querySelector('.research-shell')).transitionDuration==='0s'"
            ),
        )
        await b.call(
            "Emulation.setEmulatedMedia",
            {"features": [{"name": "forced-colors", "value": "active"}]},
        )
        forced = await b.js(
            "(()=>{const n=document.querySelector('.research-messages');return {standard:getComputedStyle(n).scrollbarColor,width:getComputedStyle(n,'::-webkit-scrollbar').width}})()"
        )
        await check(
            "forced colors restores system scrollbar styling",
            forced["standard"] == "auto" and forced["width"] == "auto",
            metrics=forced,
        )
        await b.call("Emulation.setEmulatedMedia", {"features": []})
        for width, height, scale in [(1152, 800, 1.25), (960, 667, 1.5), (390, 844, 1)]:
            await b.call(
                "Emulation.setDeviceMetricsOverride",
                {
                    "width": width,
                    "height": height,
                    "deviceScaleFactor": scale,
                    "mobile": width == 390,
                },
            )
            await asyncio.sleep(0.4)
            g = await geometry()
            await check(
                f"effective zoom/viewport {width}x{height} DPR {scale} has no horizontal overflow",
                not g["overflow"],
                geometry=g,
            )
            if width == 390:
                await b.wait("!!document.querySelector('.workspace-chat-sheet[open]')")
                await b.shot(public / "mobile-chat.png")
                await b.call(
                    "Input.dispatchKeyEvent",
                    {"type": "keyDown", "key": "Escape", "windowsVirtualKeyCode": 27},
                )
                await b.wait("!document.querySelector('.workspace-chat-sheet')")
                await check(
                    "mobile drawer closes without losing the search",
                    await b.js(
                        "!!document.querySelector('.product-card')&&!!document.querySelector('.research-chat-reopen')"
                    ),
                )
                await b.shot(public / "mobile-grid.png")
        await b.call(
            "Page.removeScriptToEvaluateOnNewDocument",
            {"identifier": fixture_script["identifier"]},
        )
        await b.call(
            "Emulation.setDeviceMetricsOverride",
            {"width": 1440, "height": 1000, "deviceScaleFactor": 1, "mobile": False},
        )
        await b.call("Page.reload", {"ignoreCache": True})
        await b.wait("!!document.querySelector('.research-chat-reopen')")
        await b.js("document.querySelector('.research-chat-reopen').click()")
        await b.wait("document.querySelectorAll('[data-message-id]').length===2")
        # A saved project collection with no research remains accessible.
        values = await own_projects()
        empty = next(p for p in values if p["chat_id"] == second_chat)
        await b.js(
            "fetch('/api/projects/'+"
            + json.dumps(empty["id"])
            + "+'/collection/items',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({offer_id:'vto:1823109'})}).then(r=>{if(!r.ok)throw Error(r.status);return r.json()})"
        )
        await b.js("document.querySelectorAll('.project-open')[0].click()")
        await b.wait("!!document.querySelector('.research-collection-shortcut')")
        await check(
            "saved collection is available without an active research",
            await b.js(
                "document.querySelector('.research-collection-shortcut').textContent.includes('1')&&!document.querySelector('.research-hud-selected')&&!!document.querySelector('#research-query')"
            ),
        )
        await b.js("document.querySelector('.research-collection-shortcut').click()")
        await b.wait("!!document.querySelector('.selected-product')")
        await b.call(
            "Input.dispatchKeyEvent",
            {"type": "keyDown", "key": "Escape", "windowsVirtualKeyCode": 27},
        )
        await b.wait("!document.querySelector('dialog[open]')")
        await input_text("ручки до 1 тенге")
        await b.js("document.querySelector('.research-composer').requestSubmit()")
        await b.wait(
            "document.querySelectorAll('[data-message-id]').length===2&&!!document.querySelector('.research-results-hud')&&!document.querySelector('.product-card')",
            timeout=140,
        )
        await check(
            "real zero-result search retains conversation and saved collection",
            await b.js(
                "document.querySelector('.research-selected-count').textContent==='1'&&!!document.querySelector('.research-chat')"
            ),
        )
        await new_chat()
        await b.js("window.__failNext=true")
        await input_text("Неподтверждённый запрос")
        await b.js("document.querySelector('.research-composer').requestSubmit()")
        await b.wait(
            "document.querySelector('.research-chat').textContent.includes('Отправка не подтверждена')"
        )
        await check(
            "network failure preserves draft and an explicitly unconfirmed local message",
            await b.js(
                "document.querySelector('#research-message').value==='Неподтверждённый запрос'&&!document.querySelector('.research-chat').textContent.includes('Сообщение сохранено')"
            ),
        )
        for path in ["/offers", "/offers/archive", "/"]:
            await b.call("Page.navigate", {"url": "http://127.0.0.1:3000" + path})
            await b.wait("!!document.querySelector('.app-navbar')")
            await check(
                "navbar has no chat-title DOM on " + path,
                await b.js(
                    "!document.querySelector('[data-testid=header-project-title],.app-navbar-context,.research-top-project')"
                ),
            )
        await own_projects()
        save(public / "browser.json", evidence)
    finally:
        await own_projects()
        await b.ws.close()
