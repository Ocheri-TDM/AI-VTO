"""Native installed Edge UI, two themes, two viewports and isolated guest."""

import asyncio
import json
import socket
import subprocess
from uuid import uuid4

import httpx
import websockets
from check_palette_browser import Browser

from app.config import Settings
from app.database.connection import create_database
from app.database.models import Chat, Message
from app.domain.models import utcnow


async def run(state, root, ledger, public):
    from check_chat_workflow import call, save

    with socket.socket() as probe:
        occupied = probe.connect_ex(("127.0.0.1", 9226)) == 0
    profile = root / ".local" / ("chat-workflow-edge-" + state["run"])
    if occupied:
        import psutil

        parent = psutil.Process(state["edge_pid"])
        assert "--user-data-dir=" + str(profile) in parent.cmdline()
        owned = [parent, *parent.children(recursive=True)]
        listeners = {
            c.pid
            for c in psutil.net_connections(kind="tcp")
            if c.laddr and c.laddr.port == 9226 and c.status == "LISTEN"
        }
        assert listeners <= {p.pid for p in owned}
        for process in reversed(owned):
            try:
                process.terminate()
            except psutil.NoSuchProcess:
                pass
        await asyncio.to_thread(psutil.wait_procs, owned, timeout=5)
    edge = subprocess.Popen(
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
    state.update(edge_pid=edge.pid, edge_profile=str(profile))
    save(ledger, state)
    async with httpx.AsyncClient() as c:
        for _ in range(100):
            try:
                if (await c.get("http://127.0.0.1:9226/json/list")).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.2)
    b = Browser()
    await b.connect(state["users"][0]["cookies"])
    checks = []

    async def check(label, condition=True):
        assert condition, label
        checks.append({"check": label, "pass": True})
        save(public / "browser.json", {"checks": checks})

    async def navigate(chat):
        script = await b.call(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": f"localStorage.setItem('forma-chat-id',{json.dumps(chat)})"},
        )
        await b.call("Page.navigate", {"url": "http://127.0.0.1:3000/"})
        await b.wait(
            "!!document.querySelector('.project-list')&&localStorage.getItem('forma-chat-id')==="
            + json.dumps(chat)
        )
        await b.call("Page.removeScriptToEvaluateOnNewDocument", {"identifier": script["identifier"]})

    async def record_owned():
        projects = await b.js("fetch('/api/projects').then(r=>r.json())")
        for p in projects:
            if p["id"] not in state["projects"]:
                state["projects"].append(p["id"])
        save(ledger, state)
        return projects

    async def theme(value):
        await b.js("document.querySelector('.theme-switch .ui-select-trigger').click()")
        await b.wait("!!document.querySelector('.theme-switch .ui-select-popup:popover-open')")
        await b.js(
            "Array.from(document.querySelectorAll('.theme-switch [role=option]')).find(e=>e.textContent.trim()==="
            + json.dumps("Pulse" if value == "pulse" else "Текущая")
            + ").click()"
        )
        await b.wait("document.documentElement.dataset.siteTheme===" + json.dumps(value))
        await asyncio.sleep(0.4)  # Let the existing theme transition finish before screenshots.

    async def send(message):
        await b.wait("document.querySelectorAll('.research-composer textarea').length===1")
        await b.js("document.querySelector('.research-composer textarea').focus()")
        await b.call("Input.insertText", {"text": message})
        await b.wait("!document.querySelector('.research-composer button').disabled")
        await b.js("document.querySelector('.research-composer button').click()")
        await b.wait(
            "!document.querySelector('.research-composer button')?.textContent.includes('Отправляю')",
            timeout=100,
        )

    try:
        await navigate(state["conversation_project"]["chat_id"])
        await b.wait("document.querySelectorAll('[data-message-id]').length===10")
        await check("Server conversation restored after API restart and F5", True)
        await check(
            "No archive, offer list or persistent suggestions in search",
            await b.js(
                "!document.querySelector('.legacy-presentations,.offer-document-list,.research-chat .research-suggestions')"
            ),
        )
        await b.js("document.querySelector('.project-list > button').click()")
        await b.wait(
            "document.querySelector('.project-item.active .project-open strong')?.textContent==='Новый чат' && !!document.querySelector('#research-query')"
        )
        await check(
            "New chat is immediate with one central composer and no naming modal",
            await b.js(
                "!document.querySelector('.project-form')&&document.querySelectorAll('.research-composer textarea').length===1"
            ),
        )
        projects = await record_owned()
        chat = await b.js("localStorage.getItem('forma-chat-id')")
        p = next(p for p in projects if p["chat_id"] == chat)
        state["browser_project"] = p
        save(ledger, state)
        await send("Красные ручки от 200 шт.")
        await b.wait(
            "document.querySelector('.project-item.active .project-open strong')?.textContent==='Ручки' && document.querySelectorAll('[data-message-id]').length===2"
        )
        await check(
            "Automatic short title and one right-side composer",
            await b.js(
                "document.querySelector('.research-chat textarea')?.id==='research-message'&&document.querySelectorAll('.research-composer textarea').length===1"
            ),
        )
        await b.wait("!!document.querySelector('.product-card input[type=checkbox]')")
        await b.js("document.querySelector('.product-card input[type=checkbox]').click()")
        await b.wait("document.querySelector('.research-selected-count')?.textContent==='1'")
        await b.call("Page.reload", {"ignoreCache": True})
        await b.wait(
            "document.querySelector('.research-selected-count')?.textContent==='1' && document.querySelectorAll('[data-message-id]').length===2"
        )
        await b.js(
            "Array.from(document.querySelectorAll('button')).find(e=>e.textContent.trim()==='Открыть подборку').click()"
        )
        await b.wait("!!document.querySelector('dialog[open]')")
        await b.js(
            "Array.from(document.querySelectorAll('dialog[open] button')).find(e=>e.textContent.includes('Создать предложение')).click()"
        )
        await b.wait("!!document.querySelector('[data-testid=offer-editor] .offer-edit-fields')")
        identifier = await b.js("location.pathname.split('/')[2]")
        state["documents"].append(identifier)
        state["browser_document"] = identifier
        save(ledger, state)
        await check("Selected panel opens explicit primary editor", True)
        await b.wait("!!document.querySelector('[data-testid=owner-stock]')")
        await check(
            "Owner sees numeric inventory and date without warehouse geography",
            await b.js(
                "document.querySelector('[data-testid=owner-stock]').textContent.includes('Данные от')"
            ),
        )
        await b.js("document.querySelector('[data-testid=owner-stock]').scrollIntoView({block:'center'})")
        await b.shot(public / "owner-stock.png")
        await b.js(
            "Array.from(document.querySelectorAll('.offer-toolbar a')).find(e=>e.textContent==='Предложения').click()"
        )
        await b.wait("document.querySelectorAll('.offer-library-row').length>=3")
        await check("Navbar offers lists multiple chats", True)
        for value in ["current", "pulse"]:
            await theme(value)
            for mobile in [False, True]:
                await b.call(
                    "Emulation.setDeviceMetricsOverride",
                    {
                        "width": 390 if mobile else 1440,
                        "height": 844 if mobile else 1000,
                        "deviceScaleFactor": 1,
                        "mobile": mobile,
                    },
                )
                await b.wait("document.documentElement.scrollWidth<=innerWidth")
                if not mobile:
                    await b.wait("document.querySelector('.offer-library .research-top nav').getBoundingClientRect().height<=48")
                await b.wait(
                    "document.querySelector('.offer-library-main').getBoundingClientRect().top>=document.querySelector('.research-top').getBoundingClientRect().bottom"
                )
                await check("Offers layout " + value + (" mobile" if mobile else " desktop"), True)
                await b.shot(public / ("offers-" + value + ("-mobile" if mobile else "-desktop") + ".png"))
        await b.call(
            "Emulation.setDeviceMetricsOverride",
            {"width": 1440, "height": 1000, "deviceScaleFactor": 1, "mobile": False},
        )
        await b.js("document.querySelector('[data-document-id=" + json.dumps(identifier) + "] a').click()")
        await b.wait("!!document.querySelector('[data-testid=offer-editor]')")
        await check(
            "List returns the same primary offer ID",
            await b.js("location.pathname.split('/')[2]") == identifier,
        )
        async with httpx.AsyncClient(
            base_url="http://127.0.0.1:8000", cookies=state["users"][0]["cookies"], timeout=60
        ) as c:
            chat_result = await call(
                c,
                "POST",
                "/api/chats",
                json={"project_name": "Проверка длинной истории", "request_id": str(uuid4())},
            )
            long_project = next(
                p for p in await call(c, "GET", "/api/projects") if p["chat_id"] == chat_result["id"]
            )
            state["projects"].append(long_project["id"])
            state["long_project"] = long_project
            save(ledger, state)
            engine, sessions = create_database(Settings().database_url)
            try:
                from datetime import timedelta

                async with sessions() as db, db.begin():
                    owner = await db.get(Chat, long_project["chat_id"])
                    assert owner.user_id == state["users"][0]["id"]
                    now = utcnow()
                    for n in range(140):
                        db.add(
                            Message(
                                id=str(uuid4()),
                                chat_id=owner.id,
                                role="user" if n % 2 == 0 else "assistant",
                                content=f"Тест пагинации {n:03d}. Это изолированное сообщение приёмки. Проверка прокрутки и сохранения позиции.",
                                created_at=now + timedelta(microseconds=n),
                                structured_operation={"type": "ACCEPTANCE_FIXTURE"},
                            )
                        )
            finally:
                await engine.dispose()
            await navigate(long_project["chat_id"])
            await b.wait("document.querySelectorAll('[data-message-id]').length===50")
            await b.js("document.querySelector('.research-messages').scrollTop=0")
            anchor = await b.js(
                "({id:document.querySelector('[data-message-id]').dataset.messageId,top:document.querySelector('[data-message-id]').getBoundingClientRect().top})"
            )
            await b.js("document.querySelector('.conversation-older').click()")
            await b.wait("document.querySelectorAll('[data-message-id]').length===100")
            top = await b.js(
                "document.querySelector('[data-message-id="
                + json.dumps(anchor["id"])
                + "]').getBoundingClientRect().top"
            )
            await check("Older page preserves scroll anchor", abs(top - anchor["top"]) < 5)
            before = await b.js("document.querySelector('.research-messages').scrollTop")
            await call(
                c,
                "POST",
                f"/api/projects/{long_project['id']}/requests",
                json={"query": "ручки", "request_id": str(uuid4())},
            )
            await b.wait("!!document.querySelector('.conversation-new')", timeout=40)
            after = await b.js("document.querySelector('.research-messages').scrollTop")
            await check("New answer does not move a reader above", abs(after - before) < 5)
            await b.shot(public / "history-anchor.png")
        await navigate(p["chat_id"])
        await b.wait(
            "document.querySelectorAll('[data-message-id]').length===2&&!!document.querySelector('.research-heading')&&!!document.querySelector('.product-card')"
        )
        await check("F5 restores active product grid separately from conversation", True)
        await theme("current")
        await b.shot(public / "chat-current-desktop.png")
        await b.call(
            "Emulation.setDeviceMetricsOverride",
            {"width": 390, "height": 844, "deviceScaleFactor": 1, "mobile": True},
        )
        await b.wait("!document.querySelector('.research-chat')")
        await b.js(
            "document.querySelector('.research-chat-reopen')?.click()"
        )
        await b.wait("!!document.querySelector('dialog[open] #research-message')")
        await check(
            "Mobile mounts one continuation input",
            await b.js("document.querySelectorAll('.research-composer textarea').length===1"),
        )
        await b.shot(public / "chat-current-mobile.png")
        await b.call("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Escape", "code": "Escape"})
        await b.call("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Escape", "code": "Escape"})
        await theme("pulse")
        await b.wait("document.documentElement.scrollWidth<=innerWidth")
        await b.shot(public / "chat-pulse-mobile.png")
        await check("Mobile chat and navbar fit Pulse", True)
        await b.call(
            "Emulation.setDeviceMetricsOverride",
            {"width": 1440, "height": 1000, "deviceScaleFactor": 1, "mobile": False},
        )
        await b.wait(
            "!!document.querySelector('.research-chat #research-message')&&document.querySelectorAll('.research-composer textarea').length===1"
        )
        await b.shot(public / "chat-pulse-desktop.png")
        await check("Desktop chat and single composer fit Pulse", True)
        # Isolated browser context has no owner cookie; no key is persisted in public evidence.
        async with httpx.AsyncClient() as discovery:
            version = (await discovery.get("http://127.0.0.1:9226/json/version")).json()
        control = Browser()
        control.number = 0
        control.ws = await websockets.connect(version["webSocketDebuggerUrl"])
        context = await control.call("Target.createBrowserContext")
        target = await control.call(
            "Target.createTarget", {"url": "about:blank", "browserContextId": context["browserContextId"]}
        )
        async with httpx.AsyncClient() as discovery:
            tabs = (await discovery.get("http://127.0.0.1:9226/json/list")).json()
        guest = Browser()
        guest.number = 0
        guest.ws = await websockets.connect(
            next(t["webSocketDebuggerUrl"] for t in tabs if t["id"] == target["targetId"]),
            max_size=30_000_000,
        )
        try:
            await guest.call("Page.enable")
            await guest.call("Network.enable")
            await guest.call("Page.navigate", {"url": "http://127.0.0.1:3000" + state["published"]["path"]})
            await guest.wait("!!document.querySelector('.offer-document')", timeout=60)
            await check(
                "Guest web contains no owner stock controls",
                await guest.js("!document.querySelector('[data-testid=owner-stock]')"),
            )
            await check(
                "Public link grants no owner API access",
                await guest.js("fetch('/api/offers').then(r=>r.status)") == 401,
            )
            await guest.shot(public / "guest.png")
        finally:
            await guest.ws.close()
            await control.call(
                "Target.disposeBrowserContext", {"browserContextId": context["browserContextId"]}
            )
            await control.ws.close()
        save(
            public / "browser.json",
            {
                "status": "PASS",
                "checks": checks,
                "browser": "installed Microsoft Edge",
                "fixture_history_messages": 140,
            },
        )
        print("Edge acceptance", len(checks), "checks")
    finally:
        await b.ws.close()
