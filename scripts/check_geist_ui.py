"""Real Edge UI/font acceptance with disposable data. No PDF generation.

Run via scripts/check_site_theme.py --font-ui [--before].
"""

import asyncio
import copy
import hashlib
import json
from pathlib import Path

from app.database.models import PresentationDocument, PresentationRevision

ROOT = Path(__file__).resolve().parents[1]
STRINGS = [
    "Souvenir Studio",
    "Ручка шариковая Tippo",
    "Рюкзак-мешок Melango — чёрный",
    "Выбранные товары",
    "Презентация проекта",
    "Ә Ғ Қ Ң Ө Ұ Ү Һ І",
    "ә ғ қ ң ө ұ ү һ і",
    "2 084 ₸ · 16 928 ₸ · 1 000 000 ₸",
    "№ 10 — от 250 шт.",
    "500 мл · 10 000 мА·ч",
]


async def exercise(b, api, app, project, doc, image_map, output, run, before=False):
    checks, fonts, geometry = [], [], []
    name = "Подборка корпоративных подарков для большой команды с длинным названием"
    (
        await api.patch(
            f"/api/projects/{project['id']}", json={"name": name, "client_name": ""}
        )
    ).raise_for_status()
    # An unchanged public fixture supplies preview bytes; no rendering job runs.
    public = ROOT / "apps/api/tests/fixtures/presentations/premium-dark-v3"
    pdf = (public / "premium-dark.pdf").read_bytes()
    async with app.state.sessions() as db:
        row = await db.get(PresentationDocument, doc["id"])
        draft = copy.deepcopy(row.draft)
        draft["settings"]["style"] = "premium-dark"
        row.draft = draft
        revision = PresentationRevision(
            document_id=row.id,
            source_version=row.version,
            status="READY",
            input={},
            result={
                "pdf": "font-fixture.pdf",
                "page_count": 2,
                "sha256": hashlib.sha256(pdf).hexdigest(),
                "bytes": len(pdf),
                "pages": [{"page": 1}, {"page": 2}],
            },
        )
        db.add(revision)
        await db.commit()
        revision_id = revision.id
    preview_hashes = {}
    for n, filename in [(1, "cover.png"), (2, "variants.png")]:
        data = (public / filename).read_bytes()
        image_map[
            f"http://127.0.0.1:18013/api/presentations/{doc['id']}/revisions/{revision_id}/pages/{n}"
        ] = data
        preview_hashes[str(n)] = hashlib.sha256(data).hexdigest()
    original = (await api.get(f"/api/presentations/{doc['id']}")).json()
    collection = (await api.get(f"/api/projects/{project['id']}/collection")).json()
    await b.call("DOM.enable")
    await b.call("CSS.enable")
    helper = "window.dc=(label,scope=document)=>[...scope.querySelectorAll('.ui-select')].find(e=>e.querySelector(':scope > label')?.textContent===label)?.querySelector(':scope > button');window.dp=()=>document.querySelector('.ui-select-popup:popover-open');"
    await b.call("Page.addScriptToEvaluateOnNewDocument", {"source": helper})

    async def check(label, expression):
        try:
            await b.wait(expression, timeout=15)
        except Exception:
            await b.shot(output / "failure.png")
            print(
                "FAILED",
                label,
                await b.js(
                    "({url:location.href,text:document.body.innerText.slice(-500)})"
                ),
                flush=True,
            )
            raise
        checks.append({"check": label, "status": "PASS"})
        print("PASS", label, flush=True)

    async def shot(name):
        await b.js("document.fonts.ready.then(()=>true)")
        await b.js(
            "new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>resolve(true))))"
        )
        await asyncio.sleep(0.3)
        await b.shot(output / (name + ".png"))

    async def text(selector, value):
        await b.js(f"(()=>{{const n={selector};n.focus();n.select()}})()")
        await b.call("Input.insertText", {"text": value})

    async def key(value):
        for kind in ["keyDown", "keyUp"]:
            p = {
                "type": kind,
                "key": value,
                "code": value,
                "windowsVirtualKeyCode": {"Escape": 27, "Enter": 13, "Tab": 9}.get(
                    value, 0
                ),
            }
            if value == "Enter" and kind == "keyDown":
                p["text"] = "\r"
            await b.call("Input.dispatchKeyEvent", p)

    async def theme(value):
        await b.js("dc('Тема интерфейса').click()")
        await b.wait("!!dp()")
        await b.js(
            "document.querySelectorAll('.theme-switch [role=option]')["
            + ("1" if value == "pulse" else "0")
            + "].click()"
        )
        await asyncio.sleep(0.15)

    async def used(label, selector, expected="geist"):
        root = (await b.call("DOM.getDocument"))["root"]["nodeId"]
        node = (
            await b.call("DOM.querySelector", {"nodeId": root, "selector": selector})
        )["nodeId"]
        assert node, selector
        actual = await b.call("CSS.getPlatformFontsForNode", {"nodeId": node})
        css = await b.js(
            "(()=>{const s=getComputedStyle(document.querySelector("
            + json.dumps(selector)
            + "));return {family:s.fontFamily,weight:s.fontWeight,spacing:s.letterSpacing,numeric:s.fontVariantNumeric}})()"
        )
        fonts.append(
            {
                "check": label,
                "selector": selector,
                "css": css,
                "actual": actual["fonts"],
            }
        )
        if not before and expected == "geist":
            fallback = [
                f
                for f in actual["fonts"]
                if not ("geist" in f["familyName"].lower() and f["isCustomFont"])
            ]
            content = await b.js(
                "document.querySelector(" + json.dumps(selector) + ").textContent"
            )
            assert actual["fonts"] and (
                not fallback
                or (
                    "₸" in content
                    and sum(f["glyphCount"] for f in fallback) == content.count("₸")
                    and all(
                        f["familyName"].startswith(("Segoe UI", "Arial"))
                        for f in fallback
                    )
                )
            ), (label, actual)
        elif expected == "fallback":
            assert actual["fonts"] and all(
                "geist" not in f["familyName"].lower() for f in actual["fonts"]
            ), (label, actual)

    async def specimen(value):
        await b.js(
            """(()=>{
          const d=document.createElement('dialog');d.id='font-specimen';d.style.cssText='width:min(1360px,calc(100vw - 48px));max-height:90vh;padding:24px;border:1px solid var(--border);background:var(--panel);color:var(--text);border-radius:12px';
          const grid=document.createElement('div');grid.style.cssText='display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:20px';d.append(grid);
          const lines="""
            + json.dumps(STRINGS)
            + """;
          for(const weight of [400,500,600,700]){const section=document.createElement('section');section.style.fontWeight=weight;
            const h=document.createElement('p');h.textContent='Geist Sans · '+weight;section.append(h);
            lines.forEach((text,i)=>{const p=document.createElement('p'),s=document.createElement('span');s.id='glyph-'+weight+'-'+i;s.textContent=text;p.style.cssText='font-size:16px;line-height:1.6;overflow-wrap:anywhere;margin:0 0 12px';p.append(s);section.append(p)});
            const numbers=document.createElement('div');numbers.className='numeric-probe';numbers.dataset.weight=weight;numbers.style.cssText='font-size:32px;font-variant-numeric:tabular-nums;display:flex';
            for(const digit of '0123456789'){const s=document.createElement('span');s.textContent=digit;numbers.append(s)}section.append(numbers);grid.append(section);
          }
          document.body.append(d);d.showModal();
        })()"""
        )
        await b.js("document.fonts.ready.then(()=>true)")
        for weight in [400, 500, 600, 700]:
            for i in range(len(STRINGS)):
                await used(value + f" glyphs {weight}/{i}", f"#glyph-{weight}-{i}")
        numeric = await b.js(
            "[...document.querySelectorAll('.numeric-probe')].map(e=>({weight:e.dataset.weight,widths:[...e.children].map(n=>n.getBoundingClientRect().width)}))"
        )
        assert all(max(r["widths"]) - min(r["widths"]) < 0.05 for r in numeric), numeric
        checks.append(
            {
                "check": value + " real tabular digit widths at 400/500/600/700",
                "status": "PASS",
                "widths": numeric,
            }
        )
        await shot(value + "-glyphs")
        await b.js(
            "document.querySelector('#font-specimen').close();document.querySelector('#font-specimen').remove()"
        )

    await b.call("Page.navigate", {"url": "http://127.0.0.1:18013/"})
    await check("Auth visible", "!!document.querySelector('.auth-card')")
    await b.js("document.fonts.ready.then(()=>true)")
    await used("auth heading", ".auth-card h1")
    await text("document.querySelector('input[name=name]')", "Тестовая тема")
    await text("document.querySelector('input[name=password]')", "Theme-password-123")
    await b.js("document.querySelector('.auth-card .primary').click()")
    await check(
        "Grid and collection visible",
        "document.querySelectorAll('.research-main .product-card').length===53 && document.querySelector('.research-selected-count')?.textContent==='3'",
    )
    for value in ["current", "pulse"]:
        await theme(value)
        await b.js("document.querySelector('.research-main').scrollTop=0")
        await shot(value + "-grid")
        await used(value + " grid heading", ".research-main h1")
        await used(value + " product name", ".research-main .product-name")
        await used(value + " price", ".research-main .product-meta strong")
        await used(value + " project title", ".research-projects .project-item strong")
        if not before:
            await b.js("document.querySelector('.research-main .product-name').click()")
            await check(
                value + " product details",
                "!!document.querySelector('.product-dialog[open] #product-title')",
            )
            await used(value + " product details heading", "#product-title")
            await key("Escape")
            for label, selector in [
                ("header", ".research-top-brand"),
                ("sidebar", ".research-projects h2"),
                ("chat", ".research-message p"),
                ("query label", "label[for=research-message]"),
                ("categories", ".query-category-caption strong"),
                ("filter", ".research-inline-filters .ui-select-trigger"),
                ("counter", ".research-selected-count"),
            ]:
                await used(value + " " + label, selector)
        geometry.append(
            await b.js(
                "(()=>{const c=document.querySelector('.project-item'),t=c.querySelector('strong').getBoundingClientRect(),s=c.querySelector('.project-settings').getBoundingClientRect();return {theme:document.documentElement.dataset.siteTheme,settings:s.width,offsetY:s.y+s.height/2-t.y-t.height/2}})()"
            )
        )
        if not before:
            assert geometry[-1]["settings"] == 32 and abs(geometry[-1]["offsetY"]) < 0.5
        await b.js("dc('Материал').click()")
        await check(value + " material popup", "!!dp()?.querySelector('input')")
        await text("dp().querySelector('input')", "длин")
        await shot(value + "-dropdown")
        await used(
            value + " dropdown", ".ui-select-popup:popover-open .ui-select-option-text"
        )
        await key("Escape")
        if not before:
            await b.js("dc('Бренд').click()")
            await check(
                value + " brand rows keep grid geometry",
                "[...dp().querySelectorAll('.ui-checkbox-option')].every(e=>getComputedStyle(e).display==='grid')",
            )
            await used(
                value + " brands",
                ".ui-select-popup:popover-open .ui-select-option-text",
            )
            await key("Escape")
        await b.js(
            "document.querySelector('.research-main .research-grid .studio-button').focus();document.activeElement.click()"
        )
        await check(
            value + " variants",
            "document.querySelectorAll('.model-variants-sheet .product-card').length===4",
        )
        await shot(value + "-variants")
        await used(value + " drawer heading", ".model-variants-sheet h2")
        geometry.append(
            await b.js(
                "(()=>{const d=document.querySelector('.model-variants-sheet'),g=d.querySelector('.research-grid');return {theme:document.documentElement.dataset.siteTheme,drawer:d.getBoundingClientRect().width,columns:getComputedStyle(g).gridTemplateColumns}})()"
            )
        )
        if not before:
            assert (
                geometry[-1]["drawer"] == 880
                and len(geometry[-1]["columns"].split()) == 2
            )
        await key("Escape")
        await b.js("document.querySelector('.research-hud-selected button').click()")
        await check(
            value + " selected",
            "document.querySelectorAll('.selected-product').length===3",
        )
        await shot(value + "-selected")
        await used(value + " selected heading", ".selected-products-sheet h2")
        if not before:
            await b.js(
                "document.querySelector('.selected-products-scroll').scrollTop=100000"
            )
            await check(
                value + " selection footer does not cover last card",
                "(()=>{const cards=document.querySelectorAll('.selected-product'),last=cards[cards.length-1].getBoundingClientRect(),f=document.querySelector('.selected-products-actions').getBoundingClientRect();return last.bottom<=f.top+.5})()",
            )
        await key("Escape")
        await b.call("Page.navigate", {"url": "about:blank"})
        await b.call(
            "Page.navigate",
            {"url": f"http://127.0.0.1:18013/#presentation={doc['id']}"},
        )
        await check(
            value + " editor preview",
            "!!document.querySelector('.presentation-page')?.complete && !!document.querySelector('.presentation-settings fieldset')",
        )
        await shot(value + "-editor")
        await used(value + " editor", ".presentation-workspace h1")
        await b.click("Подобрать сочетание")
        await check(
            value + " palettes", "document.querySelectorAll('.palette-card').length>0"
        )
        await b.js("dc('Ведущее направление').click()")
        await shot(value + "-palette")
        await used(value + " palette dialog", ".palette-dialog-body h2")
        await key("Escape")
        await b.click("Отмена", "document.querySelector('.palette-explorer')")
        await b.click("← Вернуться к подборке")
        await check(
            value + " return to grid",
            "!!document.querySelector('.research-main .product-card')",
        )
        if not before:
            await specimen(value)
    await b.call(
        "Emulation.setDeviceMetricsOverride",
        {"width": 390, "height": 844, "deviceScaleFactor": 1, "mobile": False},
    )
    if not before:
        # Reload at the narrow viewport after closing the top-layer specimen.
        # This also verifies persisted state without capturing a stale surface
        # from the preceding desktop dialog during the compositor resize.
        b.font_phase = "mobile"
        await b.call("Page.reload")
        await check(
            "Mobile reload preserves Pulse and collection",
            "document.documentElement.dataset.siteTheme==='pulse' && document.querySelector('.research-selected-count')?.textContent==='3'",
        )
    await shot("pulse-mobile")
    if not before:
        await check(
            "Mobile has no horizontal overflow",
            "document.documentElement.scrollWidth<=innerWidth",
        )
        await theme("current")
        await shot("current-mobile")
        await check(
            "Current mobile has no horizontal overflow",
            "document.documentElement.scrollWidth<=innerWidth",
        )
        await b.js(
            "document.querySelector('.research-main .research-grid .studio-button').focus();document.activeElement.click()"
        )
        await check(
            "Mobile model variants use one column",
            "!!document.querySelector('.model-variants-grid') && getComputedStyle(document.querySelector('.model-variants-grid')).gridTemplateColumns.split(' ').length===1",
        )
        await used("mobile model variants heading", ".model-variants-sheet h2")
        await shot("mobile-variants")
        await key("Escape")
        await theme("pulse")
        await b.call(
            "Emulation.setDeviceMetricsOverride",
            {"width": 1440, "height": 1000, "deviceScaleFactor": 1, "mobile": False},
        )
        # CSS viewport/DPR equivalents preserve the preceding zoom acceptance method.
        for width, height, dpr in [(1152, 800, 1.25), (960, 666, 1.5)]:
            await b.call(
                "Emulation.setDeviceMetricsOverride",
                {
                    "width": width,
                    "height": height,
                    "deviceScaleFactor": dpr,
                    "mobile": False,
                },
            )
            await b.js(
                "document.querySelector('.research-main .research-grid .studio-button').focus();document.activeElement.click()"
            )
            await check(
                f"Zoom equivalent {dpr} keeps two columns",
                "getComputedStyle(document.querySelector('.model-variants-grid')).gridTemplateColumns.split(' ').length===2 && document.querySelector('.model-variants-sheet').getBoundingClientRect().right<=innerWidth",
            )
            await shot("zoom-" + str(dpr))
            await key("Escape")
        await b.call(
            "Emulation.setDeviceMetricsOverride",
            {"width": 1440, "height": 1000, "deviceScaleFactor": 1, "mobile": False},
        )
        await text(
            "document.querySelectorAll('.research-inline-filters input[type=number]')[1]",
            "1",
        )
        await check(
            "Empty results remain readable",
            "!!document.querySelector('.research-empty')",
        )
        await used("empty state", ".research-empty h2")
        await b.js(
            "[...document.querySelectorAll('button')].find(e=>e.textContent==='Сбросить фильтры').click()"
        )
        await check(
            "Reset restores all products",
            "document.querySelectorAll('.research-main .product-card').length===53",
        )
        b.font_phase = "f5"
        await b.call("Page.reload")
        await check(
            "F5 preserves Pulse and collection",
            "document.documentElement.dataset.siteTheme==='pulse' && document.querySelector('.research-selected-count')?.textContent==='3'",
        )
        await b.js("document.fonts.ready.then(()=>true)")
        await b.call("Network.setCacheDisabled", {"cacheDisabled": True})
        b.font_phase = "slow"
        await b.call(
            "Fetch.enable",
            {"patterns": [{"urlPattern": "*.woff2*", "requestStage": "Request"}]},
        )
        await b.call("Page.reload", {"ignoreCache": True})
        await check(
            "Slow font still shows the UI",
            "document.querySelector('.research-main h1')?.getBoundingClientRect().height>0 && document.fonts.status==='loading'",
        )
        await asyncio.sleep(0.3)
        await used("slow font fallback", ".research-main h1", expected="fallback")
        # Release requests only after observing actual fallback text.
        while b.font_paused:
            await b.call("Fetch.continueRequest", {"requestId": b.font_paused.pop(0)})
        await b.call("Fetch.disable")
        await b.js("document.fonts.ready.then(()=>true)")
        await used("loaded after delay", ".research-main h1")
        b.font_phase = "blocked"
        await b.call("Network.setBlockedURLs", {"urls": ["*.woff2*"]})
        await b.call("Page.reload", {"ignoreCache": True})
        await check(
            "Blocked font still shows readable UI",
            "document.querySelector('.research-main h1')?.getBoundingClientRect().height>0",
        )
        await b.js("document.fonts.ready.then(()=>true)")
        await used("blocked font fallback", ".research-main h1", expected="fallback")
        await b.shot(output / "blocked-font-fallback.png")
        await b.call("Network.setBlockedURLs", {"urls": []})
        b.font_phase = "restored"
        await b.call("Page.reload", {"ignoreCache": True})
        await check(
            "Fonts recover after unblock",
            "!!document.querySelector('.research-main h1')",
        )
        await b.js("document.fonts.ready.then(()=>true)")
        await used("font restored", ".research-main h1")
    result = {
        "run": run,
        "status": "BASELINE" if before else "PASS",
        "checks": checks,
        "fonts": fonts,
        "geometry": geometry,
        "network": b.font_requests,
        "pdf_sha256": hashlib.sha256(pdf).hexdigest(),
        "preview_sha256": preview_hashes,
    }
    assert (await api.get(f"/api/presentations/{doc['id']}")).json() == original
    assert (
        await api.get(f"/api/projects/{project['id']}/collection")
    ).json() == collection
    assert not b.errors, b.errors
    (output / "acceptance.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), "utf-8"
    )
    print("Font UI", result["status"], len(checks), "checks", flush=True)
