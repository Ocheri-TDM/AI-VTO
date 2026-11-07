"""Additional native controls checks in the same disposable Edge/account."""

import asyncio
import json

from check_chat_workflow import save
from check_clean_start_browser import INSTRUMENT
from check_palette_browser import Browser


async def run(state, root, ledger, public):
    b = Browser()
    await b.connect(state["users"][0]["cookies"])
    checks = []

    async def check(label, condition, **detail):
        checks.append(
            {"check": label, "status": "PASS" if condition else "FAIL", **detail}
        )
        save(public / "controls.json", checks)
        print(("PASS " if condition else "FAIL ") + label, flush=True)
        assert condition, label

    async def theme(value):
        await b.js("document.querySelector('.theme-switch .ui-select-trigger').click()")
        await b.wait(
            "!!document.querySelector('.theme-switch .ui-select-popup:popover-open')"
        )
        await b.js(
            "Array.from(document.querySelectorAll('.theme-switch [role=option]')).find(e=>e.textContent.trim()==="
            + json.dumps(value)
            + ").click()"
        )
        await asyncio.sleep(0.35)

    async def style(selector):
        return await b.js(
            "(()=>{const n=document.querySelector("
            + json.dumps(selector)
            + ");return {width:getComputedStyle(n,'::-webkit-scrollbar').width,thumb:getComputedStyle(n,'::-webkit-scrollbar-thumb').backgroundColor,overflow:getComputedStyle(n).overflow,height:n.clientHeight,scroll:n.scrollHeight}})()"
        )

    async def escape():
        await b.call(
            "Input.dispatchKeyEvent",
            {"type": "keyDown", "key": "Escape", "windowsVirtualKeyCode": 27},
        )
        await b.call(
            "Input.dispatchKeyEvent",
            {"type": "keyUp", "key": "Escape", "windowsVirtualKeyCode": 27},
        )

    try:
        await b.call("Page.addScriptToEvaluateOnNewDocument", {"source": INSTRUMENT})
        await b.js(
            "(async()=>{const p=(await fetch('/api/projects').then(r=>r.json())).find(p=>p.chat_id!=="
            + json.dumps(state["first_chat"])
            + "&&p.chat_id!=="
            + json.dumps(state["second_chat"])
            + ");if(!p)throw Error('Missing owned empty chat');localStorage.setItem('forma-chat-id',p.chat_id)})()"
        )
        await b.call("Page.navigate", {"url": "http://127.0.0.1:3000/"})
        await b.wait("!!document.querySelector('#research-query')")
        await b.js(
            "(()=>{const n=document.querySelector('#research-query');Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(n,'');n.dispatchEvent(new Event('input',{bubbles:true}));})()"
        )
        await check(
            "empty composer cannot submit or open a conversation",
            await b.js(
                "document.querySelector('.research-composer button').disabled&&!document.querySelector('.research-chat')"
            ),
        )
        await b.js(
            "(()=>{const n=document.querySelector('#research-query');Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(n,'a'.repeat(2001));n.dispatchEvent(new Event('input',{bubbles:true}));})()"
        )
        await b.js("document.querySelector('.research-composer').requestSubmit()")
        await check(
            "pre-submit length validation does not open empty chat",
            await b.js(
                "!document.querySelector('.research-chat')&&!!document.querySelector('.research-error')"
            ),
        )
        await b.js(
            "localStorage.setItem('forma-chat-id',"
            + json.dumps(state["first_chat"])
            + ")"
        )
        await b.call("Page.reload", {"ignoreCache": True})
        await b.wait("document.querySelectorAll('.product-card').length===4")
        if await b.js("!!document.querySelector('.research-chat-reopen')"):
            await b.js("document.querySelector('.research-chat-reopen').click()")
        await b.wait("document.querySelectorAll('[data-message-id]').length===2")
        await asyncio.sleep(0.4)
        before = await b.js(
            "({gets:__network.filter(x=>x.method==='GET').length,posts:__network.filter(x=>x.method==='POST').length,sources:{...__sources}})"
        )
        for _ in range(3):
            await b.js(
                "document.querySelector('[aria-label=\"Свернуть чат\"]').click()"
            )
            await b.wait("!document.querySelector('.research-chat')")
            await b.js("document.querySelector('.research-chat-reopen').click()")
            await b.wait("!!document.querySelector('.research-chat textarea')")
        after = await b.js(
            "({gets:__network.filter(x=>x.method==='GET').length,posts:__network.filter(x=>x.method==='POST').length,sources:{...__sources}})"
        )
        await check(
            "panel toggles do not trigger extra GET/POST or EventSource operations",
            before == after,
            before=before,
            after=after,
        )
        await b.js(
            "(async()=>{const p=(await fetch('/api/projects').then(r=>r.json())).find(p=>p.chat_id==="
            + json.dumps(state["first_chat"])
            + ");for(const offer_id of ['vto:1823107','vto:1823108','vto:1823109','vto:1823110']){const r=await fetch('/api/projects/'+p.id+'/collection/items',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({offer_id})});if(!r.ok)throw Error(r.status)}})()"
        )
        await b.call("Page.reload", {"ignoreCache": True})
        await b.wait(
            "document.querySelector('.research-selected-count')?.textContent==='4'"
        )
        for value in ["Текущая", "Pulse"]:
            await theme(value)
            await b.js(
                "document.querySelector('.theme-switch .ui-select-trigger').click()"
            )
            await b.wait(
                "!!document.querySelector('.theme-switch .ui-select-popup:popover-open')"
            )
            dropdown = await style(".theme-switch .ui-select-options")
            await check(
                value + " native dropdown scrollbar has the shared tokens",
                dropdown["width"] == "10px",
                metrics=dropdown,
            )
            await escape()
            await check(
                value + " Escape on dropdown preserves the open conversation",
                await b.js(
                    "!document.querySelector('.ui-select-popup:popover-open')&&!!document.querySelector('.research-chat')"
                ),
            )
            await b.js(
                "document.querySelector('.research-hud-selected button').click()"
            )
            await b.wait("document.querySelectorAll('.selected-product').length===4")
            selected = await style(".selected-products-scroll")
            await check(
                value + " selected products use one real themed scroller",
                selected["width"] == "10px"
                and selected["overflow"] == "auto"
                and selected["scroll"] > selected["height"],
                metrics=selected,
            )
            await b.shot(
                public
                / (
                    "controls-"
                    + ("current" if value == "Текущая" else "pulse")
                    + "-selected.png"
                )
            )
            await escape()
            await b.wait("!document.querySelector('dialog[open]')")
            await check(
                value + " modal Escape preserves conversation and selection",
                await b.js(
                    "!!document.querySelector('.research-chat')&&document.querySelector('.research-selected-count').textContent==='4'"
                ),
            )
            await b.js(
                "document.querySelector('.research-view-setting .ui-select-trigger').click()"
            )
            await b.wait(
                "!!document.querySelector('.research-view-setting .ui-select-popup:popover-open')"
            )
            await b.js(
                "Array.from(document.querySelectorAll('.research-view-setting [role=option]')).find(e=>e.textContent.trim()==='По моделям').click()"
            )
            await b.wait("document.querySelectorAll('.product-card').length===1")
            await b.js(
                "Array.from(document.querySelectorAll('.research-grid button')).find(e=>e.textContent.startsWith('Варианты модели')).click()"
            )
            await b.wait(
                "document.querySelectorAll('.model-variants-sheet .product-card').length===4"
            )
            variants = await style(".model-variants-sheet>.sheet-body")
            await check(
                value + " model variants use their native themed scroller",
                variants["width"] == "10px" and variants["overflow"] == "auto",
                metrics=variants,
            )
            await b.shot(
                public
                / (
                    "controls-"
                    + ("current" if value == "Текущая" else "pulse")
                    + "-variants.png"
                )
            )
            await escape()
            await b.wait("!document.querySelector('dialog[open]')")
            await b.js(
                "document.querySelector('.research-view-setting .ui-select-trigger').click()"
            )
            await b.wait(
                "!!document.querySelector('.research-view-setting .ui-select-popup:popover-open')"
            )
            await b.js(
                "Array.from(document.querySelectorAll('.research-view-setting [role=option]')).find(e=>e.textContent.trim()==='Все варианты').click()"
            )
            await b.wait("document.querySelectorAll('.product-card').length===4")
    finally:
        await b.ws.close()
