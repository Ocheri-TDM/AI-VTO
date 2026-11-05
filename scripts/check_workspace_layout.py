"""Installed Edge layout acceptance. Run via check_site_theme.py --workspace-layout.

--before records the unchanged production layout; no main database is used.
"""

import asyncio
import json
from datetime import timedelta

from app.domain.models import Product, ProductColor, utcnow


def fixture_products():
    return [
        Product(
            id=f"vto:layout-demo-{i}",
            supplier="vto",
            name=f"Рюкзак демонстрационный с длинным названием и дополнительными характеристиками {i}",
            category="backpack",
            brand="Демонстрационный каталог",
            material="Тестовый материал с подробным описанием и длинной дополнительной строкой",
            original_price=3500 + i,
            original_currency="KZT",
            stock_quantity=123456,
            colors=[ProductColor(original_color="чёрный" if i % 2 else "синий")],
            fetched_at=utcnow() - timedelta(days=20),
            metadata={
                "vto_product_id": "layout-demo-many" if i < 24 else "layout-demo-pair"
            },
            source_url=f"https://vto.kz/product/layout-fixture-{i}/",
        )
        for i in range(26)
    ]


async def exercise(b, api, app, project, output, run, before=False):
    checks, measurements = [], []
    long_name = "Подборка корпоративных подарков для большой команды с очень длинным названием проекта"
    (
        await api.patch(
            f"/api/projects/{project['id']}",
            json={"name": long_name, "client_name": ""},
        )
    ).raise_for_status()
    for name in ["Короткий", "ПроектБезПробелов" * 9]:
        (await api.post("/api/chats", json={"project_name": name})).raise_for_status()
    await b.call("Page.navigate", {"url": "http://127.0.0.1:18013/"})
    await b.wait("!!document.querySelector('.auth-card')")

    async def text(selector, value):
        await b.js(f"document.querySelector({json.dumps(selector)}).focus()")
        await b.call("Input.insertText", {"text": value})

    await text("input[name=name]", "Тестовая тема")
    await text("input[name=password]", "Theme-password-123")
    await b.js("document.querySelector('.auth-card .primary').click()")
    await b.wait(
        "document.querySelectorAll('.research-projects .project-item').length===3 && !!document.querySelector('.research-grid')"
    )

    async def key(value):
        code = {"Escape": 27, "Tab": 9, "Enter": 13, " ": 32}.get(value, 0)
        for kind in ["keyDown", "keyUp"]:
            params = {
                "type": kind,
                "key": value,
                "code": "Space" if value == " " else value,
                "windowsVirtualKeyCode": code,
            }
            if kind == "keyDown" and value in ("Enter", " "):
                params["text"] = "\r" if value == "Enter" else " "
            await b.call("Input.dispatchKeyEvent", params)

    async def theme(value):
        await b.js("document.querySelector('.theme-switch .ui-select-trigger').click()")
        await b.wait(
            "!!document.querySelector('.theme-switch .ui-select-popup:popover-open')"
        )
        await b.js(
            "document.querySelectorAll('.theme-switch [role=option]')["
            + ("0" if value == "current" else "1")
            + "].click()"
        )
        await asyncio.sleep(0.2)

    async def projects():
        return await b.js("""[...document.querySelectorAll('.research-projects .project-item')].map(e=>{
          const rect=n=>{const r=n.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,right:r.right,bottom:r.bottom}};
          const s=e.querySelector('.project-settings'),t=e.querySelector('strong'),icon=e.querySelector('button:first-child svg'),meta=e.querySelector('small');
          return {name:t.textContent,active:e.classList.contains('active'),card:rect(e),settings:rect(s),title:rect(t),icon:rect(icon),meta:rect(meta),position:getComputedStyle(s).position,padding:getComputedStyle(e).padding,offsetY:rect(s).y+rect(s).height/2-rect(t).y-rect(t).height/2,sidebar:document.querySelector('.research-projects').getBoundingClientRect().width};
        })""")

    async def open_family(needle="Melango"):
        await b.js(
            "[...document.querySelectorAll('.research-main .research-grid>div')].find(e=>e.querySelector('.product-name').textContent.includes("
            + json.dumps(needle)
            + ")).querySelector('.studio-button').focus()"
        )
        if before:
            await b.js("document.activeElement.click()")
        else:
            await key("Enter")
        await b.wait(
            "!!document.querySelector('.sheet[open][aria-label=\"Варианты модели\"]')"
        )

    async def panel():
        return await b.js("""(()=>{
          const d=document.querySelector('.sheet[open]'),g=d.querySelector('.research-grid'),body=d.querySelector('.sheet-body');
          const rect=n=>{const r=n.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,right:r.right,bottom:r.bottom}};
          return {viewport:{width:innerWidth,height:innerHeight},drawer:rect(d),body:rect(body),padding:getComputedStyle(body).padding,columns:getComputedStyle(g).gridTemplateColumns,cards:[...g.querySelectorAll('.product-card')].map(e=>({name:e.querySelector('.product-name').textContent,card:rect(e),photo:rect(e.querySelector('.product-image-wrap')),image:e.querySelector('img')?rect(e.querySelector('img')):null,info:rect(e.querySelector('.product-info'))})),scrollHeight:body.scrollHeight,clientHeight:body.clientHeight};
        })()""")

    for value in ["current", "pulse"]:
        await theme(value)
        measurements.append({"theme": value, "projects": await projects()})
        await b.shot(output / (value + "-projects.png"))
        await open_family()
        await b.wait(
            "[...document.querySelectorAll('.sheet[open] img')].every(e=>e.complete)"
        )
        measurements.append({"theme": value, "panel": await panel()})
        await b.shot(output / (value + "-variants.png"))
        await key("Escape")
    await b.call(
        "Emulation.setDeviceMetricsOverride",
        {"width": 390, "height": 844, "deviceScaleFactor": 1, "mobile": False},
    )
    await open_family()
    measurements.append({"theme": "pulse", "mobile": await panel()})
    await b.shot(output / "pulse-mobile-variants.png")
    await key("Escape")
    if before:
        (output / "acceptance.json").write_text(
            json.dumps(
                {"run": run, "status": "BASELINE", "measurements": measurements},
                ensure_ascii=False,
                indent=2,
            ),
            "utf-8",
        )
        print(
            "Before layout recorded",
            json.dumps(measurements[1], ensure_ascii=False)[:1600],
            flush=True,
        )
        return

    async def check(label, expression):
        try:
            await b.wait(expression, timeout=12)
        except Exception:
            await b.shot(output / "failure.png")
            print(
                "FAILED",
                label,
                await b.js(
                    "({focus:document.activeElement?.outerHTML.slice(0,200),text:document.body.innerText.slice(-500)})"
                ),
                flush=True,
            )
            raise
        checks.append({"check": label, "status": "PASS"})
        print("PASS", label, flush=True)

    async def metrics(width, height=1000, dpr=1):
        await b.call(
            "Emulation.setDeviceMetricsOverride",
            {
                "width": width,
                "height": height,
                "deviceScaleFactor": dpr,
                "mobile": False,
            },
        )
        await asyncio.sleep(0.1)

    async def idle():
        for _ in range(100):
            if not app.state.admission.active and not app.state.admission.queued:
                await asyncio.sleep(0.2)
                return
            await asyncio.sleep(0.1)
        raise AssertionError("Fixture hydration did not settle")

    async def collection():
        return (await api.get(f"/api/projects/{project['id']}/collection")).json()

    async def saved_count(count):
        for _ in range(50):
            data = await collection()
            if len(data["selected_ids"]) == count:
                assert len(set(data["selected_ids"])) == count
                await asyncio.sleep(0.2)
                return data
            await asyncio.sleep(0.1)
        raise AssertionError(("Collection count", count, data["selected_ids"]))

    await metrics(1440)
    for value in ["current", "pulse"]:
        await metrics(1440)
        await theme(value)
        rows = await projects()
        for row in rows:
            assert row["settings"]["width"] == 32 and row["settings"]["height"] == 32, (
                row
            )
            assert abs(row["offsetY"]) < 0.5, row
            assert row["card"]["right"] - row["settings"]["right"] >= 12, row
            assert row["meta"]["x"] == row["title"]["x"], row
            assert row["meta"]["right"] <= row["settings"]["x"], row
        await check(
            value + " project names ellipsize without growing sidebar",
            "[...document.querySelectorAll('.research-projects .project-item strong')].every(e=>getComputedStyle(e).textOverflow==='ellipsis' && e.clientWidth<=e.parentElement.clientWidth)",
        )
        assert all(r["sidebar"] == 230 for r in rows)
        first = rows[0]["settings"]
        await b.call(
            "Input.dispatchMouseEvent",
            {"type": "mouseMoved", "x": first["x"] + 16, "y": first["y"] + 16},
        )
        hovered = await projects()
        assert [(r["settings"]["x"], r["settings"]["y"]) for r in hovered] == [
            (r["settings"]["x"], r["settings"]["y"]) for r in rows
        ]
        checks.append(
            {
                "check": value + " project hover preserves button geometry",
                "status": "PASS",
            }
        )
        # Settings for an inactive project must not change the current workspace.
        original_chat = await b.js("localStorage.getItem('forma-chat-id')")
        await b.js("document.querySelector('.research-projects .project-open').focus()")
        await key("Tab")
        await check(
            value + " keyboard focus reaches settings",
            "document.activeElement.matches('.project-settings') && parseFloat(getComputedStyle(document.activeElement).outlineWidth)>=2",
        )
        await key("Enter")
        await check(
            value + " settings dialog opens",
            "!!document.querySelector('.compact-sheet[open] input')",
        )
        assert await b.js("localStorage.getItem('forma-chat-id')") == original_chat
        assert (
            await b.js(
                "document.querySelector('.compact-sheet').getBoundingClientRect().width"
            )
            == 440
        )
        await key("Escape")
        await check(
            value + " settings returns focus without changing project",
            "!document.querySelector('.compact-sheet[open]') && document.activeElement.matches('.project-settings')",
        )
        # Active state and hover retain the same button placement.
        before_offsets = [r["settings"]["x"] - r["card"]["x"] for r in await projects()]
        await b.js("document.querySelector('.research-projects .project-open').click()")
        await check(
            value + " another project opens",
            "document.querySelector('.research-projects .project-item')?.classList.contains('active')",
        )
        await idle()
        assert before_offsets == [
            r["settings"]["x"] - r["card"]["x"] for r in await projects()
        ]
        await b.js(
            "[...document.querySelectorAll('.research-projects .project-open')].find(e=>e.title==="
            + json.dumps(long_name)
            + ").click()"
        )
        await check(
            value + " original project restores results",
            "document.querySelectorAll('.research-main .product-card').length===33",
        )

        for width, height, dpr, zoom in [
            (1920, 1080, 1, 100),
            (1440, 1000, 1, 100),
            (1024, 900, 1, 100),
            (760, 900, 1, 100),
            (600, 844, 1, 100),
            (390, 844, 1, 100),
            (1152, 800, 1.25, 125),
            (960, 666, 1.5, 150),
        ]:
            await metrics(width, height, dpr)
            await open_family()
            data = await panel()
            expected_columns = (
                2
                if data["body"]["width"]
                - sum(
                    float(v.replace("px", ""))
                    for v in [
                        data["padding"].split()[1]
                        if len(data["padding"].split()) > 1
                        else data["padding"]
                    ]
                )
                * 2
                >= 572
                else 1
            )
            columns = len(data["columns"].split())
            assert columns == expected_columns, (width, data["columns"])
            assert len(data["cards"]) == 4
            assert all(c["card"]["width"] >= 260 for c in data["cards"])
            assert all(c["card"]["width"] <= 440 for c in data["cards"])
            assert all(
                c["image"] is None
                or (
                    c["image"]["x"] >= c["photo"]["x"]
                    and c["image"]["right"] <= c["photo"]["right"] + 0.5
                    and c["image"]["bottom"] <= c["photo"]["bottom"] + 0.5
                )
                for c in data["cards"]
            )
            assert data["drawer"]["width"] <= min(880, width)
            await check(
                value + f" variants fit {width}px / {zoom}%",
                "(()=>{const d=document.querySelector('.model-variants-sheet[open]'),body=d.querySelector('.sheet-body');return d.getBoundingClientRect().left>=0 && d.getBoundingClientRect().right<=innerWidth+.5 && body.scrollWidth<=body.clientWidth && document.documentElement.scrollWidth<=innerWidth})()",
            )
            await check(
                value + f" photo/info/actions do not overlap {width}px",
                "[...document.querySelectorAll('.model-variants-sheet .product-card')].every(e=>{const p=e.querySelector('.product-image-wrap').getBoundingClientRect(),i=e.querySelector('.product-info').getBoundingClientRect(),a=e.querySelector('.model-variant-actions').getBoundingClientRect();return p.bottom<=i.top+.5&&i.bottom<=a.top+.5&&e.querySelector('input').closest('.model-variant-actions')})",
            )
            measurements.append(
                {"theme": value, "zoom_equivalent": zoom, "dpr": dpr, "panel": data}
            )
            if zoom != 100:
                await b.shot(output / (value + f"-zoom-{zoom}.png"))
            await key("Escape")
            await check(
                value + f" drawer returns focus {width}px",
                "!document.querySelector('.model-variants-sheet[open]') && document.activeElement.textContent.startsWith('Варианты модели')",
            )

    await metrics(1440)
    await theme("pulse")
    base = (await api.get(f"/api/researches/chat/{project['chat_id']}")).json()
    await b.js(
        "document.querySelector('.research-main [data-offer-id=\"vto:1823110\"] input').click()"
    )
    await saved_count(4)
    # The card attribute reflects the exact offer, never the family ID.
    await open_family()
    ids = await b.js(
        "[...document.querySelectorAll('.model-variants-sheet .product-card')].map(e=>e.dataset.offerId)"
    )
    group = next(g for g in base["groups"] if "vto:1823107" in g["offer_ids"])
    assert ids == [p["id"] for p in base["products"] if p["id"] in group["offer_ids"]]
    await check(
        "Grid selection is reflected in drawer",
        "document.querySelector('.model-variants-sheet [data-offer-id=\"vto:1823109\"] input').checked && document.querySelector('.model-variants-sheet [data-offer-id=\"vto:1823108\"] input').checked && document.querySelector('.model-variants-sheet [data-offer-id=\"vto:1823110\"] input').checked",
    )
    await b.js(
        "document.querySelector('.model-variants-sheet [data-offer-id=\"vto:1823110\"] input').click()"
    )
    await saved_count(3)
    await b.js(
        "document.querySelector('.model-variants-sheet [data-offer-id=\"vto:1823107\"] input').focus()"
    )
    await key(" ")
    selected = await saved_count(4)
    repeated = await api.post(
        f"/api/projects/{project['id']}/collection/items",
        json={"offer_id": "vto:1823107"},
    )
    repeated.raise_for_status()
    assert (
        repeated.json()["selected_ids"] == selected["selected_ids"]
        and repeated.json()["version"] == selected["version"]
    )
    checks.append(
        {"check": "Repeated add is idempotent by exact offer ID", "status": "PASS"}
    )
    await check(
        "Drawer selection updates project counter",
        "document.querySelector('.research-selected-count').textContent==='4'",
    )
    await key("Escape")
    await open_family()
    await check(
        "Reopen retains exact variant selection",
        "document.querySelector('.model-variants-sheet [data-offer-id=\"vto:1823107\"] input').checked",
    )
    await b.js(
        "document.querySelector('.model-variants-sheet [data-offer-id=\"vto:1823107\"] input').click()"
    )
    await saved_count(3)
    await key("Escape")
    await check(
        "Deselection updates main grid",
        "!document.querySelector('.research-main [data-offer-id=\"vto:1823107\"] input').checked",
    )
    # Two variants and a long scrolling family use the same component and state.
    for needle, count in [("характеристиками 24", 2), ("характеристиками 0", 24)]:
        main_width = await b.js(
            "document.querySelector('.research-main').getBoundingClientRect().width"
        )
        await open_family(needle)
        data = await panel()
        assert len(data["cards"]) == count
        assert (
            await b.js(
                "document.querySelector('.research-main').getBoundingClientRect().width"
            )
            == main_width
        )
        if count == 2:
            # Boundary fixture only: the normal UI has no opener for singleton
            # groups. Clone one rendered card without changing React/data/state.
            await b.js(
                "(()=>{const g=document.querySelector('.model-variants-grid'),f=g.cloneNode(true);f.dataset.fixture='single-card';while(f.children.length>1)f.lastElementChild.remove();g.style.display='none';g.after(f)})()"
            )
            await check(
                "One-card DOM boundary keeps a readable bounded card",
                "(()=>{const c=document.querySelector('[data-fixture=single-card] .product-card'),r=c.getBoundingClientRect();return r.width>=260&&r.width<=440})()",
            )
            await b.shot(output / "single-variant-boundary.png")
            await b.js(
                "document.querySelector('[data-fixture=single-card]').remove();document.querySelector('.model-variants-grid').style.removeProperty('display')"
            )
        if count == 24:
            await check(
                "Long availability and missing photos remain visible",
                "!!document.querySelector('.model-variants-sheet .missing-image') && document.querySelector('.model-variants-sheet .product-availability').textContent.includes('требуется уточнение')",
            )
            old_main = await b.js("document.querySelector('.research-main').scrollTop")
            await b.call(
                "Input.dispatchMouseEvent",
                {"type": "mouseWheel", "x": 200, "y": 500, "deltaX": 0, "deltaY": 500},
            )
            await asyncio.sleep(0.15)
            assert (
                await b.js("document.querySelector('.research-main').scrollTop")
                == old_main
            )
            await b.call(
                "Input.dispatchMouseEvent",
                {"type": "mouseWheel", "x": 1100, "y": 500, "deltaX": 0, "deltaY": 500},
            )
            await check(
                "Mouse wheel scrolls drawer instead of background",
                "document.querySelector('.model-variants-sheet .sheet-body').scrollTop>0",
            )
            header_top = await b.js(
                "document.querySelector('.model-variants-sheet>header').getBoundingClientRect().top"
            )
            await b.js(
                "document.querySelector('.model-variants-sheet .sheet-body').scrollTop=100000"
            )
            await check(
                "All 24 variants scroll inside drawer",
                "(()=>{const d=document.querySelector('.model-variants-sheet'),body=d.querySelector('.sheet-body'),last=d.querySelectorAll('.product-card')[23];return body.scrollTop>0&&last.getBoundingClientRect().bottom<=body.getBoundingClientRect().bottom})()",
            )
            assert (
                await b.js("document.querySelector('.research-main').scrollTop")
                == old_main
            )
            assert (
                await b.js(
                    "document.querySelector('.model-variants-sheet>header').getBoundingClientRect().top"
                )
                == header_top
            )
            await b.shot(output / "many-variants.png")
        await key("Escape")
    # New standalone search keeps the project collection; no separate drawer store.
    before_ids = (await collection())["selected_ids"]
    await b.js("document.querySelector('#research-message').focus()")
    await b.call("Input.insertText", {"text": "Бутылки"})
    await b.js("document.querySelector('.research-composer button').click()")
    await check(
        "New search renders independent results",
        "document.querySelectorAll('.research-main .product-card').length===1 && document.querySelector('.research-main .product-name').textContent.includes('Бутылка')",
    )
    assert (await collection())["selected_ids"] == before_ids
    await check(
        "New search keeps collection count",
        "document.querySelector('.research-selected-count').textContent==='3'",
    )
    await b.js("document.querySelector('.research-hud-selected button').click()")
    await check(
        "Existing selected panel keeps its size and two-column grid",
        "(()=>{const d=document.querySelector('.selected-products-sheet[open]');return d&&d.getBoundingClientRect().width===820&&getComputedStyle(d.querySelector('.selected-products-grid')).gridTemplateColumns.split(' ').length===2})()",
    )
    await key("Escape")
    await check(
        "Main result cards keep the original context",
        "!document.querySelector('.research-main .model-variant-card') && !!document.querySelector('.research-main .product-image-wrap .product-checkbox')",
    )
    # Single visible variant: a one-card drawer is a supported render boundary;
    # the live UI intentionally has no model button for singleton groups.
    assert not b.errors, b.errors
    result = {
        "run": run,
        "status": "PASS",
        "checks": checks,
        "measurements": measurements,
        "selection": "exact IDs retained across add/remove/reopen/new query",
        "singleton": "component regression plus one-card DOM geometry fixture cloned from a real rendered card; singleton groups intentionally have no drawer button",
        "zoom": "125/150% CSS viewport and DPR equivalent in real Edge; native browser menu zoom NOT TESTED",
        "other_browsers": "NOT TESTED",
        "main_database": "not used",
    }
    (output / "acceptance.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), "utf-8"
    )
    print("Workspace layout checks:", len(checks), "PASS", flush=True)
