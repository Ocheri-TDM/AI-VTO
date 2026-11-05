"""Installed Edge theme acceptance with the real API and disposable SQLite.

Never starts the main PostgreSQL/Ollama or changes a real project. Cached source
photos are read by verified public fixture hash. PDF rendering is not involved.
"""

import asyncio
import hashlib
import json
import shutil
import subprocess
import sys
from contextlib import suppress
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx
import psutil
import uvicorn
import websockets

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))
from app.config import Settings
from app.database.models import Base
from app.domain.models import Product, ProductColor
from app.main import create_app
from app.providers.vto.provider import normalize
from check_palette_browser import Browser

DROPDOWNS = "--dropdowns" in sys.argv
WORKSPACE_LAYOUT = "--workspace-layout" in sys.argv
OUTPUT = (
    ROOT
    / "docs/screenshots"
    / (
        "workspace-layout"
        if WORKSPACE_LAYOUT
        else "dropdowns"
        if DROPDOWNS
        else "site-theme"
    )
)
if WORKSPACE_LAYOUT:
    OUTPUT = OUTPUT / ("before" if "--before" in sys.argv else "after")


class ThemeBrowser(Browser):
    def __init__(self):
        self.errors = []

    async def call(self, method, params=None):
        self.number += 1
        number = self.number
        await self.ws.send(
            json.dumps({"id": number, "method": method, "params": params or {}})
        )
        while True:
            event = json.loads(await asyncio.wait_for(self.ws.recv(), 30))
            if event.get("method") == "Runtime.exceptionThrown":
                self.errors.append(str(event["params"].get("exceptionDetails"))[:700])
            if (
                event.get("method") == "Runtime.consoleAPICalled"
                and event["params"].get("type") == "error"
            ):
                self.errors.append(str(event["params"].get("args"))[:700])
            if event.get("id") == number:
                if event.get("error"):
                    raise RuntimeError(event["error"])
                return event.get("result", {})


