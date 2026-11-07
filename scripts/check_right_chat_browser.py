"""Real workspace placement, scrolling, focus and network checks in installed Edge."""

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
        save(public / "placement.json", checks)
        print(("PASS " if condition else "FAIL ") + label, flush=True)
        assert condition, label

    async def wait_panel(opened):
        await b.wait(
            "!!document.querySelector('.research-chat')"
            if opened
            else "!document.querySelector('#workspace-chat')"
        )
        await asyncio.sleep(0.25)

    async def close():
        await b.js("document.querySelector('[aria-label=\"Свернуть чат\"]').click()")
        await wait_panel(False)

    async def open_panel():
        await b.js("document.querySelector('.research-chat-reopen').click()")
        await b.wait("!!document.querySelector('#workspace-chat')")
        await asyncio.sleep(0.25)

    async def geometry(selector):
        return await b.js(
            "(()=>{const n=document.querySelector("
            + json.dumps(selector)
            + "),h=document.querySelector('.app-navbar').getBoundingClientRect(),r=n.getBoundingClientRect(),main=document.querySelector('.research-main');return {x:r.x,y:r.y,right:r.right,width:r.width,height:r.height,headerBottom:h.bottom,viewport:innerWidth,historyY:document.querySelector('.workspace-query-actions')?.getBoundingClientRect().y,mainRight:main.getBoundingClientRect().right,scroll:main.scrollTop,overflow:document.documentElement.scrollWidth>innerWidth}})()"
        )

    async def draft(text):
        await b.js(
            "(()=>{const n=document.querySelector('.research-composer textarea');Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(n,"
            + json.dumps(text)
            + ");n.dispatchEvent(new Event('input',{bubbles:true}));})()"
        )

    async def theme(value):
        await b.js("document.querySelector('.theme-switch .ui-select-trigger').click()")
        await b.wait(
            "!!document.querySelector('.theme-switch .ui-select-popup:popover-open')"
        )
        await b.js(
            "Array.from(document.querySelectorAll('.theme-switch [role=option]')).find(n=>n.textContent.trim()==="
            + json.dumps(value)
            + ").click()"
        )
        await asyncio.sleep(0.35)

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
            "localStorage.setItem('forma-chat-id',"
            + json.dumps(state["first_chat"])
            + ")"
        )
        await b.call("Page.navigate", {"url": "http://127.0.0.1:3000/"})
        await b.wait("document.querySelectorAll('.product-card').length===4")
        if await b.js("!!document.querySelector('.research-chat-reopen')"):
            await open_panel()
        await draft("ручки")
        await b.js("window.__holdResponses=true")
        await b.js("document.querySelector('.research-composer').requestSubmit()")
        await b.wait(
            "document.querySelector('.research-chat').textContent.includes('сохранение ещё не подтверждено')"
        )
        await close()
        await b.js("window.__holdResponses=false")
        await b.wait(
            "document.querySelectorAll('.product-card').length>=12&&document.querySelector('.research-main').scrollHeight>3*document.querySelector('.research-main').clientHeight",
            timeout=140,
        )
        await check(
            "late response stays collapsed with a real multi-screen result grid",
            await b.js("!document.querySelector('#workspace-chat')"),
            products=await b.js("document.querySelectorAll('.product-card').length"),
        )
        await open_panel()
        await draft("Черновик справа")
        selection = await b.js(
            "document.querySelector('.research-selected-count').textContent"
        )
        for value in ["Текущая", "Pulse"]:
            await theme(value)
            opened = await geometry('[aria-label="Свернуть чат"]')
            await close()
            closed = await geometry(".research-chat-reopen")
            await check(
                value
                + " opener is at the right edge, 12px below navbar, with 44px target",
                16 <= closed["viewport"] - closed["right"] <= 21
                and abs(closed["y"] - closed["headerBottom"] - 12) < 1
                and closed["height"] == 44
                and not closed["overflow"],
                geometry=closed,
            )
            await check(
                value
                + " opening and collapse share the same right-side height; History does not shift",
                abs(opened["y"] - closed["y"]) < 1
                and abs(opened["historyY"] - closed["historyY"]) < 1
                and abs(closed["mainRight"] - closed["viewport"]) < 1,
                opened=opened,
                closed=closed,
            )
            await check(
                value + " one labelled opener exists outside the scrolling main",
                await b.js(
                    "document.querySelectorAll('.research-chat-reopen').length===1&&!document.querySelector('.research-main .research-chat-reopen')&&document.querySelector('.research-chat-reopen').textContent.trim()==='Чат'&&!!document.querySelector('.research-chat-reopen svg')"
                ),
            )
            await b.js("document.querySelector('.research-main').scrollTop=1400")
            await asyncio.sleep(0.2)
            scrolled = await geometry(".research-chat-reopen")
            await check(
                value
                + " opener stays visible after several screens of native scrolling",
                scrolled["scroll"] > 1000 and abs(scrolled["y"] - closed["y"]) < 1,
                geometry=scrolled,
            )
            await b.shot(
                public
                / (
                    "right-"
                    + ("current" if value == "Текущая" else "pulse")
                    + "-collapsed.png"
                )
            )
            await open_panel()
            await b.js("document.querySelector('.research-main').scrollTop=1400")
            await asyncio.sleep(0.2)
            await b.shot(
                public
                / (
                    "right-"
                    + ("current" if value == "Текущая" else "pulse")
                    + "-open.png"
                )
            )
            await check(
                value + " history, draft and project collection survive opening",
                await b.js(
                    "document.querySelector('#research-message').value==='Черновик справа'&&document.querySelectorAll('[data-message-id]').length===4&&document.querySelector('.research-selected-count').textContent==="
                    + json.dumps(selection)
                ),
            )
        before = await b.js(
            "({gets:__network.filter(n=>n.method==='GET').length,posts:__network.filter(n=>n.method==='POST').length,sources:{...__sources}})"
        )
        for _ in range(3):
            await close()
            await open_panel()
        after = await b.js(
            "({gets:__network.filter(n=>n.method==='GET').length,posts:__network.filter(n=>n.method==='POST').length,sources:{...__sources}})"
        )
        await check(
            "three toggles issue no POST or new subscriptions and no repeated reads",
            before["posts"] == after["posts"]
            and before["sources"] == after["sources"]
            and after["gets"] - before["gets"] <= 1,
            before=before,
            after=after,
        )
        await close()
        await check(
            "closed panel leaves Tab order; focus returns to the right opener",
            await b.js(
                "!document.querySelector('#workspace-chat')&&document.activeElement===document.querySelector('.research-chat-reopen')&&document.activeElement.getAttribute('aria-expanded')==='false'"
            ),
        )
        # The collection modal uses the native top layer, above the actions row.
        await b.js("document.querySelector('.research-hud-selected button').click()")
        await b.wait("!!document.querySelector('.selected-products-sheet[open]')")
        await check(
            "selected-products modal covers and disables the underlying chat control",
            await b.js(
                "(()=>{const b=document.querySelector('.research-chat-reopen'),r=b.getBoundingClientRect();return !!document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)?.closest('dialog[open]')&&document.activeElement!==b})()"
            ),
        )
        await b.shot(public / "right-modal.png")
        await escape()
        await b.wait("!document.querySelector('dialog[open]')")
        await b.call("Page.reload", {"ignoreCache": True})
        await b.wait(
            "document.querySelectorAll('.product-card').length>=12&&!!document.querySelector('.research-chat-reopen')"
        )
        await open_panel()
        await check(
            "F5 restores collapsed preference, confirmed history, search, selection and draft",
            await b.js(
                "document.querySelector('#research-message').value==='Черновик справа'&&document.querySelectorAll('[data-message-id]').length===4&&document.querySelector('.research-selected-count').textContent==="
                + json.dumps(selection)
            ),
        )
        await close()
        for value in ["Текущая", "Pulse"]:
            await theme(value)
            for width, height, scale in [
                (1152, 800, 1.25),
                (960, 667, 1.5),
                (390, 844, 1),
                (320, 640, 1),
            ]:
                await b.call(
                    "Emulation.setDeviceMetricsOverride",
                    {
                        "width": width,
                        "height": height,
                        "deviceScaleFactor": scale,
                        "mobile": width <= 390,
                    },
                )
                await asyncio.sleep(0.3)
                g = await geometry(".research-chat-reopen")
                await check(
                    value
                    + f" right control at {width}px / DPR {scale} does not overlap or overflow",
                    not g["overflow"]
                    and g["right"] <= width - 15
                    and g["y"] >= g["headerBottom"] + 11,
                    geometry=g,
                )
                if width == 390:
                    await b.js(
                        "document.querySelector('.research-main').scrollTop=1000"
                    )
                    await b.shot(
                        public
                        / (
                            "right-"
                            + ("current" if value == "Текущая" else "pulse")
                            + "-mobile-collapsed.png"
                        )
                    )
                    await open_panel()
                    await check(
                        value
                        + " mobile uses linked existing drawer without focusing textarea or a duplicate opener",
                        await b.js(
                            "document.querySelector('#workspace-chat').matches('dialog[open]')&&document.activeElement.matches('[data-chat-collapse]')&&!document.querySelector('.research-composer textarea[autofocus]')&&Array.from(document.querySelectorAll('.research-mobile-nav button')).every(n=>n.textContent!=='Чат')&&!document.querySelector('.research-chat-reopen')"
                        ),
                    )
                    await b.shot(
                        public
                        / (
                            "right-"
                            + ("current" if value == "Текущая" else "pulse")
                            + "-mobile-open.png"
                        )
                    )
                    await close()
            await b.call(
                "Emulation.setDeviceMetricsOverride",
                {
                    "width": 1440,
                    "height": 1000,
                    "deviceScaleFactor": 1,
                    "mobile": False,
                },
            )
            await asyncio.sleep(0.25)
        await b.call(
            "Emulation.setEmulatedMedia",
            {"features": [{"name": "prefers-reduced-motion", "value": "reduce"}]},
        )
        await check(
            "reduced-motion setting still removes the grid transition",
            await b.js(
                "getComputedStyle(document.querySelector('.research-shell')).transitionDuration==='0s'"
            ),
        )
        await b.call("Emulation.setEmulatedMedia", {"features": []})
        await b.js(
            "document.querySelector('.research-projects .project-open[aria-current=page]').dataset.restore='true';Array.from(document.querySelectorAll('.research-projects .project-open')).find(n=>n.title==='Новый чат').click()"
        )
        await b.wait("!!document.querySelector('#research-query')")
        await check(
            "switching to an empty project shows no previous chat control, messages, products or collection",
            await b.js(
                "!document.querySelector('.research-chat-reopen,#workspace-chat,[data-message-id],.product-card,.research-hud-selected,.research-collection-shortcut')"
            ),
        )
        await b.js("document.querySelector('.project-open[data-restore]').click()")
        await b.wait(
            "!!document.querySelector('.research-chat-reopen')&&document.querySelectorAll('.product-card').length>=12"
        )
        await open_panel()
        await check(
            "switching back restores the scoped history and draft",
            await b.js(
                "document.querySelector('#research-message').value==='Черновик справа'&&document.querySelectorAll('[data-message-id]').length===4"
            ),
        )
        await b.call("Page.navigate", {"url": "http://127.0.0.1:3000/offers"})
        await b.wait("!!document.querySelector('.app-navbar')")
        await check(
            "chat control is absent from the offers page and navbar title stays removed",
            await b.js(
                "!document.querySelector('.workspace-chat-toggle,[data-testid=header-project-title]')"
            ),
        )
    finally:
        await b.ws.close()
