"""Installed Edge, isolated contexts, exact disposable offers; no capability output."""

import asyncio
import json
import socket
import subprocess

import httpx
import websockets
from check_chat_workflow import save
from check_client_selection_browser import ORIGIN, Page
from check_palette_browser import Browser


async def run(state, root, ledger, public, phase):
    public.mkdir(parents=True, exist_ok=True)
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
    contexts, pages, checks = [], [], []

    def check(label, valid, **details):
        checks.append(
            {"check": label, "status": "PASS" if valid else "FAIL", **details}
        )
        save(
            public / (phase + ".json"),
            {"checks": checks, "new_vercel_ui": "NOT DEPLOYED"},
        )
        print(("PASS " if valid else "FAIL ") + label, flush=True)
        assert valid, label

    async def page(owner=None, origin=ORIGIN):
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
        if owner is not None:
            await b.call(
                "Network.setCookies",
                {
                    "cookies": [
                        {
                            "name": k,
                            "value": v,
                            "url": origin,
                            "httpOnly": True,
                            "sameSite": "Lax",
                            "secure": True,
                        }
                        for k, v in state["users"][owner]["cookies"].items()
                    ]
                },
            )
        return b

    offer = state["offers"][0]
    try:
        manager = await page(0)
        await manager.go("/offers/" + offer["document"] + "/edit")
        await manager.wait(
            "!!document.querySelector('.offer-publish-actions')&&!!document.querySelector('.client-feedback-panel header>p')"
        )
        if phase == "before":
            for index in [0, 1]:
                guest = await page()
                await guest.go("/offers/" + offer["token"])
                await guest.wait(
                    "document.querySelectorAll('.offer-selection-actions input').length===3&&!document.querySelector('.offer-selection-actions input').disabled"
                )
                await guest.js(
                    f"document.querySelectorAll('.offer-selection-actions input')[{index}].click()"
                )
                await guest.wait(
                    "document.querySelector('.offer-selection-actions input:checked')&&document.querySelector('.offer-selection-notice [role=status]')?.textContent==='Выбор сохранён'"
                )
            await manager.wait(
                "document.querySelectorAll('.client-feedback-visitor').length>=2"
            )
            await manager.js(
                "document.querySelector('.client-feedback-panel details').open=true;document.querySelector('.offer-share-field input').style.visibility='hidden';document.querySelector('.client-feedback-panel').scrollIntoView({block:'center'})"
            )
            await manager.shot(public / "before-selection.png")
            await manager.js("scrollTo(0,0)")
            await manager.shot(public / "before-publication.png")
            await manager.go("/")
            await manager.wait("!!document.querySelector('.studio-button.primary')")
            await manager.js("document.documentElement.dataset.siteTheme='pulse'")
            await manager.shot(public / "before-buttons.png")
            check(
                "original UI captured with URL field redacted and two separate visitors",
                True,
            )
        else:
            await after(manager, page, control, state, public, check)
    finally:
        for b in pages:
            await b.ws.close()
        for context in contexts:
            await control.call(
                "Target.disposeBrowserContext", {"browserContextId": context}
            )
        await control.ws.close()


