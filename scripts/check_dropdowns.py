"""Shared dropdown acceptance in installed Edge.

Run: python scripts/check_site_theme.py --dropdowns
Uses that harness's disposable SQLite/API/proxy and real production frontend.
Never writes to PostgreSQL, imports suppliers or generates PDF files.
"""

import asyncio
import copy
import json
from pathlib import Path
from uuid import uuid4

from app.database.models import PresentationDocument, PresentationRevision

ROOT = Path(__file__).resolve().parents[1]


async def exercise(b, api, app, project, doc, image_map, output, run):
    checks, samples, contrasts = [], [], []
    # Exercise every editor control without invoking preparation/render jobs.
    material_id = str(uuid4())
    async with app.state.sessions() as db:
        row = await db.get(PresentationDocument, doc["id"])
        draft = copy.deepcopy(row.draft)
        draft["settings"]["style"] = "premium-dark"
        draft["settings"]["auto_decoration"] = {
            "version": 1,
            "enabled": True,
            "intensity": "SUBTLE",
        }
        # Second URL of this same fixture variant's photograph, never a sibling image.
        draft["items"][0]["images"].append(
            draft["items"][0]["images"][0] + "?fixture-copy=1"
        )
        draft["materials"] = [
            {
                "id": material_id,
                "role": "DECORATION",
                "filename": "Тестовый материал 251.png",
                "status": "READY",
                "enabled": True,
                "scope": "DOCUMENT",
                "expression": "NORMAL",
                "position": "AUTO",
                "crop": False,
                "focal_x": 0.5,
                "focal_y": 0.5,
                "opacity": 1,
                "harmonize": False,
                "required": False,
                "description": "",
            }
        ]
        row.draft = draft
        for version in [1, 2]:
            db.add(
                PresentationRevision(
                    document_id=row.id,
                    source_version=version,
                    status="FAILED",
                    input={},
                    result={},
                )
            )
        await db.commit()
    image_map[
        f"http://127.0.0.1:18013/api/presentations/{doc['id']}/materials/{material_id}"
    ] = (ROOT / "apps/api/app/assets/presentations/251.png").read_bytes()
    original = (await api.get(f"/api/presentations/{doc['id']}")).json()
    collection_before = (
        await api.get(f"/api/projects/{project['id']}/collection")
    ).json()
    latest = (await api.get(f"/api/researches/chat/{project['chat_id']}")).json()
    research_id = latest["id"]

    helper = """window.dc=(label,scope=document)=>[...scope.querySelectorAll('.ui-select')].find(e=>e.querySelector(':scope > label')?.textContent===label)?.querySelector(':scope > button');window.dp=()=>document.querySelector('.ui-select-popup:popover-open');"""
    await b.call("Page.addScriptToEvaluateOnNewDocument", {"source": helper})

    async def check(label, expression):
        try:
            await b.wait(expression, timeout=12)
        except Exception:
            print(
                "FAILED",
                label,
                await b.js(
                    "({focus:document.activeElement?.outerHTML.slice(0,250),popup:dp()?.outerHTML.slice(0,600),text:document.body.innerText.slice(-400)})"
                ),
                flush=True,
            )
            await b.shot(output / "failure.png")
            raise
        checks.append({"check": label, "status": "PASS"})
        print("PASS", label, flush=True)

    async def key(value, shift=False):
        code = {
            "Escape": 27,
            "Tab": 9,
            "Enter": 13,
            "ArrowDown": 40,
            "ArrowUp": 38,
            "Home": 36,
            "End": 35,
            " ": 32,
        }.get(value, 0)
        for kind in ["keyDown", "keyUp"]:
            await b.call(
                "Input.dispatchKeyEvent",
                {
                    "type": kind,
                    "key": value,
                    "windowsVirtualKeyCode": code,
                    "modifiers": 8 if shift else 0,
                },
            )

    async def tap(expression):
        await b.js(
            f"(()=>{{const n={expression},r=n.getBoundingClientRect();if(r.top<0||r.bottom>innerHeight)n.scrollIntoView({{block:'nearest'}})}})()"
        )
        await asyncio.sleep(0.1)
        r = await b.js(
            f"(()=>{{const r=({expression}).getBoundingClientRect();return {{x:r.left+r.width/2,y:r.top+r.height/2}}}})()"
        )
        for kind in ["mousePressed", "mouseReleased"]:
            await b.call(
                "Input.dispatchMouseEvent",
                {"type": kind, "button": "left", "clickCount": 1, **r},
            )

    async def open_control(label, scope="document"):
        await tap(f"dc({json.dumps(label)},{scope})")
        await b.wait("!!dp()")

    async def text(selector, value):
        await b.js(f"(()=>{{const n={selector};n.focus();n.select()}})()")
        # Input.insertText handles real Cyrillic input without synthesizing React changes.
        if value:
            await b.call("Input.insertText", {"text": value})
        else:
            await b.call(
                "Input.dispatchKeyEvent",
                {"type": "keyDown", "key": "Backspace", "windowsVirtualKeyCode": 8},
            )
            await b.call(
                "Input.dispatchKeyEvent",
                {"type": "keyUp", "key": "Backspace", "windowsVirtualKeyCode": 8},
            )

    async def theme(value):
        await open_control("Тема интерфейса")
        await key("Home" if value == "current" else "End")
        await key("Enter")
        await b.wait(
            "document.documentElement.dataset.siteTheme===" + json.dumps(value)
        )
        await asyncio.sleep(0.2)

    async def shot(name):
        await b.js("document.fonts.ready.then(()=>true)")
        await asyncio.sleep(0.15)
        await b.shot(output / (name + ".png"))

    async def geometry(label):
        await check(
            label + " fits viewport",
            "(()=>{const r=dp()?.getBoundingClientRect();return r&&r.left>=0&&r.right<=innerWidth+.5&&r.top>=0&&r.bottom<=innerHeight+.5})()",
        )
        data = await b.js(
            "(()=>{const p=dp(),s=getComputedStyle(p),r=p.getBoundingClientRect();return {theme:document.documentElement.dataset.siteTheme,bg:s.backgroundColor,text:s.color,width:r.width,height:r.height,placement:p.dataset.placement}})()"
        )
        samples.append({"control": label, **data})
        expected = (
            "rgb(253, 253, 255)" if data["theme"] == "pulse" else "rgb(28, 40, 34)"
        )
        assert data["bg"] == expected, data
        ratios = await b.js("""(()=>{
          const rgb=s=>s.match(/[\\d.]+/g).slice(0,3).map(Number);
          const luminance=s=>rgb(s).map(v=>{v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4}).reduce((a,v,i)=>a+v*[.2126,.7152,.0722][i],0);
          return [...dp().querySelectorAll('.ui-select-option-text,.ui-select-count')].map(e=>{
            let n=e,bg='rgba(0, 0, 0, 0)';while(n&&bg==='rgba(0, 0, 0, 0)'){bg=getComputedStyle(n).backgroundColor;n=n.parentElement;}
            const fg=getComputedStyle(e).color,a=luminance(fg),b=luminance(bg);
            return {text:e.textContent,fg,bg,ratio:(Math.max(a,b)+.05)/(Math.min(a,b)+.05)};
          });
        })()""")
        assert all(r["ratio"] >= 4.5 for r in ratios), (label, ratios)
        contrasts.extend({"control": label, **r} for r in ratios)

    async def state():
        return (await api.get(f"/api/researches/{research_id}")).json()

    async def idle():
        await b.call("Page.bringToFront")
        await asyncio.sleep(0.3)
        for _ in range(120):
            if app.state.admission.active == 0 and app.state.admission.queued == 0:
                await asyncio.sleep(0.15)
                return
            await asyncio.sleep(0.1)
        print(
            "Fixture admission",
            app.state.admission.active,
            app.state.admission.queued,
            flush=True,
        )
        raise AssertionError("Fixture HTTP did not settle")

    async def persisted(label, predicate):
        for _ in range(60):
            value = await state()
            if predicate(value):
                await asyncio.sleep(
                    0.25
                )  # Let the frontend receive this same write's response.
                checks.append({"check": label, "status": "PASS"})
                return value
            await asyncio.sleep(0.1)
        print(
            "PERSIST FAILED",
            label,
            value["view"]["filters"],
            await b.js(
                "({popup:!!dp(),checked:[...document.querySelectorAll('.ui-select-popup input:checked')].map(e=>e.parentElement.textContent),focus:document.activeElement?.outerHTML.slice(0,250),alerts:[...document.querySelectorAll('[role=alert]')].map(e=>e.textContent)})"
            ),
            flush=True,
        )
        await b.shot(output / "failure.png")
        raise AssertionError(label)

    await b.call("Page.navigate", {"url": "http://127.0.0.1:18013/"})
    await check("Auth renders shared theme control", "!!dc('Тема интерфейса')")
    for value in ["current", "pulse"]:
        await theme(value)
        await open_control("Тема интерфейса")
        await geometry(value + " auth theme")
        await shot(value + "-theme")
        await key("Escape")
    await text("document.querySelector('input[name=name]')", "Тестовая тема")
    await text("document.querySelector('input[name=password]')", "Theme-password-123")
    await tap("document.querySelector('.auth-card .primary')")
    await check(
        "Fixture search and collection load",
        "!!document.querySelector('.research-inline-filters') && document.querySelector('.research-selected-count')?.textContent==='3'",
    )

    for value in ["current", "pulse"]:
        await theme(value)
        for label in ["Цвет", "Материал", "Размер", "Представление товаров"]:
            await open_control(label)
            await geometry(value + " " + label)
            if label == "Материал":
                await check(
                    "Material search is focused",
                    "document.activeElement===dp().querySelector('input')",
                )
                await text("dp().querySelector('input')", "длин")
                await check(
                    "Long material wraps within bounded menu",
                    "dp().querySelectorAll('[role=option]').length===1 && dp().querySelector('.ui-select-option-text').getBoundingClientRect().height>20",
                )
                await shot(value + "-material")
                await text("dp().querySelector('input')", "zz-no-match")
                await check(
                    "Material empty result", "!!dp().querySelector('[role=status]')"
                )
            elif label == "Цвет":
                await shot(value + "-color")
            await key("Escape")
            await check(
                label + " returns focus",
                "!dp() && document.activeElement===dc(" + json.dumps(label) + ")",
            )
        await open_control("Бренд")
        await geometry(value + " brands")
        await check(
            "Brand rows use checkbox/name/count columns",
            "[...dp().querySelectorAll('.ui-checkbox-option')].every(e=>{const r=e.getBoundingClientRect(),c=e.querySelector('input').getBoundingClientRect(),n=e.querySelector('.ui-select-option-text').getBoundingClientRect(),q=e.querySelector('.ui-select-count').getBoundingClientRect();return getComputedStyle(e).display==='grid'&&c.width===18&&n.left>=c.right&&q.left>=n.right-1&&q.right<=r.right})",
        )
        await check(
            "Brands scroll separately",
            "(()=>{const n=dp().querySelector('.ui-select-options');return n.scrollHeight>n.clientHeight && getComputedStyle(n).overflowY==='auto'})()",
        )
        await shot(value + "-brands")
        # Labels toggle real checkboxes; searches do not change selected values.
        await tap(
            "[...dp().querySelectorAll('label')].find(e=>e.textContent.includes('Демо бренд 02')) .querySelector('.ui-select-option-text')"
        )
        await persisted(
            "Brand 02 applies immediately",
            lambda r: len(r["view"]["filters"]["brands"]) == 1,
        )
        await text("dp().querySelector('input:not([type=checkbox])')", "Демо бренд 03")
        await check(
            "Brand search filters rows",
            "dp().querySelectorAll('[type=checkbox]').length===1",
        )
        await key("Tab")
        await key(" ")
        selected = await persisted(
            "Second brand via keyboard",
            lambda r: len(r["view"]["filters"]["brands"]) == 2,
        )
        await text("dp().querySelector('input:not([type=checkbox])')", "")
        await check(
            "Search clear retains both selected brands",
            "dp().querySelectorAll('input:checked').length===2",
        )
        await text("dp().querySelector('input:not([type=checkbox])')", "очень длинным")
        await check(
            "Long brand wraps in its own row",
            "dp().querySelectorAll('.ui-checkbox-option').length===1 && dp().querySelector('.ui-select-option-text').getBoundingClientRect().height>35",
        )
        await shot(value + "-long-brand")
        await text(
            "dp().querySelector('input:not([type=checkbox])')", "Бренд не указан"
        )
        await check(
            "Unknown brand remains a separate value",
            "dp().querySelectorAll('[type=checkbox]').length===1",
        )
        await key("Escape")
        await b.call("Page.reload")
        await check(
            "F5 restores theme and brand filter",
            "document.documentElement.dataset.siteTheme==="
            + json.dumps(value)
            + " && dc('Бренд')?.textContent.includes('2')",
        )
        await idle()  # F5 also reloads independent project/collection/history reads.
        restored = await state()
        assert (
            restored["view"]["filters"]["brands"]
            == selected["view"]["filters"]["brands"]
        )
        await idle()
        await b.js(
            "[...document.querySelectorAll('button')].find(e=>e.textContent==='Сбросить фильтры').click()"
        )
        await persisted(
            "Reset retains original filter timing",
            lambda r: not r["view"]["filters"]["brands"],
        )
        await check(
            "Reset restores all fixture products",
            "document.querySelectorAll('.product-card').length===27",
        )

    # One open popover at a time, and an external theme/storage event updates it in place.
    await open_control("Цвет")
    await open_control("Размер")
    await check(
        "Opening a sibling dismisses previous popup",
        "document.querySelectorAll('.ui-select-popup:popover-open').length===1 && dc('Цвет').getAttribute('aria-expanded')==='false'",
    )
    await b.js(
        "localStorage.setItem('souvenir:site-theme','current');window.dispatchEvent(new StorageEvent('storage',{key:'souvenir:site-theme'}))"
    )
    await check(
        "Open popup follows external theme change",
        "!!dp() && getComputedStyle(dp()).backgroundColor==='rgb(28, 40, 34)'",
    )
    await key("Escape")
    await open_control("Размер")
    await tap("document.querySelector('.research-hud-topline strong')")
    await check("Outside click closes without choosing", "!dp()")
    await open_control("Представление товаров")
    await key("End")
    await key("Enter")
    await check(
        "Model grouping changes immediately",
        "dc('Представление товаров').textContent.includes('По моделям')",
    )
    await open_control("Представление товаров")
    await key("Home")
    await key("Tab")
    await check(
        "Tab commits and leaves single-select trigger",
        "!dp() && document.activeElement!==dc('Представление товаров') && dc('Представление товаров').textContent.includes('Все варианты')",
    )
    await b.js("dc('Цвет').focus()")
    await key("ArrowDown")
    await key("ч")
    await check(
        "Typeahead activates the matching color",
        "dp()?.querySelector('[data-active=true]')?.textContent.includes('Чёрный')",
    )
    await key("Enter")
    await persisted(
        "Keyboard color selection keeps canonical BLACK",
        lambda r: r["view"]["filters"]["colors"] == ["BLACK"],
    )
    await b.call("Page.reload")
    await check(
        "F5 restores color and collection",
        "dc('Цвет')?.textContent.includes('Чёрный') && document.querySelector('.research-selected-count')?.textContent==='3'",
    )
    await tap(
        "[...document.querySelectorAll('button')].find(e=>e.textContent==='Сбросить фильтры')"
    )
    await persisted("Color reset", lambda r: not r["view"]["filters"]["colors"])

    await open_control("Материал")
    await text("dp().querySelector('input')", "Демонстрационный материал 02")
    await check(
        "Material query finds exact raw facet",
        "dp().querySelectorAll('[role=option]').length===1",
    )
    await key("Enter")
    saved_material = await persisted(
        "Material applies immediately",
        lambda r: len(r["view"]["filters"]["materials"]) == 1,
    )
    assert saved_material["view"]["filters"]["materials"][0] in [
        v["value"] for v in latest["facets"]["material"]
    ]
    await b.js(
        "[...document.querySelectorAll('button')].find(e=>e.textContent==='Сбросить фильтры').click()"
    )
    await persisted("Material reset", lambda r: not r["view"]["filters"]["materials"])
    await open_control("Размер")
    await key("End")
    await key("Enter")
    await persisted(
        "Size applies immediately", lambda r: len(r["view"]["filters"]["sizes"]) == 1
    )
    await b.js(
        "[...document.querySelectorAll('button')].find(e=>e.textContent==='Сбросить фильтры').click()"
    )
    await persisted("Size reset", lambda r: not r["view"]["filters"]["sizes"])

    await b.call(
        "Emulation.setDeviceMetricsOverride",
        {"width": 390, "height": 844, "deviceScaleFactor": 1, "mobile": False},
    )
    for value in ["current", "pulse"]:
        await theme(value)
        await tap("document.querySelector('.research-mobile-filters-button')")
        await check("Mobile filter modal", "!!document.querySelector('.sheet[open]')")
        await open_control("Бренд", "document.querySelector('.sheet[open]')")
        await geometry(value + " mobile brands")
        await shot(value + "-mobile-brands")
        await key("Escape")
        await check(
            "Escape closes only nested dropdown",
            "!dp() && !!document.querySelector('.sheet[open]')",
        )
        await open_control("Материал", "document.querySelector('.sheet[open]')")
        await b.js("document.querySelector('.sheet-body').scrollTop=100")
        await geometry(value + " scrolled modal material")
        await key("Escape")
        await key("Escape")
        await check(
            "Second Escape closes filter modal",
            "!document.querySelector('.sheet[open]')",
        )

    await b.call(
        "Emulation.setDeviceMetricsOverride",
        {"width": 1440, "height": 1000, "deviceScaleFactor": 1, "mobile": False},
    )
    await b.call("Page.navigate", {"url": "about:blank"})
    await b.call(
        "Page.navigate", {"url": f"http://127.0.0.1:18013/#presentation={doc['id']}"}
    )
    await check(
        "Editor populated", "!!document.querySelector('.presentation-settings')"
    )
    for value in ["current", "pulse"]:
        await theme(value)
        # Expand fixture controls, never save or generate a document.
        await b.js("document.querySelector('.presentation-materials').open=true")
        for label, scope in [
            ("Фото этого варианта", "document"),
            ("Визуальное направление", "document"),
            ("Выразительность", "document.querySelector('.presentation-settings')"),
            ("Версия презентации", "document"),
            ("Где использовать", "document.querySelector('.material-options')"),
            ("Выразительность", "document.querySelector('.material-options')"),
            ("Размещение", "document.querySelector('.material-options')"),
        ]:
            await open_control(label, scope)
            await geometry(value + " editor " + label)
            await key("Escape")
        await open_control("Визуальное направление")
        await shot(value + "-editor")
        await key("Escape")
        await b.click("Подобрать сочетание")
        await check(
            "Palette catalog loaded",
            "document.querySelectorAll('.palette-card').length>0",
        )
        await b.js(
            "document.querySelector('.palette-explorer-filters details').open=true"
        )
        palette_before = await b.js(
            "[...document.querySelectorAll('.palette-scheme')].map(e=>e.getAttribute('style'))"
        )
        for label in ["Ведущее направление", "Характер", "Применение"]:
            await open_control(label)
            await geometry(value + " palette " + label)
            await check(
                "Popup remains in palette modal DOM",
                "document.querySelector('.palette-dialog-body').contains(dp())",
            )
            await key("Escape")
            await check(
                "Palette modal survives first Escape",
                "!!document.querySelector('.palette-explorer[open]') && !dp()",
            )
        await open_control("Ведущее направление")
        await shot(value + "-palette")
        await b.js(
            "localStorage.setItem('souvenir:site-theme',document.documentElement.dataset.siteTheme==='pulse'?'current':'pulse');window.dispatchEvent(new StorageEvent('storage',{key:'souvenir:site-theme'}))"
        )
        await check(
            "Palette popup retains focus on theme change",
            "!!dp() && document.activeElement===dc('Ведущее направление')",
        )
        assert palette_before == await b.js(
            "[...document.querySelectorAll('.palette-scheme')].map(e=>e.getAttribute('style'))"
        )
        await key("Escape")
        await b.click("Отмена", "document.querySelector('.palette-explorer')")

    await theme("pulse")
    await b.call(
        "Emulation.setDeviceMetricsOverride",
        {"width": 390, "height": 700, "deviceScaleFactor": 1, "mobile": False},
    )
    await b.click("Подобрать сочетание")
    await check(
        "Mobile palette loaded", "document.querySelectorAll('.palette-card').length>0"
    )
    await b.js("document.querySelector('.palette-explorer-filters details').open=true")
    await open_control("Применение")
    await geometry("Mobile palette")
    await shot("pulse-mobile-palette")
    await key("Escape")
    await b.js("document.querySelector('.palette-explorer').scrollTop=150")
    await open_control("Ведущее направление")
    await geometry("Scrolled palette modal")
    await key("Escape")
    await b.click("Отмена", "document.querySelector('.palette-explorer')")
    # Actual bottom-edge case using the existing material trigger in the scrolled editor.
    await b.js(
        "dc('Размещение',document.querySelector('.material-options')).scrollIntoView({block:'end'})"
    )
    await open_control("Размещение", "document.querySelector('.material-options')")
    await geometry("Bottom-edge material")
    await check(
        "Bottom-edge popup opens above trigger", "dp().dataset.placement==='top'"
    )
    await shot("bottom-edge")
    await key("Escape")
    await b.call(
        "Emulation.setEmulatedMedia",
        {"features": [{"name": "forced-colors", "value": "active"}]},
    )
    await open_control("Размещение", "document.querySelector('.material-options')")
    await check(
        "System high contrast remains enabled",
        "matchMedia('(forced-colors:active)').matches && getComputedStyle(dp()).forcedColorAdjust==='auto'",
    )
    await key("Escape")
    await b.call("Emulation.setEmulatedMedia", {"features": []})

    final = (await api.get(f"/api/presentations/{doc['id']}")).json()
    assert final == original, "Dropdown browsing changed document/revisions"
    assert (
        await api.get(f"/api/projects/{project['id']}/collection")
    ).json() == collection_before
    checks.append(
        {
            "check": "Document, palette, snapshots and collection unchanged",
            "status": "PASS",
        }
    )
    assert not b.errors, b.errors
    result = {
        "run": run,
        "status": "PASS",
        "browser": "installed Microsoft Edge",
        "checks": checks,
        "geometry": samples,
        "contrast": {
            "samples": len(contrasts),
            "minimum": min(r["ratio"] for r in contrasts),
            "measurements": contrasts,
        },
        "errors": b.errors,
        "fixture": "27 isolated offers; real Melango source photos plus explicitly labeled demo facets/material/revisions",
        "postgresql": "untouched; existing main services retained",
        "other_browsers": "NOT TESTED",
        "screen_reader": "NOT TESTED",
    }
    (output / "acceptance.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), "utf-8"
    )
    print("Dropdown checks:", len(checks), "PASS", flush=True)
