"""Native installed Edge DOM acceptance; task-owned profile and listener only."""

import json
from pathlib import Path

from check_palette_browser import Browser


async def run(state):
    user = state["live_users"][0]
    project = user["project"]
    b = Browser()
    await b.connect(user["cookies"])
    checks = []

    async def check(label, expression):
        await b.wait(expression)
        checks.append({"check": label, "pass": True})

    async def settled():
        await b.wait(
            "!document.querySelector('.research-hud-status [role=status]') && !document.querySelector('[role=alert]')"
        )

    try:
        await b.call(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": f"localStorage.setItem('forma-chat-id',{json.dumps(project['chat_id'])})"},
        )
        await b.call("Page.navigate", {"url": "http://127.0.0.1:3000/"})
        await check(
            "Authenticated persisted workspace",
            "document.querySelectorAll('.query-category-chip').length===3 && document.querySelector('.research-selected-count')?.textContent==='3'",
        )
        await b.js(
            "document.querySelectorAll('.query-category-chip')[0].querySelectorAll('button')[0].click()"
        )
        await check(
            "Hide retains category",
            "document.querySelectorAll('.query-category-chip').length===3 && document.querySelectorAll('.query-category-chip')[0].querySelector('button').getAttribute('aria-pressed')==='true'",
        )
        await settled()
        await b.js(
            "document.querySelectorAll('.query-category-chip')[0].querySelectorAll('button')[0].click()"
        )
        await settled()
        await check(
            "Show retains project selection",
            "document.querySelector('.research-selected-count').textContent==='3'",
        )
        await b.js(
            "document.querySelectorAll('.query-category-chip')[2].querySelectorAll('button')[1].click()"
        )
        await check(
            "Remove changes source workspace",
            "document.querySelectorAll('.query-category-chip').length===2 && !!document.querySelector('.query-category-notice button')",
        )
        await settled()
        await b.js("document.querySelector('.query-category-notice button').click()")
        await check("Context restore", "document.querySelectorAll('.query-category-chip').length===3")
        await settled()
        await b.call("Page.reload", {"ignoreCache": True})
        await check(
            "F5 restores categories and collection",
            "document.querySelectorAll('.query-category-chip').length===3 && document.querySelector('.research-selected-count')?.textContent==='3'",
        )
        await check("No desktop horizontal overflow", "document.documentElement.scrollWidth<=innerWidth")
        folder = Path(state["directory"])
        await b.shot(folder / "desktop.png")
        await b.call(
            "Emulation.setDeviceMetricsOverride",
            {"width": 390, "height": 844, "deviceScaleFactor": 1, "mobile": True},
        )
        await check("No mobile horizontal overflow", "document.documentElement.scrollWidth<=innerWidth")
        await check(
            "Mobile collection count retained",
            "document.querySelector('.research-selected-count').textContent==='3'",
        )
        await b.shot(folder / "mobile.png")
        # An obsolete SSE callback must not recreate an older query. The real
        # completed SSE stream is exercised by the HTTP fault harness separately.
        old = await b.js("fetch('/api/researches/chat/" + project["chat_id"] + "').then(r=>r.json())")
        assert len(old["query_categories"]) == 3
        return {
            "status": "PASS",
            "browser": "installed Microsoft Edge",
            "desktop": "1440x1000",
            "mobile": "390x844",
            "checks": checks,
            "screenshots": ["desktop.png", "mobile.png"],
            "main_api_health": await b.js("fetch('/api/health').then(r=>r.status)"),
        }
    finally:
        await b.ws.close()
