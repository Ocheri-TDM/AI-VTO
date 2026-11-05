"""Native installed Edge CDP acceptance; no project browser runtime or cookies in output.

Start a task-owned Edge profile on :9226, finish PDF visual approval first, then run.
"""

import asyncio
import base64
import hashlib
import json
import time
from pathlib import Path

import httpx
import websockets

ROOT = Path(__file__).resolve().parents[1]


class Browser:
    async def connect(self, cookies):
        async with httpx.AsyncClient() as c:
            tabs = (await c.get("http://127.0.0.1:9226/json/list")).json()
        self.ws = await websockets.connect(
            next(t["webSocketDebuggerUrl"] for t in tabs if t["type"] == "page" and
                 (t['url'].startswith('http://127.0.0.1:3000/') or t['url']=='about:blank')),
            max_size=30_000_000,
        )
        self.number = 0
        await self.call("Page.enable")
        await self.call("Network.enable")
        await self.call(
            "Network.setCookies",
            {
                "cookies": [
                    {
                        "name": k,
                        "value": v,
                        "url": "http://127.0.0.1:3000",
                        "httpOnly": True,
                        "sameSite": "Lax",
                    }
                    for k, v in cookies.items()
                ]
            },
        )
        await self.call(
            "Emulation.setDeviceMetricsOverride",
            {"width": 1440, "height": 1000, "deviceScaleFactor": 1, "mobile": False},
        )

    async def call(self, method, params=None):
        self.number += 1
        number = self.number
        await self.ws.send(
            json.dumps({"id": number, "method": method, "params": params or {}})
        )
        while True:
            result = json.loads(await asyncio.wait_for(self.ws.recv(), 40))
            if result.get("id") == number:
                if result.get("error"):
                    raise RuntimeError(result["error"])
                return result.get("result", {})

    async def js(self, expression):
        r = await self.call(
            "Runtime.evaluate",
            {"expression": expression, "awaitPromise": True, "returnByValue": True},
        )
        if r.get("exceptionDetails"):
            raise RuntimeError(str(r["exceptionDetails"])[:500])
        return r["result"].get("value")

    async def wait(self, expression, timeout=40):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                if await self.js('(async()=>Boolean(await ('+expression+')))()'):
                    return
            except RuntimeError:
                pass
            await asyncio.sleep(0.2)
        raise TimeoutError(expression[:300])

    async def click(self, label, scope="document"):
        await self.js(
            f"Array.from({scope}.querySelectorAll('button')).find(b=>b.textContent.trim()==={json.dumps(label)}).click()"
        )

    async def shot(self, path):
        r = await self.call(
            "Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False}
        )
        path.write_bytes(base64.b64decode(r["data"]))

    async def select_revision(self, document_id, revision_id):
        """Use the editor's common select rather than a removed native field."""
        await self.js("document.querySelector('.presentation-preview-heading .ui-select-trigger').click()")
        await self.wait("!!document.querySelector('.presentation-preview-heading .ui-select-popup:popover-open')")
        await self.js(
            "(async()=>{const d=await fetch('/api/presentations/'+" + json.dumps(document_id)
            + ").then(r=>r.json());const i=d.revisions.findIndex(r=>r.id==="
            + json.dumps(revision_id)
            + ");if(i<0)throw Error('Missing fixture revision');document.querySelectorAll('.presentation-preview-heading [role=option]')[i].click()})()"
        )


