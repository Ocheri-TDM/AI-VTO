"""Installed Edge with independent incognito contexts and the real local build.

External HTTPS API is checked separately. Never calls a local build a Vercel deployment.
"""

import asyncio
import json
import math
import socket
import subprocess

import httpx
import websockets
from check_chat_workflow import save
from check_palette_browser import Browser

ORIGIN = "http://127.0.0.1:3000"
INSTRUMENT = r"""(()=>{
  window.__writes=[];window.__dom=[];window.__sources={created:0,live:0};window.__network=[];window.__sourceStatus=[];
  const fetchOriginal=window.fetch.bind(window);
  window.fetch=async(...args)=>{
    const url=String(args[0] instanceof Request?args[0].url:args[0]),method=args[1]?.method||'GET';
    window.__network.push({kind:url.includes('/notifications/events')?'stream':url.includes('/client-selection')?'feedback':method,at:Date.now()});
    if(window.__holdSave&&method==='PATCH'&&url.startsWith('/api/offers/')){while(window.__holdSave)await new Promise(r=>setTimeout(r,20));}
    if(window.__failChoice&&method==='PUT'&&url.endsWith('/selection')){window.__failChoice=false;throw Error('Acceptance offline write');}
    const response=await fetchOriginal(...args);
    if(method==='PUT'&&url.endsWith('/selection')){const body=await response.clone().json();window.__writes.push({status:response.status,version:body.version,commit:body.commit_observed_at,at:Date.now()});}
    return response;
  };
  const NativeSource=window.EventSource;
  window.EventSource=class extends NativeSource{
    constructor(...args){super(...args);window.__sources.created++;window.__sources.live++;this.__closed=false;for(const kind of ['error','stream.ready','stream.renew','auth.required','stream.unavailable'])this.addEventListener(kind,()=>window.__sourceStatus.push({kind,at:Date.now()}));}
    close(){if(!this.__closed){this.__closed=true;window.__sources.live--;}super.close();}
  };
  new MutationObserver(()=>{const panel=document.querySelector('[data-client-selection-version]');if(panel){const version=Number(panel.dataset.clientSelectionVersion);if(!window.__dom.length||window.__dom.at(-1).version!==version)window.__dom.push({version,at:Date.now(),items:panel.querySelectorAll('[data-client-item]').length});}}).observe(document,{subtree:true,attributes:true,childList:true});
})();"""


class Page(Browser):
    async def attach(self, identifier):
        async with httpx.AsyncClient() as client:
            targets = (await client.get("http://127.0.0.1:9226/json/list")).json()
        target = next(t for t in targets if t["id"] == identifier)
        self.ws = await websockets.connect(
            target["webSocketDebuggerUrl"], max_size=30_000_000
        )
        self.number = 0
        await self.call("Page.enable")
        await self.call("Network.enable")
        await self.call("Emulation.setFocusEmulationEnabled", {"enabled": True})
        await self.call(
            "Emulation.setDeviceMetricsOverride",
            {"width": 1440, "height": 1000, "deviceScaleFactor": 1, "mobile": False},
        )
        await self.call("Page.addScriptToEvaluateOnNewDocument", {"source": INSTRUMENT})

    async def go(self, path):
        await self.call("Page.navigate", {"url": ORIGIN + path})


