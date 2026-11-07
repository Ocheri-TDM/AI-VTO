"""First submit before any project exists; late creation must retain UI choice."""

import asyncio
import json
import secrets

import httpx
from check_chat_workflow import call, save
from check_clean_start_browser import INSTRUMENT
from check_palette_browser import Browser


async def run(state, root, ledger, public):
    evidence = []

    async def check(label, condition):
        evidence.append({"check": label, "status": "PASS" if condition else "FAIL"})
        save(public / "fresh.json", evidence)
        print(("PASS " if condition else "FAIL ") + label, flush=True)
        assert condition, label

    for theme in ["current", "pulse"]:
        async with httpx.AsyncClient(
            base_url="http://127.0.0.1:8000", timeout=60
        ) as client:
            name = "clean-start-" + state["run"] + "-" + secrets.token_hex(3)
            user = await call(
                client,
                "POST",
                "/api/auth/register",
                json={"name": name, "password": secrets.token_urlsafe(24)},
            )
            state["users"].append(
                {"id": user["id"], "name": name, "cookies": dict(client.cookies)}
            )
            save(ledger, state)
        b = Browser()
        await b.connect(state["users"][-1]["cookies"])
        try:
            await b.call(
                "Page.addScriptToEvaluateOnNewDocument", {"source": INSTRUMENT}
            )
            await b.js(
                "localStorage.setItem('souvenir:site-theme'," + json.dumps(theme) + ")"
            )
            await b.call("Page.navigate", {"url": "http://127.0.0.1:3000/"})
            await b.wait("!!document.querySelector('#research-query')")
            await b.js("window.__holdCreation=true;window.__holdResponses=true")
            await b.js(
                "(()=>{const n=document.querySelector('#research-query');Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(n,'Рюкзак Melango');n.dispatchEvent(new Event('input',{bubbles:true}));})()"
            )
            await b.js("document.querySelector('.research-composer').requestSubmit()")
            await b.wait(
                "!!document.querySelector('.research-chat [data-message-id^=\"local:\"]')"
            )
            await check(
                theme + " first submit opens before project creation returns",
                await b.js(
                    "document.querySelectorAll('.research-composer').length===1&&document.querySelector('.research-chat').textContent.includes('Рюкзак Melango')"
                ),
            )
            await b.js(
                "document.querySelector('[aria-label=\"Свернуть чат\"]').click()"
            )
            await b.wait("!document.querySelector('.research-chat')")
            await b.js("window.__holdCreation=false")
            await b.wait(
                "localStorage.getItem('forma-chat-id')&&__network.some(n=>n.url.endsWith('/requests'))"
            )
            await asyncio.sleep(0.4)
            await check(
                theme + " manual collapse survives automatic project creation",
                await b.js(
                    "!document.querySelector('.research-chat')&&!!document.querySelector('.research-chat-reopen')"
                ),
            )
            await b.js("window.__holdResponses=false")
            await b.wait(
                "document.querySelectorAll('.product-card').length===4", timeout=140
            )
            await check(
                theme + " final search response keeps the panel collapsed",
                await b.js("!document.querySelector('.research-chat')"),
            )
            await b.js("document.querySelector('.research-chat-reopen').click()")
            await b.wait("document.querySelectorAll('[data-message-id]').length===2")
            await check(
                theme + " reopening restores confirmed history",
                await b.js(
                    "document.querySelectorAll('.research-composer').length===1"
                ),
            )
        finally:
            values = await b.js("fetch('/api/projects').then(r=>r.json())")
            state["projects"] = list(
                dict.fromkeys(state["projects"] + [p["id"] for p in values])
            )
            save(ledger, state)
            await b.ws.close()
