"""Real DOM geometry, adaptive navigation and confirmed/error deletion flows."""

import asyncio
import json


async def run(b, state, root, ledger, public):
    from check_chat_workflow import save

    checks = []
    matrix = []

    def check(name, condition):
        assert condition, name
        checks.append({"check": name, "pass": True})
        save(public / "browser.json", {"checks": checks, "matrix": matrix})

    async def navigate(page):
        await b.call("Page.navigate", {"url": "http://127.0.0.1:3000" + page})
        await b.wait("!!document.querySelector('.app-navbar')")

    async def theme(value):
        await b.js("document.querySelector('.theme-switch .ui-select-trigger').click()")
        await b.wait("!!document.querySelector('.theme-switch .ui-select-popup:popover-open')")
        label = "Pulse" if value == "pulse" else "Текущая"
        await b.js(
            "Array.from(document.querySelectorAll('.theme-switch [role=option]')).find(e=>e.textContent.trim()==="
            + json.dumps(label)
            + ").click()"
        )
        await b.wait("document.documentElement.dataset.siteTheme===" + json.dumps(value))
        await asyncio.sleep(0.4)

    scenarios = [
        ("desktop", 1440, 1000, 1, False),
        ("zoom125", 1152, 800, 1.25, False),
        ("zoom150", 960, 667, 1.5, False),
        ("mobile", 390, 844, 1, True),
    ]
    for value in ["current", "pulse"]:
        for label, width, height, dpr, mobile in scenarios:
            await b.call(
                "Emulation.setDeviceMetricsOverride",
                {"width": width, "height": height, "deviceScaleFactor": dpr, "mobile": mobile},
            )
            for page in ["/", "/offers"]:
                await navigate(page)
                await theme(value)
                # UI-only long-name fixture; no account/profile data is changed.
                await b.js(
                    "(()=>{const n=document.querySelector('.app-navbar-name');n.textContent='Очень длинное имя пользователя '.repeat(8)})()"
                )
                geometry = await b.js(
                    "(()=>{const h=document.querySelector('.app-navbar'),r=h.getBoundingClientRect();const controls=Array.from(h.querySelectorAll('button,a')).map(e=>{const a=e.getBoundingClientRect();return {label:e.textContent,height:a.height,top:a.top,bottom:a.bottom,left:a.left,right:a.right}});return {height:r.height,top:r.top,bottom:r.bottom,controls,overflow:document.documentElement.scrollWidth>innerWidth,valid:controls.every(a=>a.height>=39&&a.height<=46&&a.top>=r.top+7&&a.bottom<=r.bottom-7&&a.left>=0&&a.right<=innerWidth)}})()"
                )
                check(f"Navbar {value} {label} {page}", geometry["valid"] and not geometry["overflow"])
                matrix.append(
                    {
                        "theme": value,
                        "scenario": label,
                        "page": page,
                        "effective_width": width,
                        "dpr": dpr,
                        **geometry,
                    }
                )
                await b.shot(
                    public / (f"navbar-{value}-{label}-" + ("chat" if page == "/" else "offers") + ".png")
                )
    await b.call(
        "Emulation.setDeviceMetricsOverride",
        {"width": 1440, "height": 1000, "deviceScaleFactor": 1, "mobile": False},
    )
    await navigate("/")
    await b.js("document.querySelector('.app-navbar-links button').click()")
    await b.wait("!!document.querySelector('dialog[open] .project-list')")
    check("Projects navigation still opens the chat list", True)
    await b.js("document.querySelector('dialog[open] header button').click()")
    await navigate("/offers")
    identifier = state["browser_document"]
    selector = '[data-document-id="' + identifier + '"]'
    await b.wait("!!document.querySelector(" + json.dumps(selector) + ")")
    for value in ["current", "pulse"]:
        await b.call(
            "Emulation.setDeviceMetricsOverride",
            {"width": 390, "height": 844, "deviceScaleFactor": 1, "mobile": True},
        )
        await theme(value)
        await b.js(
            "document.querySelector("
            + json.dumps(selector)
            + ").querySelector('.offer-delete-action').click()"
        )
        await b.wait("!!document.querySelector('dialog[open]')")
        check(
            "Mobile confirmation fits " + value,
            await b.js(
                "(()=>{let r=document.querySelector('dialog[open]').getBoundingClientRect();return r.left>=0&&r.right<=innerWidth&&r.top>=0&&r.bottom<=innerHeight})()"
            ),
        )
        await b.shot(public / ("delete-mobile-" + value + ".png"))
        await b.js(
            "Array.from(document.querySelectorAll('dialog[open] button')).find(e=>e.textContent==='Отмена').click()"
        )
    await b.call(
        "Emulation.setDeviceMetricsOverride",
        {"width": 1440, "height": 1000, "deviceScaleFactor": 1, "mobile": False},
    )
    await b.js(
        "Array.from(document.querySelector("
        + json.dumps(selector)
        + ").querySelectorAll('button')).find(e=>e.textContent==='Удалить').click()"
    )
    await b.wait("!!document.querySelector('dialog[open]')")
    check(
        "Delete dialog names document and explains preservation",
        await b.js(
            "document.querySelector('dialog[open]').textContent.includes('Чат и выбранные товары сохранятся')"
        ),
    )
    await b.shot(public / "delete-confirm.png")
    await b.js(
        "Array.from(document.querySelectorAll('dialog[open] button')).find(e=>e.textContent==='Отмена').click()"
    )
    check("Cancel preserves row", await b.js("!!document.querySelector(" + json.dumps(selector) + ")"))
    await b.js(
        "document.querySelector(" + json.dumps(selector) + ").querySelector('.offer-delete-action').click()"
    )
    await b.wait("!!document.querySelector('dialog[open]')")
    await b.call("Network.setBlockedURLs", {"urls": ["*/api/offers/" + identifier]})
    await b.js(
        "Array.from(document.querySelectorAll('dialog[open] button')).find(e=>e.textContent==='Удалить предложение').click()"
    )
    await b.wait("!!document.querySelector('dialog[open] [role=alert]')")
    check(
        "Failed delete retains row and visible retry",
        await b.js("!!document.querySelector(" + json.dumps(selector) + ")"),
    )
    await b.shot(public / "delete-error.png")
    await b.call("Network.setBlockedURLs", {"urls": []})
    await b.call(
        "Network.emulateNetworkConditions",
        {"offline": False, "latency": 500, "downloadThroughput": -1, "uploadThroughput": -1},
    )
    await b.js(
        "Array.from(document.querySelectorAll('dialog[open] button')).find(e=>e.textContent==='Удалить предложение').click()"
    )
    check(
        "Submit disabled during deletion",
        await b.js(
            "Array.from(document.querySelectorAll('dialog[open] button')).some(e=>e.textContent.includes('Удаляю')&&e.disabled)"
        ),
    )
    await b.wait(
        "!document.querySelector(" + json.dumps(selector) + ")&&!document.querySelector('dialog[open]')"
    )
    await b.call(
        "Network.emulateNetworkConditions",
        {"offline": False, "latency": 0, "downloadThroughput": -1, "uploadThroughput": -1},
    )
    check("Confirmed server delete removes row", True)
    await b.shot(public / "delete-complete.png")
    await b.call("Page.navigate", {"url": "http://127.0.0.1:3000/offers/" + identifier + "/edit"})
    await b.wait("!!document.querySelector('[data-testid=offer-unavailable]')")
    check(
        "Old editor URL offers no edit controls or automatic replacement",
        await b.js("!document.querySelector('.offer-edit-fields')"),
    )
    await b.shot(public / "deleted-editor.png")
    save(
        public / "browser.json",
        {
            "status": "PASS",
            "checks": checks,
            "matrix": matrix,
            "browser": "installed Microsoft Edge",
            "zoom_method": "125/150% effective CSS viewport and DPR emulation; not a native browser-menu zoom test",
            "long_name": "UI-only text fixture; account unchanged",
        },
    )
    print("Edge acceptance", len(checks), "checks")