async def main():
    ports = {
        c.laddr.port
        for c in psutil.net_connections(kind="tcp")
        if c.laddr and c.status == "LISTEN"
    }
    assert not ports & {18012, 18013, 9226}, "Acceptance ports already in use"
    initial_services = sorted(ports & {3000, 8000, 55432, 11434})
    folder = ROOT / ".local" / ("site-theme-" + uuid4().hex)
    assert folder.resolve().parent == (ROOT / ".local").resolve()
    folder.mkdir()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    fixture = json.loads(
        (ROOT / "apps/api/tests/fixtures/vto/melango.json").read_text("utf-8")
    )
    manifest = json.loads(
        (
            ROOT / "apps/api/tests/fixtures/presentations/premium-dark-v3/manifest.json"
        ).read_text("utf-8")
    )
    image_map = {}
    photos = {}
    for item in manifest["items"]:
        source = (
            ROOT
            / ".local/presentations/assets/original"
            / (item["source_sha256"] + ".img")
        )
        assert source.is_file(), (
            "Install the recorded source fixture cache before visual acceptance"
        )
        data = source.read_bytes()
        assert hashlib.sha256(data).hexdigest() == item["source_sha256"]
        url = (
            "http://127.0.0.1:18013/fixture-image/"
            + item["offer_id"].replace(":", "-")
            + ".jpg"
        )
        image_map[url] = data
        photos[item["offer_id"]] = url
    settings = Settings(
        _env_file=None,
        database_url="sqlite+aiosqlite:///" + str(folder / "theme.db"),
        llm_backend="basic",
        background_research_enabled=False,
        presentation_worker_enabled=False,
        auth_required=True,
        presentation_storage=folder / "presentations",
        search_requests_per_minute=1000,
    )
    app = create_app(settings, [])
    async with app.state.engine.begin() as db:
        await db.run_sync(Base.metadata.create_all)
    products = normalize(**fixture)
    for product in products:
        product.primary_image = photos.get(product.id)
        product.images = [product.primary_image] if product.primary_image else []
    products += [
        Product(
            id="vto:theme-" + key,
            supplier="vto",
            name=name,
            brand="Демонстрационный каталог",
            material="металл",
            original_price=price,
            original_currency="KZT",
            stock_quantity=stock,
            colors=[ProductColor(original_color=color)],
            source_url="https://vto.kz/product/theme-fixture-" + key + "/",
        )
        for key, name, price, stock, color in [
            ("pen", "Ручка — тестовый товар", 1200, 300, "чёрный"),
            (
                "pen-long",
                "Ручка с длинным названием — тестовый товар для проверки переносов",
                None,
                None,
                "белый",
            ),
            ("bottle", "Бутылка — тестовый товар", 3500, 500, "синий"),
        ]
    ]
    if DROPDOWNS:
        products += [
            Product(
                id=f"vto:dropdown-demo-{i}",
                supplier="vto",
                name=f"Ручка демонстрационная {i}",
                brand=None
                if i == 0
                else (
                    "Демонстрационный бренд с очень длинным названием для проверки переноса"
                    if i == 1
                    else f"Демо бренд {i:02}"
                ),
                material="Металл с длинным описанием декоративного покрытия и натуральными вставками"
                if i == 1
                else f"Демонстрационный материал {i:02}",
                metadata={"size": "A5" if i % 2 else "A4"},
                original_price=1000 + i,
                original_currency="KZT",
                stock_quantity=300,
                source_url=f"https://vto.kz/product/dropdown-fixture-{i}/",
            )
            for i in range(20)
        ]
    if WORKSPACE_LAYOUT:
        from check_workspace_layout import fixture_products

        products += fixture_products()
    await app.state.index.ingest(products)
    server = uvicorn.Server(
        uvicorn.Config(
            app, host="127.0.0.1", port=18012, log_level="error", access_log=False
        )
    )
    api_task = asyncio.create_task(server.serve())
    web = edge = proxy = browser = None
    checks, mutations = [], []
    async with (
        httpx.AsyncClient(base_url="http://127.0.0.1:18012", timeout=20) as api,
        httpx.AsyncClient(base_url="http://127.0.0.1:18012", timeout=20) as browser_api,
        httpx.AsyncClient(base_url="http://127.0.0.1:3000", timeout=20) as assets,
    ):
        try:
            for _ in range(80):
                if server.started:
                    break
                await asyncio.sleep(0.1)
            assert server.started
            response = await api.post(
                "/api/auth/register",
                json={"name": "Тестовая тема", "password": "Theme-password-123"},
            )
            response.raise_for_status()
            (
                await api.post(
                    "/api/chats", json={"project_name": "Подборка для команды"}
                )
            ).raise_for_status()
            project = (await api.get("/api/projects")).json()[0]
            result = await api.post(
                f"/api/projects/{project['id']}/requests",
                json={"query": "Рюкзаки, ручки, бутылки"},
            )
            result.raise_for_status()
            research = result.json()["research"]
            assert research["matching_count"] == len(products)
            for offer in ["vto:1823109", "vto:1823108", "vto:theme-pen-long"]:
                (
                    await api.post(
                        f"/api/projects/{project['id']}/collection/items",
                        json={"offer_id": offer},
                    )
                ).raise_for_status()
            collection = (
                await api.get(f"/api/projects/{project['id']}/collection")
            ).json()
            doc = (
                await api.post(
                    f"/api/projects/{project['id']}/presentations",
                    json={"collection_version": collection["version"]},
                )
            ).json()
            original_draft = json.dumps(doc["draft"], sort_keys=True)

            async def forward(reader, writer):
                try:
                    try:
                        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
                    except (asyncio.IncompleteReadError, TimeoutError):
                        return  # A browser may abandon a speculative TCP connection.
                    lines = head.decode("latin1").split("\r\n")
                    method, path, _ = lines[0].split(" ", 2)
                    assert path.startswith("/") and not path.startswith("//")
                    headers = dict(
                        line.split(": ", 1) for line in lines[1:] if ": " in line
                    )
                    length = int(
                        next(
                            (
                                v
                                for k, v in headers.items()
                                if k.lower() == "content-length"
                            ),
                            "0",
                        )
                    )
                    assert length <= 262144
                    body = await reader.readexactly(length)
                    headers = {
                        k: v
                        for k, v in headers.items()
                        if k.lower()
                        not in ("connection", "accept-encoding", "transfer-encoding")
                    }
                    if not any(k.lower() == "cookie" for k in headers):
                        headers["cookie"] = (
                            ""  # The proxy must not resurrect a cleared browser session.
                        )
                    parsed = urlsplit(path)
                    image_url = (
                        parse_qs(parsed.query).get("url", [""])[0]
                        if parsed.path == "/_next/image"
                        else "http://127.0.0.1:18013" + parsed.path
                    )
                    if image_url in image_map:
                        response = httpx.Response(
                            200,
                            content=image_map[image_url],
                            headers={"Content-Type": "image/jpeg"},
                        )
                    else:
                        if path.startswith("/api/") and method != "GET":
                            mutations.append(method + " " + parsed.path)
                        response = await (
                            browser_api if path.startswith("/api/") else assets
                        ).request(method, path, headers=headers, content=body)
                    if (
                        DROPDOWNS
                        and response.status_code >= 400
                        and parsed.path not in ("/api/auth/me", "/favicon.ico")
                    ):
                        print(
                            "Fixture HTTP error",
                            method,
                            parsed.path,
                            response.status_code,
                            flush=True,
                        )
                    payload = response.content
                    result_headers = [
                        (k, v)
                        for k, v in response.headers.multi_items()
                        if k.lower()
                        not in (
                            "content-encoding",
                            "content-length",
                            "connection",
                            "transfer-encoding",
                        )
                    ]
                    output = (
                        f"HTTP/1.1 {response.status_code} {response.reason_phrase}\r\n"
                        + "".join(k + ": " + v + "\r\n" for k, v in result_headers)
                    )
                    writer.write(
                        output.encode("latin1")
                        + f"Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n".encode()
                        + payload
                    )
                    await writer.drain()
                finally:
                    writer.close()
                    with suppress(ConnectionError):
                        await writer.wait_closed()

            proxy = await asyncio.start_server(forward, "127.0.0.1", 18013)
            with (folder / "web.log").open("ab") as log:
                if 3000 not in ports:
                    web = await asyncio.to_thread(
                        subprocess.Popen,
                        [
                            "C:/Program Files/nodejs/node.exe",
                            "node_modules/next/dist/bin/next",
                            "start",
                            "--hostname",
                            "127.0.0.1",
                        ],
                        cwd=ROOT / "apps/web",
                        stdout=log,
                        stderr=log,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
            for _ in range(80):
                try:
                    if (await assets.get("/")).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.1)
            with (folder / "edge.log").open("ab") as log:
                edge = await asyncio.to_thread(
                    subprocess.Popen,
                    [
                        "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
                        "--headless=new",
                        "--disable-extensions",
                        "--remote-debugging-port=9226",
                        "--user-data-dir=" + str(folder / "edge-profile"),
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
            browser = b = ThemeBrowser()
            await b.connect({})
            await b.call("Runtime.enable")
            await b.call(
                "Page.addScriptToEvaluateOnNewDocument",
                {
                    "source": "if(location.origin==='http://127.0.0.1:18013')localStorage.setItem('forma-chat-id',"
                    + json.dumps(project["chat_id"])
                    + ")"
                },
            )

            async def check(label, expression):
                try:
                    await b.wait(expression, timeout=20)
                except TimeoutError:
                    print(
                        "FAILED",
                        label,
                        await b.js(
                            "({url:location.href,dialogs:[...document.querySelectorAll('dialog[open]')].map(e=>e.className),theme:document.documentElement.dataset.siteTheme,themeValues:[...document.querySelectorAll('.theme-switch .ui-select-trigger')].map(e=>e.textContent),alerts:[...document.querySelectorAll('[role=alert]')].map(e=>e.textContent),text:document.body.innerText.slice(0,900)})"
                        ),
                        flush=True,
                    )
                    await b.shot(OUTPUT / "failure.png")
                    raise
                checks.append({"check": label, "status": "PASS"})

            async def theme(value):
                await b.js(
                    "document.querySelector('.theme-switch .ui-select-trigger').click()"
                )
                await b.wait(
                    "!!document.querySelector('.theme-switch .ui-select-popup:popover-open')"
                )
                await b.js(
                    "[...document.querySelectorAll('.theme-switch [role=option]')]["
                    + ("1" if value == "pulse" else "0")
                    + "].click()"
                )
                await check(
                    "Theme " + value,
                    "document.documentElement.dataset.siteTheme===" + json.dumps(value),
                )
                await asyncio.sleep(0.2)

            async def shot(name):
                await b.js("document.fonts.ready.then(()=>true)")
                await asyncio.sleep(0.2)
                await b.shot(OUTPUT / (name + ".png"))

            contrasts = []

            async def contrast(selector, minimum=4.5, prop="color"):
                value = await b.js(
                    """(arg=>{
                  const node=document.querySelector(arg.selector);if(!node)throw Error(arg.selector);
                  const rgb=s=>{const n=s.match(/[\\d.]+/g).map(Number);return [n[0],n[1],n[2],n[3]??1]};
                  const over=(f,b)=>f.slice(0,3).map((v,i)=>v*f[3]+b[i]*(1-f[3]));
                  let bg=[255,255,255];for(const n of [node,...(function*(n){while(n.parentElement){n=n.parentElement;yield n}})(node)].reverse())bg=over(rgb(getComputedStyle(n).backgroundColor),bg);
                  const s=getComputedStyle(node),fg=over(rgb(s.getPropertyValue(arg.prop)),bg);
                  const lum=c=>c.map(v=>v/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4).reduce((s,v,i)=>s+v*[.2126,.7152,.0722][i],0);
                  const a=lum(fg),z=lum(bg);return {ratio:(Math.max(a,z)+.05)/(Math.min(a,z)+.05),foreground:fg,background:bg};
                })"""
                    + "("
                    + json.dumps({"selector": selector, "prop": prop})
                    + ")"
                )
                assert value["ratio"] >= minimum, (selector, value)
                contrasts.append({"selector": selector, "minimum": minimum, **value})

            async def text(selector, value):
                await b.js(
                    "document.querySelector("
                    + json.dumps(selector)
                    + ").focus();document.querySelector("
                    + json.dumps(selector)
                    + ").select()"
                )
                await b.call("Input.insertText", {"text": value})

            async def key(value):
                code = {
                    "Escape": 27,
                    "Tab": 9,
                    "Enter": 13,
                    "ArrowDown": 40,
                    "ArrowUp": 38,
                    " ": 32,
                }.get(value, 0)
                await b.call(
                    "Input.dispatchKeyEvent",
                    {
                        "type": "keyDown",
                        "key": value,
                        "code": value,
                        "windowsVirtualKeyCode": code,
                    },
                )
                await b.call(
                    "Input.dispatchKeyEvent",
                    {
                        "type": "keyUp",
                        "key": value,
                        "code": value,
                        "windowsVirtualKeyCode": code,
                    },
                )

            if WORKSPACE_LAYOUT:
                from check_workspace_layout import exercise

                await exercise(
                    b,
                    api,
                    app,
                    project,
                    OUTPUT,
                    folder.name,
                    before="--before" in sys.argv,
                )
                return
            if DROPDOWNS:
                from check_dropdowns import exercise

                await exercise(
                    b, api, app, project, doc, image_map, OUTPUT, folder.name
                )
                return
            await b.call("Page.navigate", {"url": "http://127.0.0.1:18013/"})
            await check(
                "Default auth theme",
                "!!document.querySelector('.auth-card') && document.documentElement.dataset.siteTheme==='current'",
            )
            await theme("pulse")
            await text('input[name="name"]', "Тестовая тема")
            await text('input[name="password"]', "incorrect-password")
            await b.js("document.querySelector('.auth-card .primary').click()")
            await check(
                "Readable auth error",
                "!!document.querySelector('.auth-card [role=alert]')",
            )
            await contrast(".auth-card .field-error")
            await contrast(".auth-card .primary")
            await shot("pulse-auth")
            await text('input[name="password"]', "Theme-password-123")
            await b.js("document.querySelector('.auth-card .primary').click()")
            await check(
                "Login retains theme and populated grid",
                "document.querySelectorAll('.product-card').length===7 && document.documentElement.dataset.siteTheme==='pulse'",
            )
            await check(
                "Photos loaded from verified cache",
                "[...document.querySelectorAll('.product-image-wrap img')].filter(i=>i.complete&&i.naturalWidth>0).length===3",
            )
            await contrast(".product-name")
            await contrast(".product-category")
            await contrast(".research-selected-count")
            await contrast(".research-composer .studio-button:disabled")
            await contrast(".research-inline-filters .ui-select-trigger")
            target_button = ".research-hud-selected .studio-button.primary"
            point = await b.js(
                "(()=>{const r=document.querySelector("
                + json.dumps(target_button)
                + ").getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()"
            )
            await b.call("Input.dispatchMouseEvent", {"type": "mouseMoved", **point})
            await asyncio.sleep(0.2)
            await contrast(target_button)
            await b.call(
                "Input.dispatchMouseEvent",
                {"type": "mousePressed", "button": "left", "clickCount": 1, **point},
            )
            await asyncio.sleep(0.2)
            await contrast(target_button)
            await b.call(
                "Input.dispatchMouseEvent",
                {"type": "mouseMoved", "x": 1, "y": 1, "buttons": 1},
            )
            await b.call(
                "Input.dispatchMouseEvent",
                {
                    "type": "mouseReleased",
                    "button": "left",
                    "clickCount": 1,
                    "x": 1,
                    "y": 1,
                },
            )
            await check(
                "Hover/pressed CTA stays readable",
                "!document.querySelector('dialog[open]')",
            )
            await shot("pulse-grid-desktop")
            await theme("current")
            await check(
                "Current theme remains dark",
                "getComputedStyle(document.body).backgroundColor==='rgb(17, 20, 19)'",
            )
            await shot("current-grid-desktop")
            await theme("pulse")
            await b.call("Page.reload", {"ignoreCache": True})
            await check(
                "F5 restores Pulse/workspace/selection",
                "document.documentElement.dataset.siteTheme==='pulse' && document.querySelector('.research-selected-count')?.textContent==='3' && document.querySelectorAll('.product-card').length===7",
            )
            # Two actual tabs share only the visual preference through storage events.
            target = await b.call(
                "Target.createTarget", {"url": "http://127.0.0.1:18013/"}
            )
            async with httpx.AsyncClient() as probe:
                tabs = (await probe.get("http://127.0.0.1:9226/json/list")).json()
            second = ThemeBrowser()
            second.number = 0
            second.ws = await websockets.connect(
                next(
                    t["webSocketDebuggerUrl"]
                    for t in tabs
                    if t["id"] == target["targetId"]
                ),
                max_size=30_000_000,
            )
            try:
                await second.wait(
                    "!!document.querySelector('.theme-switch .ui-select-trigger') && document.documentElement.dataset.siteTheme==='pulse'"
                )
                await second.js(
                    "(()=>{document.querySelector('.theme-switch .ui-select-trigger').click();setTimeout(()=>document.querySelectorAll('.theme-switch [role=option]')[0].click(),30)})()"
                )
                await check(
                    "Other tab synchronizes Current",
                    "document.documentElement.dataset.siteTheme==='current'",
                )
                await second.js(
                    "(()=>{document.querySelector('.theme-switch .ui-select-trigger').click();setTimeout(()=>document.querySelectorAll('.theme-switch [role=option]')[1].click(),30)})()"
                )
                await check(
                    "Other tab synchronizes Pulse",
                    "document.documentElement.dataset.siteTheme==='pulse'",
                )
            finally:
                await second.ws.close()
                await b.call("Target.closeTarget", {"targetId": target["targetId"]})
            await asyncio.sleep(0.2)
            await b.js(
                "document.querySelector('.theme-switch .ui-select-trigger').focus()"
            )
            await key("Tab")
            await check(
                "Keyboard focus visible",
                "(()=>{const s=getComputedStyle(document.activeElement);return s.outlineStyle!=='none'&&parseFloat(s.outlineWidth)>=2})()",
            )
            await check(
                "Desktop layout", "document.documentElement.scrollWidth<=innerWidth"
            )
            await text(".research-inline-filters input[type=number]", "999999")
            await key("Tab")
            await check(
                "Empty state keeps selection",
                "document.querySelectorAll('.product-card').length===0 && !!document.querySelector('.research-empty') && document.querySelector('.research-selected-count').textContent==='3'",
            )
            await theme("current")
            await theme("pulse")
            await check(
                "Theme preserves effective filter",
                "document.querySelector('.research-inline-filters input[type=number]').value==='999999'",
            )
            await shot("pulse-empty")
            await b.click("Сбросить фильтры")
            await check(
                "Reset returns the original matching grid",
                "document.querySelectorAll('.product-card').length===7",
            )
            await b.js(
                "document.querySelector('.research-hud-selected .studio-button').click()"
            )
            await check(
                "Selected panel",
                "document.querySelectorAll('.selected-product').length===3",
            )
            await shot("pulse-selected")
            await key("Escape")
            await check(
                "Selected modal closed", "!document.querySelector('.sheet[open]')"
            )
            await b.js("document.querySelector('.image-details-button').click()")
            await check(
                "Product details dialog",
                "!!document.querySelector('.product-dialog[open]')",
            )
            await shot("pulse-product-details")
            await key("Escape")
            await check(
                "Details dialog closed",
                "!document.querySelector('.product-dialog[open]')",
            )
            await b.call(
                "Emulation.setDeviceMetricsOverride",
                {"width": 390, "height": 844, "deviceScaleFactor": 1, "mobile": True},
            )
            await check(
                "Mobile layout", "document.documentElement.scrollWidth<=innerWidth"
            )
            await shot("pulse-grid-mobile")
            await b.js(
                "document.querySelector('.research-mobile-filters-button').click()"
            )
            await check(
                "Mobile filters modal",
                "!!document.querySelector('.sheet[open] .ui-select-trigger')",
            )
            await contrast(".sheet[open] .ui-select-trigger")
            await shot("pulse-filters-mobile")
            await key("Escape")
            await b.call(
                "Emulation.setDeviceMetricsOverride",
                {
                    "width": 1440,
                    "height": 1000,
                    "deviceScaleFactor": 1,
                    "mobile": False,
                },
            )
            await b.js(
                "document.querySelector('.presentation-list').open=true;document.querySelector('.presentation-list button').click()"
            )
            await check(
                "Presentation editor theme",
                "!!document.querySelector('.presentation-settings') && document.documentElement.dataset.siteTheme==='pulse'",
            )
            await b.call("Page.reload", {"ignoreCache": True})
            await check(
                "Editor F5 keeps theme and document",
                "!!document.querySelector('.presentation-settings') && document.documentElement.dataset.siteTheme==='pulse'",
            )
            await shot("pulse-editor")
            await b.click("Подобрать сочетание")
            await check(
                "Palette library", "document.querySelectorAll('.palette-card').length>0"
            )
            await b.js(
                "document.querySelector('.palette-explorer-filters .ui-select-trigger').click()"
            )
            await check(
                "Open palette listbox",
                "!!document.querySelector('.palette-explorer .ui-select-popup:popover-open')",
            )
            await contrast(".palette-explorer-filters .ui-select-trigger")
            await contrast(
                ".palette-explorer .ui-select-popup [role=option][data-active=true]"
            )
            await contrast(
                ".palette-explorer .ui-select-popup [role=option]:not([data-active=true])"
            )
            await contrast(".palette-dialog-body footer .primary:disabled")
            await shot("pulse-palette-dropdown")
            await key("Escape")
            await check(
                "First Escape preserves palette dialog",
                "!!document.querySelector('.palette-explorer[open]') && !document.querySelector('.palette-explorer .ui-select-popup:popover-open')",
            )
            await b.js(
                "document.querySelector('[aria-label=\"Закрыть библиотеку палитр\"]').click()"
            )
            await theme("current")
            await b.click("Подобрать сочетание")
            await check(
                "Current palette library",
                "document.querySelectorAll('.palette-card').length>0",
            )
            await b.js(
                "document.querySelector('.palette-explorer-filters .ui-select-trigger').click()"
            )
            await check(
                "Original palette colours preserved",
                "getComputedStyle(document.querySelector('.palette-explorer .ui-select-popup:popover-open')).backgroundColor==='rgb(28, 40, 34)'",
            )
            await key("Escape")
            await b.js(
                "document.querySelector('[aria-label=\"Закрыть библиотеку палитр\"]').click()"
            )
            await theme("pulse")
            await b.click("← Вернуться к подборке")
            await check(
                "Back preserves theme and selected items",
                "document.querySelector('.research-selected-count')?.textContent==='3'",
            )
            logout_probe = await api.post(
                "/api/auth/logout", headers={"Content-Type": "application/json"}
            )
            known_defects = []
            if logout_probe.status_code == 400:
                assert logout_probe.json().get("code") == "INVALID_JSON"
                known_defects.append(
                    {
                        "status": "FAIL",
                        "scope": "pre-existing auth/API defect; no theme code runs on this endpoint",
                        "request": "POST /api/auth/logout; Content-Type application/json; empty body",
                        "http_status": 400,
                        "code": "INVALID_JSON",
                    }
                )
            else:
                logout_probe.raise_for_status()
                (
                    await api.post(
                        "/api/auth/login",
                        json={
                            "name": "Тестовая тема",
                            "password": "Theme-password-123",
                        },
                    )
                ).raise_for_status()
            await b.call("Network.clearBrowserCookies")
            await b.call("Page.reload", {"ignoreCache": True})
            await check(
                "New unauthenticated session keeps Pulse",
                "!!document.querySelector('.auth-card') && document.documentElement.dataset.siteTheme==='pulse'",
            )
            await b.call(
                "Emulation.setDeviceMetricsOverride",
                {"width": 390, "height": 844, "deviceScaleFactor": 1, "mobile": True},
            )
            await check(
                "Mobile auth layout", "document.documentElement.scrollWidth<=innerWidth"
            )
            await shot("pulse-auth-mobile")
            await text('input[name="name"]', "Тестовая тема")
            await text('input[name="password"]', "Theme-password-123")
            await b.js("document.querySelector('.auth-card .primary').click()")
            await check(
                "New login keeps Pulse and collection",
                "document.querySelector('.research-selected-count')?.textContent==='3' && document.documentElement.dataset.siteTheme==='pulse'",
            )
            await b.js(
                "document.querySelector('.presentation-list').open=true;document.querySelector('.presentation-list button').click()"
            )
            await check(
                "Reopen editor on mobile",
                "!!document.querySelector('.presentation-settings')",
            )
            await b.call(
                "Emulation.setDeviceMetricsOverride",
                {"width": 390, "height": 844, "deviceScaleFactor": 1, "mobile": True},
            )
            await b.click("Подобрать сочетание")
            await check(
                "Mobile palette", "document.querySelectorAll('.palette-card').length>0"
            )
            await b.js(
                "document.querySelector('.palette-explorer-filters .ui-select-trigger').click()"
            )
            await check(
                "Mobile popover fits viewport",
                "(()=>{const r=document.querySelector('.palette-explorer .ui-select-popup:popover-open')?.getBoundingClientRect();return !!r&&r.left>=0&&r.right<=innerWidth})()",
            )
            await shot("pulse-palette-mobile")
            await key("Escape")
            await b.js(
                "document.querySelector('[aria-label=\"Закрыть библиотеку палитр\"]').click()"
            )
            # Block hydration bundles only: the inline preference still styles
            # the server-rendered loading screen before application JS runs.
            before_origin = await b.js("performance.timeOrigin")
            await b.call(
                "Network.setBlockedURLs",
                {"urls": ["http://127.0.0.1:18013/_next/static/*.js*"]},
            )
            await b.call("Page.reload", {"ignoreCache": True})
            await check(
                "Pulse before hydration",
                f"performance.timeOrigin!=={before_origin} && !!document.querySelector('.auth-screen') && document.documentElement.dataset.siteTheme==='pulse' && getComputedStyle(document.body).backgroundColor==='rgb(243, 245, 242)'",
            )
            await b.call("Network.setBlockedURLs", {"urls": []})
            await b.call("Page.reload", {"ignoreCache": True})
            await check(
                "Hydration restores editor after blocked-bundle check",
                "!!document.querySelector('.presentation-settings') && document.documentElement.dataset.siteTheme==='pulse'",
            )
            assert (
                json.dumps(
                    (await api.get(f"/api/presentations/{doc['id']}")).json()["draft"],
                    sort_keys=True,
                )
                == original_draft
            )
            assert (
                await api.get(f"/api/projects/{project['id']}/collection")
            ).json() == collection
            assert not any("/presentations/" in value for value in mutations), mutations
            checks.append(
                {
                    "check": "Theme/palette browsing leaves document and collection unchanged",
                    "status": "PASS",
                }
            )
            assert not b.errors, b.errors
            checks.append({"check": "No hydration/runtime errors", "status": "PASS"})
            result = {
                "status": "PARTIAL" if known_defects else "PASS",
                "theme_status": "PASS",
                "run": folder.name,
                "checks": checks,
                "contrast": contrasts,
                "known_unrelated_defects": known_defects,
                "browser": "installed Microsoft Edge",
                "main_services_initial": initial_services,
                "database": "disposable SQLite; real existing API; main PostgreSQL untouched",
                "photos": "three verified cached real VTO variants; other cards deliberately lack images",
                "runtime_errors": b.errors,
                "screenshots": [
                    p.name for p in OUTPUT.glob("*.png") if p.name != "failure.png"
                ],
                "mutations": mutations,
                "pdf_generation": "NOT RUN: site theme only",
            }
            (OUTPUT / "acceptance.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2), "utf-8"
            )
            print("Theme browser checks:", len(checks), "PASS")
        finally:
            if browser:
                await browser.ws.close()
            for process in (edge, web):
                if process:
                    p = psutil.Process(process.pid)
                    assert (
                        (str(folder / "edge-profile") in " ".join(p.cmdline()))
                        if process == edge
                        else Path(p.cwd()).resolve() == (ROOT / "apps/web").resolve()
                    )
                    children = p.children(recursive=True)
                    for child in reversed(children):
                        try:
                            child.terminate()
                        except psutil.NoSuchProcess:
                            pass
                    p.terminate()
                    _, alive = await asyncio.to_thread(
                        psutil.wait_procs, [p, *children], timeout=5
                    )
                    for child in alive:
                        child.kill()
            if proxy:
                proxy.close()
                await proxy.wait_closed()
            server.should_exit = True
            await asyncio.wait_for(api_task, 10)
            assert folder.resolve().parent == (ROOT / ".local").resolve()
            shutil.rmtree(folder)
            remaining = {
                c.laddr.port
                for c in psutil.net_connections(kind="tcp")
                if c.laddr and c.status == "LISTEN"
            }
            assert not remaining & {18012, 18013, 9226}
            assert sorted(remaining & {3000, 8000, 55432, 11434}) == initial_services
            receipt = OUTPUT / "acceptance.json"
            if receipt.exists():
                value = json.loads(receipt.read_text("utf-8"))
                if value.get("run") == folder.name:
                    value["cleanup"] = {
                        "disposable_database_removed": True,
                        "test_processes_stopped": True,
                        "main_services_final": initial_services,
                    }
                    receipt.write_text(
                        json.dumps(value, ensure_ascii=False, indent=2), "utf-8"
                    )


if __name__ == "__main__":
    asyncio.run(main())