async def main():
    state = json.loads((ROOT / ".local/palette-acceptance.json").read_text("utf-8"))
    output = ROOT / ".local/palette-acceptance" / state["run"]
    docid = state["primary"]

    async def doc():
        return await b.js(f"fetch('/api/presentations/{docid}').then(r=>r.json())")

    b = Browser()
    await b.connect(state["users"][0]["cookies"])
    try:
        await b.call("Page.navigate", {"url": "about:blank"})
        await b.wait("location.href==='about:blank'")
        await b.call(
            "Page.navigate", {"url": "http://127.0.0.1:3000/#presentation=" + docid}
        )
        await b.wait("!!document.querySelector('.presentation-settings fieldset')")
        before = await doc()
        opening = time.perf_counter()
        await b.click("Подобрать сочетание")
        await b.wait("document.querySelector('dialog[open] .palette-card')")
        await b.wait("document.querySelectorAll('.palette-card').length===24")
        open_seconds = round(time.perf_counter() - opening, 3)
        await b.js("document.querySelector('.palette-card').click()")
        chosen = await b.js(
            "document.querySelector('.palette-card.is-selected').textContent"
        )
        more_started = time.perf_counter()
        await b.click("Ещё сочетания")
        await b.wait("document.querySelectorAll('.palette-card').length>24")
        more_seconds = round(time.perf_counter() - more_started, 3)
        assert (
            await b.js(
                "document.querySelector('.palette-card.is-selected').textContent"
            )
            == chosen
        )
        assert (await doc())["version"] == before["version"]
        assert len((await doc())["revisions"]) == len(before["revisions"])
        await b.shot(output / "explorer-desktop.png")
        await b.click("Отмена", "document.querySelector('dialog')")
        assert (await doc())["draft"]["settings"] == before["draft"]["settings"]
        await b.click("Подобрать сочетание")
        await b.wait("!!document.querySelector('dialog[open]')")
        await b.js(
            "document.querySelector('.palette-explorer-filters [role=combobox]').click()"
        )
        await b.wait("!!document.querySelector('[role=combobox][aria-expanded=true]')")
        await b.call('Input.dispatchKeyEvent', {'type':'keyDown','key':'End','windowsVirtualKeyCode':35})
        await b.call('Input.dispatchKeyEvent', {'type':'keyDown','key':'Enter','windowsVirtualKeyCode':13})
        await b.wait("!!document.querySelector('input[aria-label=\"Основной HEX\"]')")
        await b.js(
            "(()=>{const i=document.querySelector('input[aria-label=\"Основной HEX\"]');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(i,'#DB2344');i.dispatchEvent(new Event('input',{bubbles:true}));})()"
        )
        await b.wait(
            "document.querySelector('.palette-card')?.textContent.includes('#DB2344')"
        )
        await b.js("document.querySelector('.palette-card').click()")
        await b.click("Применить палитру", "document.querySelector('dialog')")
        await b.wait(
            f"fetch('/api/presentations/{docid}').then(r=>r.json()).then(d=>d.draft.settings.palette_plan?.primary==='#DB2344')"
        )
        await b.wait(
            "document.querySelector('.presentation-header').textContent.includes('Черновик сохранён')"
        )
        saved = await doc()
        assert saved["draft"]["settings"]["palette_plan"]["locked"]
        assert len(saved["revisions"]) == len(before["revisions"])
        await b.call("Page.reload", {"ignoreCache": True})
        await b.wait("!!document.querySelector('.presentation-settings fieldset')")
        assert (await doc())["draft"]["settings"]["palette_plan"][
            "primary"
        ] == "#DB2344"
        # Toggle decoration using its actual controls; no render until requested.
        await b.js(
            "(()=>{const f=Array.from(document.querySelectorAll('.presentation-settings fieldset')).find(f=>f.querySelector('legend')?.textContent==='Автооформление');f.querySelector('input[type=checkbox]').click();})()"
        )
        await b.wait(
            f"fetch('/api/presentations/{docid}').then(r=>r.json()).then(d=>d.draft.settings.auto_decoration?.enabled===false)"
        )
        await b.wait(
            "document.querySelector('.presentation-header').textContent.includes('Черновик сохранён')"
        )
        await b.js(
            "(()=>{const f=Array.from(document.querySelectorAll('.presentation-settings fieldset')).find(f=>f.querySelector('legend')?.textContent==='Автооформление');f.querySelector('input[type=checkbox]').click();})()"
        )
        await b.wait(
            "document.querySelector('.presentation-header').textContent.includes('Черновик сохранён')"
        )
        await b.wait(
            f"fetch('/api/presentations/{docid}').then(r=>r.json()).then(d=>d.draft.settings.auto_decoration?.enabled===true)"
        )
        await b.call("Page.reload", {"ignoreCache": True})
        await b.wait("!!document.querySelector('.presentation-settings fieldset')")
        assert (await doc())["draft"]["settings"]["auto_decoration"]["enabled"]
        oldsrc = await b.js(
            "document.querySelector('.presentation-page').getAttribute('src')"
        )
        oldcount = len((await doc())["revisions"])
        await b.click("Сгенерировать PDF")
        assert await b.js("!!document.querySelector('.presentation-page')")
        await b.wait(
            f"fetch('/api/presentations/{docid}').then(r=>r.json()).then(d=>d.revisions.length>{oldcount}&&d.revisions[0].status==='READY')",
            90,
        )
        current = await doc()
        rev = current["revisions"][0]
        await b.wait(
            f"document.querySelector('.presentation-page').getAttribute('src').includes('{rev['id']}')"
        )
        assert oldsrc != await b.js(
            "document.querySelector('.presentation-page').getAttribute('src')"
        )
        folder = output / "browser"
        folder.mkdir(exist_ok=True)
        await b.call(
            "Browser.setDownloadBehavior",
            {"behavior": "allow", "downloadPath": str(folder), "eventsEnabled": True},
        )
        await b.js(
            "document.querySelector('.presentation-preview a[download]').click()"
        )
        end = time.monotonic() + 15
        while not list(folder.glob("*.pdf")) and time.monotonic() < end:
            await asyncio.sleep(0.2)
        downloaded = next(folder.glob("*.pdf"))
        assert (
            hashlib.sha256(downloaded.read_bytes()).hexdigest()
            == rev["result"]["sha256"]
        )
        async with httpx.AsyncClient(
            base_url="http://127.0.0.1:8000", cookies=state["users"][0]["cookies"]
        ) as c:
            for page in rev["result"]["pages"]:
                r = await c.get(page["preview"])
                r.raise_for_status()
                (folder / f"page-{page['page']}.png").write_bytes(r.content)
            selection = (
                await c.get(f"/api/projects/{current['project_id']}/collection")
            ).json()["selected_ids"]
        assert selection == ["vto:1823109", "vto:1823108", "vto:1823107"]
        # Manual edit marks origin; it must not silently retain a preset label.
        await b.js(
            "(()=>{const i=document.querySelector('.presentation-palette input[type=color]');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(i,'#BC304A');i.dispatchEvent(new Event('input',{bubbles:true}));})()"
        )
        await b.wait(
            f"fetch('/api/presentations/{docid}').then(r=>r.json()).then(d=>d.draft.settings.palette_plan?.manual===true)"
        )
        await b.wait(
            "document.querySelector('.presentation-header').textContent.includes('Черновик сохранён')"
        )
        assert (await doc())["draft"]["settings"]["palette_plan"]["origin_preset_id"]
        legacy = state["renders"]["legacy-v3"]["revision"]
        await b.select_revision(docid, legacy)
        await b.call("Page.reload", {"ignoreCache": True})
        await b.wait(
            f"document.querySelector('.presentation-page')?.getAttribute('src').includes('{legacy}')"
        )
        assert await b.js("document.body.innerText.includes('Своя палитра')")
        await b.click("Подобрать сочетание")
        await b.wait("!!document.querySelector('dialog[open] .palette-card')")
        await b.call(
            "Emulation.setDeviceMetricsOverride",
            {"width": 390, "height": 844, "deviceScaleFactor": 1, "mobile": True},
        )
        await asyncio.sleep(0.3)
        assert await b.js(
            "document.querySelector('dialog').getBoundingClientRect().width<=innerWidth+1&&document.documentElement.scrollWidth<=innerWidth"
        )
        await b.shot(output / "explorer-mobile.png")
        await b.call(
            "Input.dispatchKeyEvent",
            {
                "type": "keyDown",
                "key": "Tab",
                "code": "Tab",
                "windowsVirtualKeyCode": 9,
            },
        )
        assert await b.js(
            "document.querySelector('dialog').contains(document.activeElement)"
        )
        await b.call(
            "Input.dispatchKeyEvent",
            {
                "type": "keyDown",
                "key": "Escape",
                "code": "Escape",
                "windowsVirtualKeyCode": 27,
            },
        )
        await b.wait("!document.querySelector('dialog[open]')")
        evidence = {
            "open_seconds": open_seconds,
            "more_seconds": more_seconds,
            "desktop": [1440, 1000],
            "mobile": [390, 844],
            "cancel_preserves_draft": True,
            "more_preserves_selection": True,
            "library_no_jobs": True,
            "fixed_hex": "#DB2344",
            "palette_and_decoration_f5": True,
            "manual_provenance": True,
            "revision_f5": True,
            "old_preview_preserved": True,
            "native_download_sha256": rev["result"]["sha256"],
            "page_count": rev["result"]["page_count"],
            "revision": rev["id"],
            "mobile_overflow": False,
            "escape_and_focus": True,
            "collection_preserved": True,
        }
        (output / "browser-evidence.json").write_text(
            json.dumps(evidence, indent=2), "utf-8"
        )
        print(
            "Native Edge library/cancel/more/custom HEX/manual/F5/decor/generate/download/history/mobile: PASS"
        )
    finally:
        await b.ws.close()


if __name__ == "__main__":
    asyncio.run(main())