async def after(manager, page, control, state, public, check):
    offer = state["offers"][0]
    path = "/offers/" + offer["document"] + "/edit"
    api = "/api/offers/" + offer["document"]

    async def doc(b):
        return await b.js("fetch(" + json.dumps(api) + ").then(r=>r.json())")

    async def field(b, selector, value):
        await b.js(
            "(()=>{const e=document.querySelector("
            + json.dumps(selector)
            + ");Object.getOwnPropertyDescriptor(e instanceof HTMLTextAreaElement?HTMLTextAreaElement.prototype:HTMLInputElement.prototype,'value').set.call(e,"
            + json.dumps(value)
            + ");e.dispatchEvent(new Event('input',{bubbles:true}));})()"
        )

    async def choice(guest, index, count):
        await guest.js(
            f"document.querySelectorAll('.offer-selection-actions input')[{index}].click()"
        )
        await guest.wait(
            f"document.querySelectorAll('.offer-selection-actions input:checked').length==={count}&&document.querySelector('.offer-selection-notice [role=status]')?.textContent==='Выбор сохранён'"
        )

    async def clipboard(b, origin):
        await control.call(
            "Browser.grantPermissions",
            {
                "browserContextId": b.context,
                "origin": origin,
                "permissions": ["clipboardReadWrite", "clipboardSanitizedWrite"],
            },
        )
        await b.call("Page.bringToFront")
        # Never read the user's pre-existing clipboard. Read only after our own
        # isolated offer's successful native write, and return equality, not bytes.
        await b.js(
            "document.querySelector('.offer-publish-actions button:not(.offer-primary)').click()"
        )
        await b.wait(
            "document.querySelector('.offer-manager-panel [role=status]')?.textContent==='Ссылка скопирована'"
        )
        return await b.js(
            "navigator.clipboard.readText().then(value=>value===document.querySelector('.offer-publish-actions a').href&&value.startsWith('https://'))"
        )

    before = await doc(manager)
    check(
        "no history, session explanation, URL field or empty replacement",
        await manager.js(
            "!document.querySelector('.client-feedback-panel details,.client-feedback-identity,.offer-share-field,input[aria-label=\"Клиентская ссылка\"]')&&!document.querySelector('.client-feedback-panel').textContent.includes('История изменений выбора')&&!document.body.innerText.includes('Посетители — браузерные сессии')"
        ),
    )
    check(
        "separate visitor lists retain images, SKU, revision and counts",
        await manager.js(
            "document.querySelectorAll('.client-feedback-visitor').length>=2&&[...document.querySelectorAll('.client-feedback-items article')].every(e=>e.querySelector('img')&&e.textContent.includes('Артикул:')&&e.textContent.includes('Версия'))"
        ),
    )
    check(
        "native local secure-context Clipboard copies current HTTPS capability",
        await clipboard(manager, ORIGIN),
    )
    # Refusal and pending Promise use transport spies only for those error paths.
    await manager.js(
        "window.__clipboardOriginal=navigator.clipboard.writeText.bind(navigator.clipboard);navigator.clipboard.writeText=()=>Promise.reject(new DOMException('denied','NotAllowedError'))"
    )
    await manager.click("Копировать ссылку")
    await manager.wait(
        "document.querySelector('.offer-copy-error')?.textContent.includes('Повторите попытку')"
    )
    check(
        "Clipboard refusal has no false success, URL field, prompt or alert",
        await manager.js(
            "!document.querySelector('.offer-manager-panel [role=status]')?.textContent.includes('Ссылка скопирована')&&!document.querySelector('.offer-share-field,input[readonly]')"
        ),
    )
    await manager.shot(public / "copy-error.png")
    await manager.js(
        "navigator.clipboard.writeText=()=>new Promise(resolve=>window.__releaseCopy=resolve)"
    )
    await manager.click("Копировать ссылку")
    await manager.wait("!!window.__releaseCopy")
    check(
        "pending Clipboard Promise blocks repeat and reports no success",
        await manager.js(
            "document.querySelector('.offer-publish-actions button[aria-busy=true]').disabled&&!document.querySelector('.offer-manager-panel [role=status]')?.textContent.includes('Ссылка скопирована')"
        ),
    )
    await manager.js(
        "window.__releaseCopy();navigator.clipboard.writeText=window.__clipboardOriginal"
    )
    await manager.wait(
        "!document.querySelector('.offer-publish-actions button[aria-busy=true]')"
    )
    check("native copy retry succeeds", await clipboard(manager, ORIGIN))
    after_copy = await doc(manager)
    check(
        "copy retains token, expiry, document, revision and draft", before == after_copy
    )

    guest = await page()
    guest2 = await page()
    for b in (guest, guest2):
        await b.go("/offers/" + offer["token"])
        await b.wait(
            "document.querySelectorAll('.offer-selection-actions input').length===3&&!document.querySelector('.offer-selection-actions input').disabled"
        )
    initial = await manager.js(
        "document.querySelectorAll('.client-feedback-visitor').length"
    )
    await choice(guest, 0, 1)
    await manager.wait(
        f"document.querySelectorAll('.client-feedback-visitor').length>{initial}"
    )
    await manager.js("document.querySelector('.notification-bell').click()")
    await manager.wait(
        "document.querySelectorAll('.notification-list .is-unread').length>0"
    )
    check("guest selection updates manager and bell without F5", True)
    await manager.call(
        "Input.dispatchKeyEvent",
        {"type": "keyDown", "key": "Escape", "windowsVirtualKeyCode": 27},
    )
    await choice(guest2, 1, 1)
    await manager.wait(
        f"document.querySelectorAll('.client-feedback-visitor').length>{initial + 1}"
    )
    await choice(guest, 0, 0)
    await manager.wait(
        "[...document.querySelectorAll('.client-feedback-visitor>p')].some(e=>e.textContent.includes('Выбранных позиций нет'))"
    )
    check(
        "unselect updates count and keeps second visitor independent",
        await guest2.js(
            "document.querySelectorAll('.offer-selection-actions input:checked').length===1"
        ),
    )
    await guest2.call("Page.reload")
    await guest2.wait(
        "document.querySelectorAll('.offer-selection-actions input:checked').length===1"
    )
    await manager.call(
        "Network.emulateNetworkConditions",
        {"offline": True, "latency": 0, "downloadThroughput": 0, "uploadThroughput": 0},
    )
    await asyncio.sleep(1)
    await choice(guest2, 2, 2)
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
        "[...document.querySelectorAll('.client-feedback-visitor>p')].some(e=>e.textContent.includes('Отмечено позиций: 2'))",
        timeout=50,
    )
    check(
        "F5 persists choices; reconnect restores current state without history UI",
        await manager.js("!document.querySelector('.client-feedback-panel details')"),
    )
    await manager.js(
        "scrollTo(0,document.querySelector('.client-feedback-panel').getBoundingClientRect().top+scrollY-95)"
    )
    await manager.shot(public / "after-selection.png")
    await manager.js("scrollTo(0,0)")
    await manager.shot(public / "after-publication.png")

    # Measure actual computed colors, filters and ancestor opacity in every state.
    await manager.call("DOM.enable")
    await manager.call("CSS.enable")
    metrics = []

    async def measure(b, selector, name, theme):
        await b.call("DOM.enable")
        await b.call("CSS.enable")
        root_id = (await b.call("DOM.getDocument"))["root"]["nodeId"]
        node = (
            await b.call("DOM.querySelector", {"nodeId": root_id, "selector": selector})
        )["nodeId"]
        for label, pseudo in [
            ("normal", []),
            ("hover", ["hover"]),
            ("pressed", ["hover", "active"]),
            ("focus", ["focus-visible"]),
        ]:
            await b.call(
                "CSS.forcePseudoState", {"nodeId": node, "forcedPseudoClasses": pseudo}
            )
            await asyncio.sleep(0.2)
            result = await b.js(
                r"""(()=>{const e=document.querySelector(SELECTOR),s=getComputedStyle(e);
              const rgb=x=>x.match(/[\d.]+/g).slice(0,3).map(Number),lum=c=>rgb(c).map(v=>{v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4}).reduce((v,c,i)=>v+c*[.2126,.7152,.0722][i],0),ratio=(a,b)=>(Math.max(lum(a),lum(b))+.05)/(Math.min(lum(a),lum(b))+.05);
              let opacity=1,filters=[];for(let p=e;p;p=p.parentElement){const c=getComputedStyle(p);opacity*=Number(c.opacity);if(c.filter!=='none')filters.push(c.filter);}
              let adjacent=getComputedStyle(document.body).backgroundColor;for(let p=e.parentElement;p;p=p.parentElement){const c=getComputedStyle(p).backgroundColor;if(c!=='rgba(0, 0, 0, 0)'&&c!=='transparent'){adjacent=c;break;}}
              return {color:s.color,background:s.backgroundColor,contrast:ratio(s.color,s.backgroundColor),font:s.fontFamily,size:s.fontSize,weight:s.fontWeight,opacity,filters,height:e.getBoundingClientRect().height,focus:s.outlineColor,focusWidth:s.outlineWidth,focusContrast:ratio(s.outlineColor,adjacent),adjacent,icon:e.querySelector('svg')?getComputedStyle(e.querySelector('svg')).color:null};})()""".replace(
                    "SELECTOR", json.dumps(selector)
                )
            )
            metrics.append({"action": name, "theme": theme, "state": label, **result})
            check(
                f"{theme} {name} {label} computed contrast",
                result["contrast"] >= 4.5
                and result["opacity"] == 1
                and not result["filters"],
                contrast=round(result["contrast"], 3),
                colors=[result["color"], result["background"]],
            )
            if label == "focus":
                check(
                    f"{theme} {name} visible focus",
                    float(result["focusWidth"][:-2]) >= 2
                    and result["focusContrast"] >= 3,
                    focus=result["focus"],
                    adjacent=result["adjacent"],
                    ratio=result["focusContrast"],
                )
            if theme == "pulse":
                check(
                    f"{name} white text and matching icon",
                    result["color"] == "rgb(255, 255, 255)"
                    and (not result["icon"] or result["icon"] == result["color"]),
                )
        await b.call(
            "CSS.forcePseudoState", {"nodeId": node, "forcedPseudoClasses": []}
        )

    await measure(manager, ".offer-primary", "update publication", "pulse")
    # A style fixture only: no publish request or historical export regeneration.
    await manager.js(
        "document.querySelector('.offer-primary').disabled=true;document.querySelector('.offer-primary').setAttribute('aria-busy','true')"
    )
    check(
        "loading publication stays readable and disabled",
        await manager.js(
            "(()=>{const e=document.querySelector('.offer-primary'),s=getComputedStyle(e);return e.disabled&&s.color==='rgb(255, 255, 255)'&&s.opacity==='1'})()"
        ),
    )
    await manager.js(
        "document.querySelector('.offer-primary').disabled=false;document.querySelector('.offer-primary').setAttribute('aria-busy','false')"
    )
    for width, zoom in [(1440, 1), (390, 1), (1152, 1.25), (960, 1.5)]:
        await manager.call(
            "Emulation.setDeviceMetricsOverride",
            {
                "width": width,
                "height": 844,
                "deviceScaleFactor": zoom,
                "mobile": width == 390,
            },
        )
        result = await manager.js(
            "(()=>{const e=document.querySelector('.offer-primary'),b=e.getBoundingClientRect();return {overflow:document.documentElement.scrollWidth>innerWidth+1,clipped:e.scrollWidth>e.clientWidth+1||e.scrollHeight>e.clientHeight+1,height:b.height}})()"
        )
        check(
            f"editor {width}px / {zoom} scale long label fits",
            not result["overflow"] and not result["clipped"],
            geometry=result,
        )
        await manager.shot(public / f"publication-{width}.png")
    await manager.call(
        "Emulation.setDeviceMetricsOverride",
        {"width": 1440, "height": 1000, "deviceScaleFactor": 1, "mobile": False},
    )
    for theme in ["pulse", "current"]:
        await manager.go("/offers")
        await manager.wait(
            "!!document.querySelector('.offer-library-row .studio-button.primary')"
        )
        await manager.js(
            "document.documentElement.dataset.siteTheme=" + json.dumps(theme)
        )
        await measure(
            manager, ".offer-library-row .studio-button.primary", "open offer", theme
        )
        await manager.shot(public / f"offers-{theme}.png")
        await manager.go("/")
        await manager.wait(
            "!!document.querySelector('.project-list>.studio-button.primary')"
        )
        await manager.js(
            "document.documentElement.dataset.siteTheme=" + json.dumps(theme)
        )
        await measure(
            manager, ".project-list>.studio-button.primary", "new chat", theme
        )
        await manager.shot(public / f"buttons-{theme}.png")
    # Actual new-chat and first-message HTTP workflow; record its exact new project
    # immediately so cleanup never depends on guessing ownership.
    previous_projects = await manager.js(
        "document.querySelectorAll('.project-item').length"
    )
    await manager.click("Новый чат")
    await manager.wait(
        f"document.querySelectorAll('.project-item').length>{previous_projects}&&!!document.querySelector('.research-composer textarea')&&document.querySelector('.project-list>.studio-button.primary')?.getAttribute('aria-busy')==='false'"
    )
    projects = await manager.js("fetch('/api/projects').then(r=>r.json())")
    own = {p["id"] for p in projects}
    state["projects"] = list(dict.fromkeys([*state["projects"], *own]))
    save(public.parent.parent.parent / ".local/offer-ui-ledger.json", state)
    await field(manager, ".research-composer textarea", "бутылки")
    await manager.js("document.documentElement.dataset.siteTheme='pulse'")
    await measure(manager, ".research-composer .studio-button.primary", "send", "pulse")
    send_width = await manager.js(
        "document.querySelector('.research-composer button').getBoundingClientRect().width"
    )
    await manager.click("Отправить")
    await manager.wait(
        "document.querySelector('.research-message.user')?.textContent.includes('бутылки')",
        timeout=90,
    )
    await manager.wait("!!document.querySelector('.research-results-hud')", timeout=100)
    check("new chat and first request reach persisted conversation and results", True)
    await measure(
        manager,
        ".research-hud-selected .studio-button.primary",
        "open collection",
        "pulse",
    )
    await manager.click("Открыть подборку")
    await manager.wait(
        "!!document.querySelector('.selected-products-actions .studio-button.primary')"
    )
    # No eligible selection means disabled, not fake enabled, for create in this
    # brand-new empty project. Existing isolated collection has three real items.
    check(
        "empty collection create is disabled",
        await manager.js(
            "document.querySelector('.selected-products-actions .studio-button.primary').disabled"
        ),
    )
    await manager.call(
        "Input.dispatchKeyEvent",
        {"type": "keyDown", "key": "Escape", "windowsVirtualKeyCode": 27},
    )
    await manager.wait("!document.querySelector('.selected-products-panel')")
    await manager.wait(
        "!!document.querySelector('.product-checkbox input:not(:disabled)')"
    )
    await manager.js(
        "document.querySelector('.product-checkbox input:not(:disabled)').click()"
    )
    await manager.wait(
        "document.querySelector('.research-selected-count')?.textContent==='1'"
    )
    await manager.click("Открыть подборку")
    await manager.wait(
        "!!document.querySelector('.selected-products-actions .studio-button.primary:not(:disabled)')"
    )
    await measure(
        manager,
        ".selected-products-actions .studio-button.primary",
        "create offer",
        "pulse",
    )
    await manager.shot(public / "create-offer.png")
    await manager.call(
        "Input.dispatchKeyEvent",
        {"type": "keyDown", "key": "Escape", "windowsVirtualKeyCode": 27},
    )
    check("send layout has a reserved busy label width", send_width >= 100)
    save(public / "button-colors.json", {"measurements": metrics})

    # Edge's own settings API changes only this disposable profile. This is real
    # browser zoom, separate from the additional viewport/scale checks above.
    zoom_target = (
        await control.call("Target.createTarget", {"url": "edge://settings/appearance"})
    )["targetId"]
    settings = Page()
    await settings.attach(zoom_target)
    zoom_results = []
    try:
        await settings.wait(
            "typeof chrome.settingsPrivate?.setDefaultZoom==='function'"
        )
        for zoom in [1.25, 1.5]:
            await settings.js(
                f"new Promise(resolve=>chrome.settingsPrivate.setDefaultZoom({zoom},()=>resolve(true)))"
            )
            configured = await settings.js(
                "new Promise(resolve=>chrome.settingsPrivate.getDefaultZoom(resolve))"
            )
            zoom_page = await page(0)
            for theme in ["pulse", "current"]:
                for route in [path, "/"]:
                    await zoom_page.go(route)
                    await zoom_page.wait(
                        "!!document.querySelector('.offer-primary,.project-list>.studio-button.primary')"
                    )
                    await zoom_page.js(
                        "document.documentElement.dataset.siteTheme="
                        + json.dumps(theme)
                    )
                    geometry = await zoom_page.js("""(()=>({width:innerWidth,dpr:devicePixelRatio,overflow:document.documentElement.scrollWidth>innerWidth+1,
                      clipped:[...document.querySelectorAll('.offer-primary,.studio-button.primary')].filter(e=>e.getBoundingClientRect().width>0).some(e=>e.scrollWidth>e.clientWidth+1||e.scrollHeight>e.clientHeight+1)}))()""")
                    zoom_results.append(
                        {
                            "zoom": zoom,
                            "theme": theme,
                            "screen": "editor" if route == path else "workspace",
                            "native_setting": configured,
                            **geometry,
                        }
                    )
                    check(
                        f"native Edge zoom {zoom} {theme} {route == path and 'editor' or 'workspace'}",
                        abs(configured - zoom) < 0.001
                        and abs(geometry["dpr"] - zoom) < 0.02
                        and not geometry["overflow"]
                        and not geometry["clipped"],
                        geometry=geometry,
                    )
                    await zoom_page.shot(
                        public
                        / f"zoom-{zoom}-{theme}-{'editor' if route == path else 'workspace'}.png"
                    )
        save(
            public / "native-zoom.json",
            {
                "checks": zoom_results,
                "method": "Edge settingsPrivate.setDefaultZoom in task-owned profile; measured devicePixelRatio",
            },
        )
    finally:
        await settings.js(
            "new Promise(resolve=>chrome.settingsPrivate.setDefaultZoom(1,()=>resolve(true)))"
        )
        await settings.ws.close()
        await control.call("Target.closeTarget", {"targetId": zoom_target})

    # Native Clipboard on the actual HTTPS Vercel deployment, separately from the
    # local new UI. No login/deployment/configuration changes are performed.
    external_origin = "https://souvenir-studio-three.vercel.app"
    external = await page(0, external_origin)
    await external.call("Page.navigate", {"url": external_origin + path})
    await external.wait(
        "!!document.querySelector('.offer-publish-actions')", timeout=80
    )
    check(
        "real HTTPS native Clipboard copies the same existing client URL",
        await clipboard(external, external_origin),
    )
    check(
        "external new UI deployment status measured",
        True,
        old_url_field=await external.js(
            "!!document.querySelector('.offer-share-field')"
        ),
        result="new frontend NOT DEPLOYED by this task",
    )
    # Logout destroys the server session and app provider, then a different real
    # account logs in in the same tab without inheriting notifications.
    await manager.go("/offers")
    await manager.wait("!!document.querySelector('.notification-bell')")
    await manager.click("Выйти")
    await manager.wait(
        "!!document.querySelector('.auth-card')&&window.__sources.live===0"
    )
    statuses = await manager.js(
        "Promise.all(['/api/auth/me','/api/notifications',"
        + json.dumps(api)
        + "].map(url=>fetch(url).then(r=>r.status)))"
    )
    check(
        "logout closes stream, removes private UI and server session",
        all(code == 401 for code in statuses),
        private_statuses=statuses,
    )
    created = await manager.js("window.__sources.created")
    await asyncio.sleep(2)
    check(
        "logout does not reconnect account stream",
        await manager.js(
            "window.__sources.live===0&&window.__sources.created===" + str(created)
        ),
    )
    for name in ["name", "password"]:
        await field(manager, "input[name=" + name + "]", state["users"][1][name])
    await manager.click("Войти")
    await manager.wait(
        "!!document.querySelector('.offer-library-row')&&window.__sources.live===1"
    )
    check(
        "different account has only own offer and notifications",
        await manager.js(
            "fetch('/api/notifications').then(r=>r.json()).then(v=>v.items.length===0&&document.querySelectorAll('.offer-library-row').length===1)"
        ),
    )