async def run(state, root, ledger, public):
    public.mkdir(parents=True, exist_ok=True)
    checks, samples = [], []

    def check(label, valid, **details):
        checks.append(
            {"check": label, "status": "PASS" if valid else "FAIL", **details}
        )
        save(
            public / "browser.json",
            {
                "origin": ORIGIN,
                "checks": checks,
                "latency_samples": samples,
                "external_new_ui": "NOT DEPLOYED",
            },
        )
        print(("PASS " if valid else "FAIL ") + label, flush=True)
        assert valid, label

    if not state.get("edge_pid"):
        with socket.socket() as probe:
            assert probe.connect_ex(("127.0.0.1", 9226)) != 0
        profile = root / ".local" / ("client-selection-edge-" + state["run"])
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
                version = (
                    await client.get("http://127.0.0.1:9226/json/version")
                ).json()
                break
            except httpx.HTTPError:
                await asyncio.sleep(0.2)
        else:
            raise TimeoutError("Edge startup")
    control = Browser()
    control.ws = await websockets.connect(
        version["webSocketDebuggerUrl"], max_size=30_000_000
    )
    control.number = 0
    contexts, pages = [], []

    async def page(context=None, cookies=None):
        if context is None:
            context = (await control.call("Target.createBrowserContext"))[
                "browserContextId"
            ]
            contexts.append(context)
        target = await control.call(
            "Target.createTarget",
            {"url": "about:blank", "browserContextId": context, "newWindow": True},
        )
        b = Page()
        await b.attach(target["targetId"])
        b.context = context
        pages.append(b)
        if cookies:
            await b.call(
                "Network.setCookies",
                {
                    "cookies": [
                        {
                            "name": k,
                            "value": v,
                            "url": ORIGIN,
                            "httpOnly": True,
                            "sameSite": "Lax",
                            "secure": True,
                        }
                        for k, v in cookies.items()
                    ]
                },
            )
        return b

    async def selected(b, count):
        await b.wait(
            f"document.querySelectorAll('.offer-selection-actions input:checked').length==={count}&&document.querySelector('.offer-selection-notice [role=status]')?.textContent==='Выбор сохранён'"
        )

    async def click_choice(b, index):
        return await b.js(
            f"(()=>{{const at=Date.now();document.querySelectorAll('.offer-selection-actions input')[{index}].click();return at;}})()"
        )

    async def cursor(b):
        return await b.js(
            "Number(document.querySelector('[data-client-selection-version]')?.dataset.clientSelectionVersion||0)"
        )

    async def measure(b, index, manager):
        before = await cursor(manager)
        at = await click_choice(b, index)
        await manager.wait(
            f"Number(document.querySelector('[data-client-selection-version]')?.dataset.clientSelectionVersion)>{before}",
            timeout=20,
        )
        await b.wait(
            "window.__writes.at(-1)?.status===200&&document.querySelector('.offer-selection-notice [role=status]')?.textContent==='Выбор сохранён'"
        )
        event = await manager.js(f"window.__dom.find(e=>e.version>{before})")
        commit = await b.js("Date.parse(window.__writes.at(-1).commit)")
        samples.append(
            {
                "click_to_manager_dom_ms": event["at"] - at,
                "commit_observed_to_dom_ms": event["at"] - commit,
            }
        )
        check(
            "confirmed choice reaches manager DOM",
            event["at"] >= at and event["at"] >= commit,
        )

    offer = state["offers"][0]
    try:
        manager = await page(cookies=state["users"][0]["cookies"])
        manager2 = await page(cookies=state["users"][1]["cookies"])
        guest = await page()
        await manager.go("/offers/" + offer["document"] + "/edit")
        await manager.wait(
            "!!document.querySelector('[data-client-selection-version]')&&Number(document.querySelector('[data-client-selection-version]').dataset.clientSelectionVersion)>0"
        )
        await manager2.go("/offers/" + state["offers"][2]["document"] + "/edit")
        await manager2.wait(
            "!!document.querySelector('[data-client-selection-version]')"
        )
        await guest.go("/offers/" + offer["token"])
        await selected(guest, 0)
        check(
            "fresh incognito guest has three readonly positions and no selected marks",
            await guest.js(
                "document.querySelectorAll('.offer-selection-actions input').length===3&&!document.querySelector('.offer-edit-fields')"
            ),
        )
        check(
            "manager preview has no client choice controls",
            await manager.js("!document.querySelector('.offer-selection-actions')"),
        )
        check(
            "manager stream is one app subscription",
            await manager.js("window.__sources.live===1"),
            visibility=await manager.js("document.visibilityState"),
        )
        foreign_before = await cursor(manager2)
        for index in [0, 1, 2]:
            await measure(guest, index, manager)
        await selected(guest, 3)
        check(
            "public counter counts three positions",
            await guest.js(
                "document.querySelector('.offer-selection-count').textContent==='Выбрано: 3'"
            ),
        )
        check(
            "another manager sees no foreign change",
            await cursor(manager2) == foreign_before,
        )
        await guest.call("Page.reload")
        await selected(guest, 3)
        check("real HttpOnly Secure visitor cookie survives F5", True)
        cookie = next(
            c
            for c in (await guest.call("Network.getAllCookies"))["cookies"]
            if c["name"].startswith("offer_visitor_")
        )
        check(
            "browser cookie is Secure HttpOnly and path scoped",
            cookie["secure"]
            and cookie["httpOnly"]
            and cookie["path"].endswith("/selection"),
        )
        await manager.call("Page.reload")
        await manager.wait(
            "document.querySelectorAll('.client-feedback-visitor').length>=3"
        )
        check(
            "manager F5 restores separate visitor lists and unread badge",
            await manager.js("!!document.querySelector('.notification-count')"),
        )
        guest2 = await page()
        await guest2.go("/offers/" + offer["token"])
        await selected(guest2, 0)
        await measure(guest2, 0, manager)
        await selected(guest2, 1)
        for index in [1, 1, 2, 2]:
            await measure(guest2, index, manager)
        await selected(guest2, 1)
        check(
            "independent incognito visitor cannot clear first visitor",
            await guest.js(
                "document.querySelectorAll('.offer-selection-actions input:checked').length===3"
            ),
        )
        tab = await page(guest.context)
        await tab.go("/offers/" + offer["token"])
        await selected(tab, 3)
        await click_choice(tab, 2)
        await selected(tab, 2)
        await selected(guest, 2)
        check(
            "same-browser tabs share one visitor and synchronize BroadcastChannel", True
        )
        await asyncio.gather(click_choice(tab, 0), click_choice(guest, 1))
        await selected(guest, 0)
        await selected(tab, 0)
        check("two tabs changing different lines retain both desired removals", True)
        await manager.wait(
            "document.querySelector('.client-feedback-visitor>p')?.textContent.includes('Выбранных позиций нет')"
        )
        check("manager sees that the latest visitor removed all marks", True)
        await guest.js(
            "(()=>{const e=document.querySelector('.offer-selection-actions input');e.click();e.click();e.click();})()"
        )
        await selected(guest, 1)
        await selected(tab, 1)
        check("rapid choose/remove/choose ends selected in both tabs", True)
        await guest.js("window.__failChoice=true")
        await click_choice(guest, 1)
        await guest.wait(
            "document.querySelector('.offer-selection-notice [role=status]').textContent.includes('Acceptance offline write')"
        )
        check(
            "failed optimistic action is visibly unconfirmed",
            await guest.js(
                "document.querySelectorAll('.offer-selection-actions input:checked').length===2&&!!document.querySelector('.offer-selection-actions small')"
            ),
        )
        await guest.click("Не удалось сохранить. Повторить")
        await selected(guest, 2)
        check("explicit retry persists original pending intent", True)
        # Keep a real draft unsaved while client events arrive. Only this task's
        # PATCH is held before network dispatch, not a fake server response.
        await manager.js("window.__holdSave=true")
        price = await manager.js(
            "(Number(document.querySelector('.offer-price-inputs input').value)+1.23).toFixed(2)"
        )
        await manager.js(
            "(()=>{const e=document.querySelector('.offer-price-inputs input');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(e,"
            + json.dumps(price)
            + ");e.dispatchEvent(new Event('input',{bubbles:true}));})()"
        )
        await asyncio.sleep(0.8)
        await measure(guest2, 1, manager)
        check(
            "SSE does not overwrite unsaved manager price",
            await manager.js(
                "document.querySelector('.offer-price-inputs input').value==="
                + json.dumps(price)
                + "&&document.querySelector('.offer-manager-panel').textContent.includes('Сохраняю')"
            ),
        )
        await manager.js("window.__holdSave=false")
        await manager.wait(
            "document.querySelector('.offer-manager-panel>div>strong').textContent==='Черновик сохранён'"
        )
        # Guard navigation to another offer while the current form is dirty.
        await manager.js("window.__holdSave=true")
        await manager.js(
            "(()=>{const e=document.querySelector('.offer-price-inputs input');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(e,(Number(e.value)+1).toFixed(2));e.dispatchEvent(new Event('input',{bubbles:true}));})()"
        )
        await asyncio.sleep(0.8)
        await manager.js("document.querySelector('.notification-bell').click()")
        await manager.wait("!!document.querySelector('.notification-sheet')")
        target = await manager.js(
            "fetch('/api/notifications').then(r=>r.json()).then(p=>p.items.find(i=>i.document_id==="
            + json.dumps(state["offers"][1]["document"])
            + ")?.id)"
        )
        assert target
        await manager.js(
            "setTimeout(()=>document.querySelector('[data-notification-id=\""
            + target
            + "\"] button').click(),0);true"
        )
        await asyncio.sleep(0.2)
        await manager.call("Page.handleJavaScriptDialog", {"accept": False})
        check(
            "notification navigation respects unsaved draft and Cancel",
            await manager.js(
                "location.pathname==="
                + json.dumps("/offers/" + offer["document"] + "/edit")
                + "&&!!document.querySelector('.notification-sheet')"
            ),
        )
        await manager.js("window.__holdSave=false")
        await manager.wait(
            "document.querySelector('.offer-manager-panel>div>strong').textContent==='Черновик сохранён'"
        )
        await manager.call(
            "Input.dispatchKeyEvent",
            {"type": "keyDown", "key": "Escape", "windowsVirtualKeyCode": 27},
        )
        await manager.wait("!document.querySelector('.notification-sheet')")
        # Disconnection is actual CDP network-offline, not a fake SSE message.
        await manager.call(
            "Network.emulateNetworkConditions",
            {
                "offline": True,
                "latency": 0,
                "downloadThroughput": 0,
                "uploadThroughput": 0,
            },
        )
        await manager.js("document.querySelector('.notification-bell').click()")
        await manager.wait(
            "document.querySelector('[data-connection]')?.dataset.connection==='offline'||document.querySelector('[data-connection]')?.dataset.connection==='polling'"
        )
        check(
            "disconnected notification center does not claim live",
            await manager.js(
                "document.querySelector('[data-connection]').dataset.connection!=='live'"
            ),
        )
        await click_choice(guest2, 2)
        await selected(guest2, 3)
        before = await cursor(manager)
        await manager.call(
            "Network.emulateNetworkConditions",
            {
                "offline": False,
                "latency": 0,
                "downloadThroughput": -1,
                "uploadThroughput": -1,
            },
        )
        await manager.wait(
            f"Number(document.querySelector('[data-client-selection-version]').dataset.clientSelectionVersion)>{before}"
        )
        try:
            await manager.wait(
                "document.querySelector('[data-connection]')?.dataset.connection==='live'"
            )
        except TimeoutError:
            save(
                public / "reconnect-debug.json",
                await manager.js(
                    "({online:navigator.onLine,visibility:document.visibilityState,status:document.querySelector('[data-connection]')?.dataset.connection,sources:window.__sources,events:window.__sourceStatus,feedbackCursor:document.querySelector('[data-client-selection-version]')?.dataset.clientSelectionVersion})"
                ),
            )
            raise
        check(
            "reconnect recovers offline choice without read acknowledgement",
            await manager.js(
                "document.querySelectorAll('.notification-list .is-unread').length>0"
            ),
        )
        await manager.js(
            "(()=>{const s=document.createElement('style');s.id='acceptance-redact';s.textContent='.offer-share-field input{color:transparent!important;text-shadow:none!important}';document.head.append(s);})()"
        )
        await manager.shot(public / "manager-notifications.png")
        await manager.js("document.getElementById('acceptance-redact').remove()")
        await manager.js(
            "Array.from(document.querySelector('.notification-list article').querySelectorAll('button')).find(e=>e.textContent==='Прочитано').click()"
        )
        await manager.wait(
            "!document.querySelector('.notification-list article').classList.contains('is-unread')"
        )
        check("explicit read updates the viewed notification group", True)
        await manager.call(
            "Input.dispatchKeyEvent",
            {"type": "keyDown", "key": "Escape", "windowsVirtualKeyCode": 27},
        )
        await manager.wait("!document.querySelector('.notification-sheet')")
        for theme in ["current", "pulse"]:
            await manager.go("/offers")
            await manager.wait(
                "!!document.querySelector('.app-navbar')&&!!document.querySelector('[data-client-interest]')"
            )
            await manager.js(
                "document.querySelector('.theme-switch .ui-select-trigger').click()"
            )
            await manager.wait(
                "!!document.querySelector('.theme-switch .ui-select-popup:popover-open')"
            )
            await manager.js(
                "Array.from(document.querySelectorAll('.theme-switch [role=option]')).find(e=>e.textContent.trim()==="
                + json.dumps("Pulse" if theme == "pulse" else "Текущая")
                + ").click()"
            )
            await manager.wait(
                "document.documentElement.dataset.siteTheme===" + json.dumps(theme)
            )
            for width in [1440, 390]:
                await manager.call(
                    "Emulation.setDeviceMetricsOverride",
                    {
                        "width": width,
                        "height": 844 if width == 390 else 1000,
                        "deviceScaleFactor": 1,
                        "mobile": width == 390,
                    },
                )
                await asyncio.sleep(0.25)
                geometry = await manager.js(
                    "(()=>{const b=document.querySelector('.notification-bell').getBoundingClientRect();return {overflow:document.documentElement.scrollWidth>innerWidth+1,bellWidth:b.width,bellHeight:b.height,font:getComputedStyle(document.body).fontFamily,removedTitle:document.querySelectorAll('[data-testid=header-project-title],.app-navbar-context').length};})()"
                )
                check(
                    f"{theme} {width}px navbar, Geist and interest indicator",
                    not geometry["overflow"]
                    and geometry["bellWidth"] >= 44
                    and geometry["bellHeight"] >= 44
                    and geometry["removedTitle"] == 0
                    and "Geist" in geometry["font"],
                    geometry=geometry,
                )
                await manager.shot(public / f"offers-{theme}-{width}.png")
        await guest.call(
            "Emulation.setDeviceMetricsOverride",
            {"width": 390, "height": 844, "deviceScaleFactor": 1, "mobile": True},
        )
        await guest.js("scrollTo(0,0)")
        check(
            "guest mobile counter and readonly document fit 390px",
            await guest.js("document.documentElement.scrollWidth<=innerWidth+1"),
        )
        await guest.shot(public / "guest-mobile.png")
        await guest.js(
            "document.querySelector('.offer-selection-actions').scrollIntoView({block:'center'})"
        )
        await guest.shot(public / "guest-choice-mobile.png")
        await guest.call("Emulation.setEmulatedMedia", {"media": "print"})
        check(
            "printed document omits checkbox and client interest controls",
            await guest.js(
                "getComputedStyle(document.querySelector('.offer-selection-actions')).display==='none'&&getComputedStyle(document.querySelector('.offer-selection-notice')).display==='none'"
            ),
        )
        external = await page()
        await external.call(
            "Page.navigate",
            {
                "url": "https://souvenir-studio-three.vercel.app/offers/"
                + offer["token"]
            },
        )
        await external.wait("!!document.querySelector('.offer-product')")
        check("real external incognito can read the published offer", True)
        external_controls = await external.js(
            "document.querySelectorAll('.offer-selection-actions input').length"
        )
        check(
            "external frontend deployment gap measured, not called UI PASS",
            external_controls == 0,
            deployed_controls=external_controls,
            result="new UI NOT DEPLOYED; local UI verified separately",
        )
        await manager2.go("/offers")
        await manager2.wait("!!document.querySelector('.notification-bell')")
        await manager2.js(
            "Array.from(document.querySelector('.app-navbar').querySelectorAll('button')).find(e=>e.textContent==='Выйти').click()"
        )
        await manager2.wait(
            "!document.querySelector('.notification-bell')&&window.__sources.live===0"
        )
        check("logout closes account stream and clears notification projection", True)
        distributions = {}
        for key in ["click_to_manager_dom_ms", "commit_observed_to_dom_ms"]:
            values = sorted(s[key] for s in samples)
            distributions[key] = {
                "n": len(values),
                "p50": values[len(values) // 2],
                "p95": values[max(0, math.ceil(len(values) * 0.95) - 1)],
                "max": max(values),
            }
        save(
            public / "browser.json",
            {
                "origin": ORIGIN,
                "checks": checks,
                "latency": distributions,
                "latency_samples": samples,
                "clock": "browser Date.now and server commit-observed timestamp on same local Windows host",
                "external_new_ui": "NOT DEPLOYED",
            },
        )
    finally:
        for b in pages:
            await b.ws.close()
        for context in contexts:
            await control.call(
                "Target.disposeBrowserContext", {"browserContextId": context}
            )
        await control.ws.close()
