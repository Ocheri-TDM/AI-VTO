"""Installed Edge: ordinary HTTPS login, local new UI and anonymous exports.

Run between check_offer_lifecycle.py http and cleanup. No cloud deployment,
cookie injection, project browser runtime or private capability in artifacts.
"""

import asyncio
import hashlib
import json
import shutil
import subprocess
from contextlib import closing, suppress

import httpx
import psutil
import pypdfium2 as pdfium
import websockets
from check_commercial_offers import save
from check_offer_lifecycle import FIXTURES, LEDGER, ORIGIN, PUBLIC, ROOT
from check_palette_browser import Browser
from PIL import Image, ImageDraw


async def main():
    state = json.loads(LEDGER.read_text("utf-8"))
    folder = (ROOT / ".local" / ("offer-lifecycle-edge-" + state["run"])).resolve()
    assert folder.parent == (ROOT / ".local").resolve() and not folder.exists()
    assert not any(
        c.status == "LISTEN" and c.laddr.port == 9226
        for c in psutil.net_connections("tcp")
    )
    folder.mkdir()
    downloads = folder / "downloads"
    downloads.mkdir()
    checks, bounds = [], []
    browsers = []
    edge = control = None
    path = "/api/offers/" + state["primary"]

    def check(name, valid):
        assert valid, name
        checks.append({"check": name, "status": "PASS"})

    async def field(b, selector, value):
        await b.js(
            "(()=>{const e=document.querySelector(" + json.dumps(selector) + ");"
            "Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(e,"
            + json.dumps(str(value))
            + ");e.dispatchEvent(new Event('input',{bubbles:true}));})()"
        )

    async def login(b, origin):
        await b.call(
            "Page.navigate", {"url": origin + "/offers/" + state["primary"] + "/edit"}
        )
        await b.wait("!!document.querySelector('input[name=name]')")
        for name in ("name", "password"):
            await field(b, f"input[name={name}]", state["users"][0][name])
        await b.click("Войти")
        await b.wait(
            "document.querySelectorAll('.offer-edit-fields').length===3", timeout=70
        )
        check(
            "ordinary browser login "
            + ("Vercel HTTPS" if origin == ORIGIN else "local new build"),
            True,
        )

    async def doc(b):
        return await b.js("fetch(" + json.dumps(path) + ").then(r=>r.json())")

    async def geometry(b, label):
        await b.wait(
            "[...document.querySelectorAll('.offer-product-brand img')].every(i=>i.complete&&i.naturalWidth>0)"
        )
        result = await b.js("""(()=>({overflow:document.documentElement.scrollWidth>innerWidth+1,
          rows:[...document.querySelectorAll('.offer-product')].map(s=>{
            const p=s.getBoundingClientRect(),l=s.querySelector('.offer-product-layout').getBoundingClientRect(),
              f=s.querySelector('.offer-product-brand').getBoundingClientRect(),i=s.querySelector('.offer-product-brand img');
            return {footerBelow:f.top>=l.bottom-1,leftInside:f.left>=p.left&&f.left<p.left+p.width/3,
              withinSection:f.bottom<=p.bottom+1,imageLoaded:i.complete&&i.naturalWidth>0,
              footerWidth:i.getBoundingClientRect().width};})}))()""")
        check(label + " no horizontal overflow", not result["overflow"])
        check(
            label + " independent footer below each complete product",
            len(result["rows"]) == 3
            and all(
                r["footerBelow"]
                and r["leftInside"]
                and r["withinSection"]
                and r["imageLoaded"]
                for r in result["rows"]
            ),
        )
        bounds.append({"viewport": label, **result})

    async def shot(b, filename):
        # Capabilities are never saved visibly in public screenshots.
        await b.js(
            "document.querySelectorAll('.offer-share-field input').forEach(e=>e.style.visibility='hidden')"
        )
        await b.shot(PUBLIC / filename)
        await b.js(
            "document.querySelectorAll('.offer-share-field input').forEach(e=>e.style.visibility='')"
        )

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
                    info = (
                        await probe.get("http://127.0.0.1:9226/json/version")
                    ).json()
                    break
                except (httpx.HTTPError, ValueError):
                    await asyncio.sleep(0.1)
        owner = Browser()
        await owner.connect({})  # Deliberately no session injection.
        owner.ws.ping_timeout = None
        browsers.append(owner)
        await login(owner, ORIGIN)
        check(
            "Vercel existing editor retains exact manual values",
            (await doc(owner))["draft"]["items"][1]["unit_price"] == "35224.51",
        )
        print(
            "Ordinary Vercel browser login and existing deployed editor verified",
            flush=True,
        )
        await login(owner, "http://127.0.0.1:3000")
        check(
            "new UI removes revoke and rotate controls",
            await owner.js(
                "![...document.querySelectorAll('button')].some(b=>['Отозвать ссылку','Выпустить новую ссылку'].includes(b.textContent))"
            ),
        )
        check(
            "expiry explicit UTC+5",
            "UTC+5"
            in await owner.js(
                "document.querySelector('.offer-link-expiry').textContent"
            ),
        )
        before = await doc(owner)
        # Clipboard transport spy: checks exact supplied bytes without modifying
        # or reading the user's operating-system clipboard.
        await owner.js(
            "window.__copies=[];Object.defineProperty(navigator.clipboard,'writeText',{configurable:true,value:async v=>{window.__copies.push(v)}})"
        )
        for n in range(10):
            await owner.click("Копировать ссылку")
            await owner.wait(
                f"window.__copies.length==={n + 1}&&document.querySelector('.offer-manager-panel').textContent.includes('Ссылка скопирована')"
            )
        check(
            "ten copy actions provide the same full HTTPS URL",
            await owner.js(
                "window.__copies.every(v=>v===" + json.dumps(state["public_url"]) + ")"
            ),
        )
        after = await doc(owner)
        check(
            "copy changes no version, revision, epoch or deadline",
            all(before[k] == after[k] for k in ("version", "revisions", "share")),
        )
        await owner.js(
            "Object.defineProperty(navigator.clipboard,'writeText',{configurable:true,value:async()=>{throw Error('Clipboard permission denied')}})"
        )
        await owner.click("Копировать ссылку")
        await owner.wait("!!document.querySelector('.offer-error')")
        check(
            "denied clipboard has visible error and readonly full URL",
            await owner.js(
                "document.querySelector('.offer-error').textContent.includes('вручную')&&document.querySelector('.offer-share-field input').readOnly&&document.querySelector('.offer-share-field input').value==="
                + json.dumps(state["public_url"])
            ),
        )
        await owner.call("Page.reload", {"ignoreCache": True})
        await owner.wait("document.querySelectorAll('.offer-edit-fields').length===3")
        check(
            "F5 same offer and manual quantity",
            (await doc(owner))["draft"]["items"][1]["quantity"] == 101,
        )
        await owner.js(
            "localStorage.setItem('forma-chat-id',"
            + json.dumps(state["project"]["chat_id"])
            + ")"
        )
        await owner.call("Page.navigate", {"url": "http://127.0.0.1:3000/"})
        await owner.wait(
            "[...document.querySelectorAll('button')].some(b=>b.textContent==='Открыть подборку')"
        )
        await owner.click("Открыть подборку")
        await owner.wait(
            "[...document.querySelectorAll('button')].some(b=>b.textContent==='Открыть предложение'&&!b.disabled)"
        )
        await owner.click("Открыть предложение")
        await owner.wait("document.querySelectorAll('.offer-edit-fields').length===3")
        check(
            "collection reopens same persistent editor",
            await owner.js(
                "location.pathname==="
                + json.dumps("/offers/" + state["primary"] + "/edit")
            ),
        )
        async with httpx.AsyncClient() as probe:
            tab_info = (
                await probe.put(
                    "http://127.0.0.1:9226/json/new?"
                    + "http://127.0.0.1:3000/offers/"
                    + state["primary"]
                    + "/edit"
                )
            ).json()
        second = Browser()
        second.ws = await websockets.connect(
            tab_info["webSocketDebuggerUrl"], max_size=30_000_000, ping_timeout=None
        )
        second.number = 0
        browsers.append(second)
        await second.call("Page.enable")
        await second.wait("document.querySelectorAll('.offer-edit-fields').length===3")
        check(
            "two real tabs read the same document and manual quantity",
            (await doc(second))["id"] == before["id"]
            and (await doc(second))["draft"]["items"][1]["quantity"] == 101,
        )
        failed_path = f"/api/projects/{state['project']['id']}/offer"
        await second.call(
            "Page.addScriptToEvaluateOnNewDocument",
            {
                "source": "const original=window.fetch;window.__fallbackCreates=0;window.fetch=(url,options)=>{const p=String(url);"
                "if(p==="
                + json.dumps(failed_path)
                + ")return Promise.resolve(new Response(JSON.stringify({detail:'Test read denied'}),{status:403,headers:{'Content-Type':'application/json'}}));"
                "if(p.endsWith('/offers')&&options?.method==='POST')window.__fallbackCreates++;return original(url,options)}"
            },
        )
        await second.call("Page.navigate", {"url": "http://127.0.0.1:3000/"})
        await second.wait(
            "[...document.querySelectorAll('button')].some(b=>b.textContent==='Открыть подборку')"
        )
        await second.click("Открыть подборку")
        await second.wait(
            "document.querySelector('.selected-products-panel')?.textContent.includes('Не удалось прочитать предложение')"
        )
        check(
            "read-failure UI fixture disables creation without fallback POST",
            await second.js(
                "window.__fallbackCreates===0&&[...document.querySelectorAll('.selected-products-actions button')].some(b=>b.textContent==='Создать предложение'&&b.disabled)"
            ),
        )
        await owner.js("document.querySelector('.offer-settings').open=true")
        await owner.call(
            "Emulation.setDeviceMetricsOverride",
            {
                "width": 390,
                "height": 844,
                "deviceScaleFactor": 1,
                "mobile": True,
            },
        )
        await geometry(owner, "mobile 390 editable owner")
        await owner.js("window.scrollTo(0,0)")
        await shot(owner, "owner-mobile.png")
        await owner.call(
            "Emulation.setDeviceMetricsOverride",
            {
                "width": 1440,
                "height": 1000,
                "deviceScaleFactor": 1,
                "mobile": False,
            },
        )
        logo = Image.new("RGB", (360, 100), "#163E52")
        ImageDraw.Draw(logo).text((20, 38), "CLIENT TEST", fill="white")
        logo.save(folder / "logo.png")
        banner = Image.new("RGB", (1000, 220), "#83fd80")
        banner.save(folder / "banner.png")
        for index, name in enumerate(("logo", "banner")):
            previous = (await doc(owner))["version"]
            tree = await owner.call("DOM.getDocument")
            nodes = await owner.call(
                "DOM.querySelectorAll",
                {
                    "nodeId": tree["root"]["nodeId"],
                    "selector": ".offer-settings input[type=file]",
                },
            )
            await owner.call(
                "DOM.setFileInputFiles",
                {
                    "nodeId": nodes["nodeIds"][index],
                    "files": [str(folder / (name + ".png"))],
                },
            )
            await owner.wait(
                "fetch("
                + json.dumps(path)
                + f").then(r=>r.json()).then(d=>d.version>{previous}&&!d.materials_preparing)",
                timeout=100,
            )
            await owner.wait("!document.querySelector('.offer-primary').disabled")
        await owner.call("Page.reload", {"ignoreCache": True})
        await owner.wait("document.querySelectorAll('.offer-edit-fields').length===3")
        reopened = await doc(owner)
        check(
            "custom header and banner survive reopen",
            bool(
                reopened["draft"]["settings"]["logo"]
                and reopened["preview"]["banner_url"]
            ),
        )
        await owner.click("Клиентский вид")
        await geometry(owner, "desktop owner preview")
        await owner.js(
            "localStorage.setItem('souvenir:site-theme','pulse');document.documentElement.dataset.siteTheme='pulse'"
        )
        await geometry(owner, "Pulse owner preview")
        await owner.js(
            "localStorage.setItem('souvenir:site-theme','current');document.documentElement.dataset.siteTheme='current'"
        )
        await shot(owner, "owner-desktop.png")
        await owner.click("Вернуться в редактор")
        await owner.click("Обновить опубликованную версию")
        await owner.wait(
            "fetch("
            + json.dumps(path)
            + ").then(r=>r.json()).then(d=>d.share.published_revision_id!=="
            + json.dumps(before["share"]["published_revision_id"])
            + "&&!d.share.pending_revision_id)",
            timeout=140,
        )
        latest = await doc(owner)
        check(
            "explicit content update preserves URL/deadline",
            latest["share"]["public_url"] == state["public_url"]
            and latest["share"]["expires_at"] == state["expires_at"],
        )
        print(
            "New local editor: copy, reopen, uploads and publication verified",
            flush=True,
        )
        control = Browser()
        control.ws = await websockets.connect(
            info["webSocketDebuggerUrl"], ping_timeout=None
        )
        control.number = 0
        context = (await control.call("Target.createBrowserContext"))[
            "browserContextId"
        ]
        target = (
            await control.call(
                "Target.createTarget",
                {"url": "about:blank", "browserContextId": context},
            )
        )["targetId"]
        async with httpx.AsyncClient() as probe:
            tabs = (await probe.get("http://127.0.0.1:9226/json/list")).json()
        viewer = Browser()
        viewer.ws = await websockets.connect(
            next(t["webSocketDebuggerUrl"] for t in tabs if t["id"] == target),
            max_size=30_000_000,
            ping_timeout=None,
        )
        viewer.number = 0
        browsers.append(viewer)
        await viewer.call("Page.enable")
        await viewer.call("Network.enable")
        await viewer.call(
            "Emulation.setDeviceMetricsOverride",
            {"width": 1440, "height": 1000, "deviceScaleFactor": 1, "mobile": False},
        )
        await viewer.call(
            "Browser.setDownloadBehavior",
            {
                "behavior": "allow",
                "downloadPath": str(downloads),
                "browserContextId": context,
            },
        )
        await viewer.call("Page.navigate", {"url": state["public_url"]})
        await viewer.wait(
            "document.querySelectorAll('.offer-product').length===3", timeout=80
        )
        check(
            "clean incognito Vercel public view without owner session",
            await viewer.js("fetch('/api/auth/me').then(r=>r.status===401)"),
        )
        for kind, button in (("pdf", "PDF"), ("xlsx", "Excel")):
            await viewer.click(button)
            file = downloads / ("commercial-offer." + kind)
            for _ in range(150):
                if file.exists() and file.stat().st_size > 100:
                    break
                await asyncio.sleep(0.2)
            check("actual anonymous Vercel " + button + " download", file.exists())
            async with httpx.AsyncClient(base_url=ORIGIN) as guest:
                dto = (
                    await guest.get(
                        state["public_url"].replace(ORIGIN, "/api/public", 1)
                    )
                ).json()
                response = await guest.get(dto[kind + "_url"])
            check(
                button + " download matches pinned public revision",
                hashlib.sha256(file.read_bytes()).digest()
                == hashlib.sha256(response.content).digest(),
            )
            shutil.copyfile(file, FIXTURES / ("offer-custom-header." + kind))
        local_public = state["public_url"].replace(ORIGIN, "http://127.0.0.1:3000", 1)
        await viewer.call("Page.navigate", {"url": local_public})
        await viewer.wait(
            "document.querySelectorAll('.offer-product-brand').length===3"
        )
        await geometry(viewer, "desktop anonymous new build")
        await viewer.js("document.querySelector('.offer-product').scrollIntoView()")
        await shot(viewer, "product-desktop.png")
        await viewer.call(
            "Emulation.setDeviceMetricsOverride",
            {"width": 390, "height": 844, "deviceScaleFactor": 1, "mobile": True},
        )
        await geometry(viewer, "mobile 390 anonymous new build")
        await viewer.js(
            "document.querySelector('.offer-product-brand').scrollIntoView({block:'center'})"
        )
        await shot(viewer, "product-mobile.png")
        # UI-only clock fixture: server time, real shares and DB remain untouched.
        # Remount via an explicit navigation script to control only public fetch.
        await viewer.call(
            "Page.addScriptToEvaluateOnNewDocument",
            {
                "source": "const originalFetch=window.fetch;window.fetch=(url,options)=>String(url).startsWith('/api/public/offers/')?Promise.resolve(new Response(JSON.stringify({code:'OFFER_LINK_EXPIRED',detail:'Срок действия ссылки истёк'}),{status:410,headers:{'Content-Type':'application/json'}})):originalFetch(url,options)"
            },
        )
        await viewer.call("Page.reload", {"ignoreCache": True})
        await viewer.wait(
            "document.querySelector('.offer-error')?.textContent.includes('Срок действия ссылки истёк')"
        )
        check(
            "expired UI fixture hides all offer content",
            await viewer.js("document.querySelectorAll('.offer-product').length===0"),
        )
        await shot(viewer, "expired-ui-fixture.png")
        save(
            PUBLIC / "browser.json",
            {
                "checks": checks,
                "bounds": bounds,
                "browser": "installed Microsoft Edge; task-owned profile; ordinary form logins",
                "clipboard": "writeText transport spy and denied-permission simulation; operating system clipboard not read or changed",
                "expired_ui": "410 UI-only fetch fixture; exact backend boundary tested with injected service clock",
                "production_new_frontend": "NOT DEPLOYED: tested new UI locally, existing deployed UI over Vercel",
            },
        )
        with pdfium.PdfDocument(str(FIXTURES / "offer-custom-header.pdf")) as pages:
            for n in range(len(pages)):
                with (
                    closing(pages[n]) as page,
                    closing(page.render(scale=1.2)) as bitmap,
                ):
                    bitmap.to_pil().save(
                        FIXTURES / f"offer-custom-header-page-{n + 1}.png"
                    )
        print(
            "Browser checks:",
            len(checks),
            "; no capabilities saved in screenshots/evidence",
        )
    finally:
        for b in browsers + ([control] if control else []):
            with suppress(websockets.exceptions.ConnectionClosed, OSError):
                await b.ws.close()
        if edge:
            try:
                parent = psutil.Process(edge.pid)
                targets = parent.children(recursive=True) + [parent]
                for p in targets:
                    try:
                        p.terminate()
                    except psutil.NoSuchProcess:
                        pass
                await asyncio.to_thread(psutil.wait_procs, targets, timeout=8)
            except psutil.NoSuchProcess:
                pass
        if folder.exists():
            assert (
                folder.parent == (ROOT / ".local").resolve()
                and folder.name == "offer-lifecycle-edge-" + state["run"]
            )
            shutil.rmtree(folder)


if __name__ == "__main__":
    asyncio.run(main())
