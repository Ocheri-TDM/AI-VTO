"""Real owner and clean incognito Edge, desktop/mobile, real download controls.

Run after check_commercial_offers.py run. No project browser dependency installed.
"""

import asyncio
import base64
import copy
import hashlib
import html
import json
import shutil
import subprocess
from uuid import uuid4

import httpx
import openpyxl
import psutil
import websockets
from check_commercial_offers import LEDGER, PUBLIC, ROOT, call, edit_body, ready, save
from check_palette_browser import Browser
from PIL import Image, ImageDraw


async def main():
    state = json.loads(LEDGER.read_text("utf-8"))
    folder = (ROOT / ".local" / ("commercial-edge-" + state["run"])).resolve()
    assert folder.parent == (ROOT / ".local").resolve() and not folder.exists()
    assert not any(
        c.status == "LISTEN" and c.laddr.port == 9226
        for c in psutil.net_connections("tcp")
    )
    folder.mkdir()
    downloads = folder / "downloads"
    downloads.mkdir()
    PUBLIC.mkdir(parents=True, exist_ok=True)
    checks = []
    edge = owner = viewer = browser_socket = None

    def check(label, valid):
        assert valid, label
        checks.append({"check": label, "status": "PASS"})

    async def field(browser, selector, value):
        await browser.js(
            "(()=>{const e=document.querySelector("
            + json.dumps(selector)
            + ");Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(e,"
            + json.dumps(str(value))
            + ");e.dispatchEvent(new Event('input',{bubbles:true}));})()"
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
                    if (
                        await probe.get("http://127.0.0.1:9226/json/list")
                    ).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.1)
            info = (await probe.get("http://127.0.0.1:9226/json/version")).json()
        owner = Browser()
        await owner.connect(state["users"][0]["cookies"])
        owner.ws.ping_timeout = None
        await owner.call(
            "Page.addScriptToEvaluateOnNewDocument",
            {
                "source": "if(location.origin==='http://127.0.0.1:3000')localStorage.setItem('forma-chat-id',"
                + json.dumps(state["project"]["chat_id"])
                + ");"
            },
        )
        await owner.call("Page.navigate", {"url": "http://127.0.0.1:3000/"})
        await owner.wait(
            "[...document.querySelectorAll('button')].some(b=>b.textContent==='Открыть подборку')"
        )
        await owner.click("Открыть подборку")
        await owner.wait(
            "[...document.querySelectorAll('button')].some(b=>b.textContent==='Создать предложение')"
        )
        await owner.click("Создать предложение")
        await owner.wait(
            "location.pathname.endsWith('/edit')&&document.querySelectorAll('.offer-edit-fields').length===3"
        )
        identifier = await owner.js("location.pathname.split('/')[2]")
        state["documents"].append(identifier)
        state["browser_document"] = identifier
        save(LEDGER, state)
        path = "/api/offers/" + identifier
        check("collection to editor real primary button", True)
        check(
            "browser quantities 250/100/1",
            await owner.js(
                "[...document.querySelectorAll('.offer-price-inputs input[type=number]')].map(e=>Number(e.value))"
            )
            == [250, 100, 1],
        )
        # Actual controlled React field, second product retains its quantity 100.
        selector = '.offer-product:nth-child(2) input[aria-label^="Цена:"]'
        await field(owner, selector, "35224.51")
        await owner.wait(
            "document.querySelector('.offer-product:nth-child(2) .offer-line-amount').textContent.includes('3 522 451,00')"
        )
        await owner.call("Page.reload", {"ignoreCache": True})
        await owner.wait(
            "document.querySelector('.offer-product:nth-child(2) input[aria-label^=\"Цена:\"]')?.value==='35224.51'"
        )
        check("manual decimal autosave and F5", True)
        await owner.shot(PUBLIC / "editor-desktop.png")
        await owner.click("Клиентский вид")
        check(
            "editor preview no item inputs",
            await owner.js(
                "document.querySelectorAll('.offer-edit-fields input').length"
            )
            == 0,
        )
        await owner.click("Вернуться в редактор")
        # Reorder and undo it; exclusion affects only this document.
        await owner.js(
            "[...document.querySelectorAll('.offer-product:nth-child(2) button')].find(b=>b.textContent==='Выше').click()"
        )
        await owner.wait(
            "document.querySelector('.offer-product:first-child .offer-sku').textContent.includes('GF-12449.40')"
        )
        await owner.js(
            "[...document.querySelectorAll('.offer-product:first-child button')].find(b=>b.textContent==='Ниже').click()"
        )
        await owner.wait(
            "document.querySelector('.offer-product:nth-child(2) .offer-sku').textContent.includes('GF-12449.40')"
        )
        check("reorder real controls", True)
        await owner.js("document.querySelector('.offer-settings').open=true")
        shutil.copyfile(
            ROOT / "apps/api/app/assets/presentations/VTOlogo.png", folder / "logo.png"
        )
        banner = Image.new("RGB", (1000, 260), "#83fd80")
        ImageDraw.Draw(banner).polygon(
            [(720, 0), (1000, 0), (1000, 260), (580, 260)], fill="#f42902"
        )
        banner.save(folder / "banner.png")
        for index, name in enumerate(("logo", "banner")):
            tree = await owner.call("DOM.getDocument")
            previous = await owner.js(
                f"fetch('{path}').then(r=>r.json()).then(d=>d.version)"
            )
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
                f"fetch('{path}').then(r=>r.json()).then(d=>d.version>{previous}&&!d.materials_preparing)"
            )
            await owner.wait(
                "document.querySelector('.offer-manager-panel').textContent.includes('Черновик сохранён')&&!document.querySelector('.offer-primary').disabled"
            )
            await asyncio.sleep(0.5)
        check("real safe logo and asynchronous banner uploads", True)
        await owner.wait("!document.querySelector('.offer-primary').disabled")
        await owner.click("Создать ссылку для клиента")
        await owner.wait(
            "!!document.querySelector('.offer-publish-actions a')", timeout=160
        )
        local_path = await owner.js(
            "document.querySelector('.offer-publish-actions a').getAttribute('href')"
        )
        state["browser_public_path"] = local_path
        save(LEDGER, state)
        check(
            "local-only link label",
            await owner.js(
                "document.querySelector('.offer-publish-actions').textContent.includes('локальный просмотр')"
            ),
        )
        # Separate incognito context has no owner/session cookies.
        browser_socket = await websockets.connect(
            info["webSocketDebuggerUrl"], ping_timeout=None
        )
        seq = 0

        async def browser_call(method, params):
            nonlocal seq
            seq += 1
            await browser_socket.send(
                json.dumps({"id": seq, "method": method, "params": params})
            )
            while True:
                r = json.loads(await browser_socket.recv())
                if r.get("id") == seq:
                    assert not r.get("error"), r.get("error")
                    return r["result"]

        context = (await browser_call("Target.createBrowserContext", {}))[
            "browserContextId"
        ]
        target = (
            await browser_call(
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
        await viewer.call(
            "Page.navigate", {"url": "http://127.0.0.1:3000" + local_path}
        )
        await viewer.wait("document.querySelectorAll('.offer-product').length===3")
        check(
            "incognito auth401 and public200",
            await viewer.js("fetch('/api/auth/me').then(r=>r.status)") == 401,
        )
        check(
            "public no edit/chat/project controls",
            await viewer.js(
                "document.querySelectorAll('input,textarea,[data-testid=header-account],.research-sidebar').length"
            )
            == 0,
        )
        for n in range(3):
            await viewer.js(
                f"document.querySelectorAll('.offer-product')[{n}].scrollIntoView()"
            )
            await viewer.wait(
                f"[...document.querySelectorAll('.offer-product:nth-child({n + 1}) .offer-main-photo img')].every(i=>i.complete&&i.naturalWidth>0)"
            )
        await viewer.js("window.scrollTo(0,0)")
        check(
            "public calculation matches",
            await viewer.js(
                "document.querySelector('.offer-product:nth-child(2) .offer-line-amount').textContent.includes('3 522 451,00')"
            ),
        )
        await viewer.shot(PUBLIC / "client-desktop.png")
        await viewer.js(
            "document.querySelector('.offer-product:nth-child(2)').scrollIntoView()"
        )
        await viewer.shot(PUBLIC / "client-product.png")
        if await viewer.js("!!document.querySelector('.offer-thumbnails button')"):
            await viewer.js(
                "document.querySelectorAll('.offer-thumbnails button')[1].click()"
            )
            check(
                "gallery thumbnail selection",
                await viewer.js(
                    "document.querySelectorAll('.offer-thumbnails button')[1].getAttribute('aria-pressed')==='true'"
                ),
            )
        for theme in ("current", "pulse"):
            await viewer.js(
                "document.documentElement.dataset.siteTheme=" + json.dumps(theme)
            )
            check(
                "visitor theme independent " + theme,
                await viewer.js(
                    "getComputedStyle(document.querySelector('.commercial-public')).backgroundColor"
                )
                == "rgb(253, 253, 255)",
            )
        await viewer.call(
            "Emulation.setDeviceMetricsOverride",
            {"width": 390, "height": 844, "deviceScaleFactor": 1, "mobile": True},
        )
        await viewer.js("window.scrollTo(0,0)")
        await viewer.shot(PUBLIC / "client-mobile.png")
        check(
            "mobile no horizontal overflow",
            await viewer.js("document.documentElement.scrollWidth<=innerWidth"),
        )
        await viewer.js(
            "document.querySelector('.offer-product:nth-child(2)').scrollIntoView()"
        )
        await viewer.shot(PUBLIC / "client-mobile-product.png")
        await viewer.call("Emulation.setEmulatedMedia", {"media": "print"})
        check(
            "print actions hidden",
            await viewer.js(
                "getComputedStyle(document.querySelector('.offer-toolbar')).display==='none'"
            ),
        )
        await viewer.call("Emulation.setEmulatedMedia", {"media": "screen"})
        await viewer.js("window.scrollTo(0,0)")
        async with httpx.AsyncClient(
            base_url="http://127.0.0.1:8000",
            cookies=state["users"][0]["cookies"],
            timeout=180,
        ) as c:
            document = await call(c, "GET", path)
            public = "/api/public/offers/" + local_path.rsplit("/", 1)[1]
            async with httpx.AsyncClient(base_url=c.base_url, timeout=180) as guest:
                value = await call(guest, "GET", public)
                for label, ext in [("PDF", "pdf"), ("Excel", "xlsx")]:
                    await viewer.click(label)
                    output = downloads / ("commercial-offer." + ext)
                    for _ in range(120):
                        if output.exists() and not list(downloads.glob("*.crdownload")):
                            break
                        await asyncio.sleep(0.2)
                    assert output.exists(), "Browser download incomplete"
                    response = await guest.get(value[ext + "_url"])
                    check(
                        "real browser download matches " + ext,
                        hashlib.sha256(output.read_bytes()).digest()
                        == hashlib.sha256(response.content).digest(),
                    )
                # Read actual workbook cells/images into an inspectable local table.
                workbook = openpyxl.load_workbook(
                    downloads / "commercial-offer.xlsx", data_only=True
                )
                sheet = workbook.active
                table = "<meta charset=utf-8><style>html,body{background:#fff;color:#111;color-scheme:light}body{font:14px Arial}table{border-collapse:collapse}td,th{border:1px solid #aaa;padding:9px;max-width:260px;white-space:pre-wrap}img{max-width:90px;max-height:110px}</style><table>"
                images = {
                    i.anchor._from.row: base64.b64encode(i._data()).decode()
                    for i in sheet._images
                }
                for number, row in enumerate(sheet):
                    table += (
                        "<tr>"
                        + "".join(
                            "<td>"
                            + (
                                "<img src='data:image/png;base64,"
                                + images[number]
                                + "'>"
                                if cell.column == 2 and number in images
                                else html.escape(str(cell.value or ""))
                            )
                            + "</td>"
                            for cell in row
                        )
                        + "</tr>"
                    )
                table += "</table>"
                await viewer.call("Page.navigate", {"url": "about:blank"})
                await viewer.wait("location.href==='about:blank'")
                await viewer.call(
                    "Emulation.setDeviceMetricsOverride",
                    {
                        "width": 1900,
                        "height": 1100,
                        "deviceScaleFactor": 1,
                        "mobile": False,
                    },
                )
                await viewer.call(
                    "Page.setDocumentContent",
                    {
                        "frameId": (await viewer.call("Page.getFrameTree"))[
                            "frameTree"
                        ]["frame"]["id"],
                        "html": table,
                    },
                )
                await viewer.shot(PUBLIC / "xlsx-readback.png")
                await viewer.call(
                    "Page.navigate", {"url": "http://127.0.0.1:3000" + local_path}
                )
                await viewer.wait(
                    "document.querySelectorAll('.offer-product').length===3"
                )
                # CAS conflict then explicit update; stale guest must not download mismatched revision.
                body = edit_body(document)
                body["items"][1]["quantity"] = 102
                saved = await call(c, "PATCH", path, json=body)
                conflict = await c.patch(path, json=body)
                check("stale manager save rejected", conflict.status_code == 409)
                await call(
                    c,
                    "POST",
                    path + "/publish",
                    json={
                        "version": saved["version"],
                        "request_id": str(uuid4()),
                        "share_epoch": saved["share"]["epoch"],
                    },
                )
                document, _ = await ready(c, path, saved["version"])
                await viewer.click("PDF")
                await viewer.wait(
                    "document.querySelector('[role=alert]')?.textContent.includes('Предложение обновилось')"
                )
                check("stale public download visible409", True)
                await viewer.click("Обновить страницу")
                await viewer.wait(
                    "document.querySelector('.offer-product:nth-child(2) .offer-calculation').textContent.includes('102')"
                )
                document = await call(
                    c,
                    "POST",
                    path + "/revoke",
                    json={
                        "version": document["version"],
                        "epoch": document["share"]["epoch"],
                    },
                )
                await viewer.call("Page.reload", {"ignoreCache": True})
                await viewer.wait(
                    "!!document.querySelector('[role=alert]')&&document.querySelectorAll('.offer-product').length===0"
                )
                check(
                    "revoked link browser unavailable",
                    await viewer.js(
                        "document.querySelectorAll('.offer-product').length"
                    )
                    == 0,
                )
                document = await call(
                    c,
                    "POST",
                    path + "/rotate",
                    json={
                        "version": document["version"],
                        "epoch": document["share"]["epoch"],
                    },
                )
                await viewer.call(
                    "Page.navigate",
                    {
                        "url": "http://127.0.0.1:3000"
                        + document["share"]["local_preview_path"]
                    },
                )
                await viewer.wait(
                    "document.querySelectorAll('.offer-product').length===3"
                )
                check("rotated link browser works", True)
            await owner.call(
                "Page.navigate",
                {"url": "http://127.0.0.1:3000/offers/" + identifier + "/edit"},
            )
            await owner.wait(
                "document.querySelectorAll('.offer-edit-fields').length===3"
            )
            await owner.js(
                "[...document.querySelectorAll('.offer-product:nth-child(3) button')].find(b=>b.textContent==='Исключить позицию').click()"
            )
            await owner.wait(
                "document.querySelectorAll('.offer-edit-fields').length===2&&document.querySelector('.offer-manager-panel').textContent.includes('Черновик сохранён')"
            )
            collection = await call(
                c, "GET", "/api/projects/" + state["project"]["id"] + "/collection"
            )
            check(
                "exclude affects private document only",
                len(collection["selected_ids"]) == 3
                and await viewer.js(
                    "document.querySelectorAll('.offer-product').length"
                )
                == 3,
            )
            await owner.call(
                "Page.navigate",
                {"url": "http://127.0.0.1:3000/#presentation=" + state["legacy"]},
            )
            await owner.wait(
                "!!document.querySelector('.presentation-workspace .presentation-page')"
            )
            await owner.wait(
                "document.querySelector('.presentation-page').complete&&document.querySelector('.presentation-page').naturalWidth>0"
            )
            check(
                "archived legacy editor and real preview remain accessible",
                await owner.js(
                    "[...document.querySelectorAll('.presentation-workspace a')].some(a=>a.textContent==='Скачать PDF'&&a.getAttribute('href'))"
                ),
            )
            await owner.shot(PUBLIC / "legacy-editor.png")
            # UI-only fixture: same real variant photo twice, never another color.
            # No fixture is inserted into the production catalog or persisted offer.
            async with httpx.AsyncClient(base_url=c.base_url) as fixture_client:
                current = await call(
                    fixture_client,
                    "GET",
                    "/api/public/offers/"
                    + document["share"]["local_preview_path"].rsplit("/", 1)[1],
                )
                product = copy.deepcopy(current["items"][1])
                photo = await fixture_client.get(product["images"][0]["url"])
                assert photo.status_code == 200
            image_data = (
                "data:image/png;base64," + base64.b64encode(photo.content).decode()
            )
            product["images"] = [
                {"id": "fixture-main", "url": image_data},
                {"id": "fixture-second", "url": image_data},
            ]
            product["main_image"] = "fixture-main"
            product["description"] = "\n\n".join([product["description"]] * 5)
            fixture = {
                **current,
                "title": "Проверка галереи и полного описания",
                "company": "",
                "items": [product],
            }
            fixture["logo_url"] = current["logo_url"]
            source = (
                "const originalFetch=window.fetch;window.fetch=(url,options)=>String(url)==='/api/public/offers/gallery-fixture'?Promise.resolve(new Response("
                + json.dumps(json.dumps(fixture, ensure_ascii=False))
                + ",{status:200,headers:{'Content-Type':'application/json'}})):originalFetch(url,options);"
            )
            script = await viewer.call(
                "Page.addScriptToEvaluateOnNewDocument", {"source": source}
            )
            await viewer.call(
                "Page.navigate", {"url": "http://127.0.0.1:3000/offers/gallery-fixture"}
            )
            await viewer.wait(
                "document.querySelectorAll('.offer-thumbnails button').length===2"
            )
            await viewer.js(
                "document.querySelectorAll('.offer-thumbnails button')[1].click()"
            )
            check(
                "gallery selection UI fixture same variant",
                await viewer.js(
                    "document.querySelectorAll('.offer-thumbnails button')[1].getAttribute('aria-pressed')==='true'"
                ),
            )
            await viewer.js(
                "document.querySelector('.offer-description summary').click()"
            )
            check(
                "full description expansion UI fixture",
                await viewer.js(
                    "document.querySelector('.offer-description details').open&&document.querySelector('.offer-description details').textContent.length>1200"
                ),
            )
            await viewer.js("document.querySelector('.offer-product').scrollIntoView()")
            await viewer.shot(PUBLIC / "gallery-description-fixture.png")
            await viewer.call(
                "Page.removeScriptToEvaluateOnNewDocument",
                {"identifier": script["identifier"]},
            )
        save(
            PUBLIC / "browser.json",
            {
                "checks": checks,
                "browser": info["Browser"],
                "native_excel": "NOT TESTED",
                "xlsx_visual": "actual workbook values and embedded images read with openpyxl and displayed in Edge; not native Excel",
            },
        )
        print(
            f"Edge owner/incognito acceptance: {len(checks)} checks; no keys/cookies in artifacts"
        )
    finally:
        for client in (owner, viewer):
            if client:
                await client.ws.close()
        if browser_socket:
            await browser_socket.close()
        if edge:
            try:
                process = psutil.Process(edge.pid)
                targets = process.children(recursive=True) + [process]
                for p in targets:
                    try:
                        p.terminate()
                    except psutil.NoSuchProcess:
                        pass
                _, alive = psutil.wait_procs(targets, timeout=5)
                for p in alive:
                    try:
                        p.kill()
                    except psutil.NoSuchProcess:
                        pass
            except psutil.NoSuchProcess:
                pass
        assert folder.parent == (ROOT / ".local").resolve()
        for _ in range(30):
            try:
                shutil.rmtree(folder)
                break
            except OSError:
                await asyncio.sleep(0.2)


if __name__ == "__main__":
    asyncio.run(main())
