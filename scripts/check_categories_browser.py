"""Installed Edge acceptance for query chips; dedicated profile on port 9226."""

import asyncio
import json
import sys
from pathlib import Path

import httpx
import websockets
from check_palette_browser import Browser

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / ".local/query-categories-acceptance.json"


async def main():
    state = json.loads(LEDGER.read_text("utf-8"))
    project = state["browser_project"]
    checks = []
    b = Browser()
    await b.connect(state["users"][0]["cookies"])
    second = None

    async def check(label, expression, browser=b):
        await browser.wait(expression)
        checks.append({"check": label, "passed": True})
        print(label, flush=True)

    async def submit(query):
        await b.js(
            "document.querySelector('.research-composer textarea').focus();document.querySelector('.research-composer textarea').select()"
        )
        await b.call("Input.insertText", {"text": query})
        await b.call(
            "Input.dispatchKeyEvent",
            {
                "type": "keyDown",
                "key": "Enter",
                "code": "Enter",
                "windowsVirtualKeyCode": 13,
            },
        )
        await b.call(
            "Input.dispatchKeyEvent",
            {
                "type": "keyUp",
                "key": "Enter",
                "code": "Enter",
                "windowsVirtualKeyCode": 13,
            },
        )
        await b.wait(
            "document.querySelector('.research-composer button').textContent!=='Обрабатываю…' && document.querySelector('.research-composer textarea').value==='' && !document.querySelector('[role=alert]')"
        )

    async def click_branch(index, kind="eye", browser=b):
        await browser.js(
            f"document.querySelectorAll('.query-category-chip')[{index}].querySelectorAll('button')[{0 if kind == 'eye' else 1}].click()"
        )

    async def settled(browser=b):
        await browser.wait(
            "!document.querySelector('.research-hud-status [role=status]') && !document.querySelector('[role=alert]')"
        )

    async def show_all(browser=b):
        await browser.js(
            "document.querySelector('.query-categories-heading button').click()"
        )
        await settled(browser)

    async def server(browser=b):
        return await browser.js(
            f"fetch('/api/researches/chat/{project['chat_id']}').then(r=>r.json())"
        )

    try:
        await b.call(
            "Page.addScriptToEvaluateOnNewDocument",
            {
                "source": f"localStorage.setItem('forma-chat-id',{json.dumps(project['chat_id'])})"
            },
        )
        sse_setup = None
        if "--sse-smoke" in sys.argv:
            sse_setup = await b.call(
                "Page.addScriptToEvaluateOnNewDocument",
                {
                    "source": """
              window.__nativeCategoryEventSource=window.EventSource;
              window.__sseSources=[];
              window.EventSource=class {
                constructor(url){this.url=url;this.listeners={};window.__sseSources.push(this)}
                addEventListener(type,callback){this.listeners[type]=callback}
                close(){this.closed=true}
              };
              window.__nativeCategoryFetch=window.fetch;
              let initial=true;
              window.fetch=async(...args)=>{
                const result=await window.__nativeCategoryFetch(...args);
                const url=String(args[0]);
                if(initial&&url.includes('/researches/chat/')){
                  initial=false;const body=await result.clone().json();
                  if(body){window.__sseOldId=body.id;body.status='RUNNING';return new Response(JSON.stringify(body),{status:200,headers:{'Content-Type':'application/json'}})}
                }
                if(window.__sseOldId&&url.endsWith('/researches/'+window.__sseOldId)){
                  window.__sseOldRead=true;await new Promise(resolve=>setTimeout(resolve,500));
                }
                return result;
              };
            """
                },
            )
        await b.call("Page.navigate", {"url": "http://127.0.0.1:3000/"})
        await b.wait(
            f"document.querySelector('.research-top-project')?.textContent==={json.dumps(project['name'])}"
        )
        if "--sse-smoke" in sys.argv:
            await b.wait(
                'window.__sseSources?.length>0 && window.__sseSources[0].listeners["research.updated"]'
            )
            await submit("зонты")
            await b.wait(
                "document.querySelectorAll('.query-category-chip').length===1 && document.querySelector('.query-category-caption strong').textContent.includes('Зонты')"
            )
            assert await b.js("window.__sseSources[0].closed===true")
            await b.js(
                'window.__sseSources[0].listeners["research.updated"](new MessageEvent("research.updated",{data:JSON.stringify({id:window.__sseOldId,version:0})}))'
            )
            await b.wait("window.__sseOldRead===true")
            await asyncio.sleep(0.7)
            await check(
                "Queued old SSE callback cannot replace the new active query",
                "document.querySelectorAll('.query-category-chip').length===1 && document.querySelector('.query-category-caption strong').textContent.includes('Зонты') && document.querySelector('.research-selected-count').textContent==='3'",
            )
            await b.call(
                "Page.removeScriptToEvaluateOnNewDocument",
                {"identifier": sse_setup["identifier"]},
            )
            await b.js(
                "window.fetch=window.__nativeCategoryFetch;window.EventSource=window.__nativeCategoryEventSource"
            )
            await b.call("Page.reload", {"ignoreCache": True})
            await b.wait(
                "document.querySelector('.query-category-caption strong')?.textContent.includes('Зонты')"
            )
            state["browser_checks"] = state.get("browser_checks", []) + checks
            LEDGER.write_text(json.dumps(state, ensure_ascii=False, indent=2), "utf-8")
            print("Stale SSE callback smoke passed")
            return
        if "--undo-smoke" in sys.argv:
            await submit("ручки, бутылки, рюкзаки")
            await click_branch(2, "remove")
            await settled()
            await b.js(
                "document.querySelector('.query-category-notice button').click()"
            )
            await settled()
            await submit("назад")
            await check(
                "Chat Undo restores the contextual removal notice",
                "document.querySelectorAll('.query-category-chip').length===2 && document.querySelector('.query-category-notice button')?.textContent==='Вернуть'",
            )
            await b.js(
                "document.querySelector('.query-category-notice button').click()"
            )
            await settled()
            await check(
                "Context restore after Undo retains original order and collection",
                "document.querySelectorAll('.query-category-chip').length===3 && document.querySelectorAll('.query-category-caption strong')[2].textContent.includes('Рюкзаки') && document.querySelector('.research-selected-count').textContent==='3'",
            )
            state["browser_checks"] = state.get("browser_checks", []) + checks
            LEDGER.write_text(json.dumps(state, ensure_ascii=False, indent=2), "utf-8")
            print("Undo smoke passed")
            return
        await submit("ручки, бутылки, рюкзаки")
        await check(
            "Three server chips in query order before loading later pages",
            "[...document.querySelectorAll('.query-category-caption strong')].map(e=>e.firstChild.textContent.trim()).join('|')==='Ручки|Бутылки|Рюкзаки' && document.querySelectorAll('.research-main .product-card').length===100",
        )
        initial = await server()
        expected = initial["matching_count"]
        while await b.js(
            "[...document.querySelectorAll('button')].some(e=>e.textContent.startsWith('Показать ещё 100'))"
        ):
            old = await b.js(
                "document.querySelectorAll('.research-main .product-card').length"
            )
            await b.js(
                "[...document.querySelectorAll('button')].find(e=>e.textContent.startsWith('Показать ещё 100')).click()"
            )
            await b.wait(
                f"document.querySelectorAll('.research-main .product-card').length>{old}"
            )
        assert (
            await b.js(
                "document.querySelectorAll('.research-main .product-card').length"
            )
            == expected
        )
        for n, label in enumerate(["Ручки", "Бутылки", "Рюкзаки"], 1):
            await b.js(
                f"[...document.querySelectorAll('.research-main .product-card')].find(e=>e.querySelector('.product-category').textContent.trim()==={json.dumps(label)}).querySelector('.product-checkbox input').click()"
            )
            await b.wait(
                f"document.querySelector('.research-selected-count').textContent==='{n}'"
            )
        selected = await b.js(
            f"fetch('/api/projects/{project['id']}/collection').then(r=>r.json())"
        )
        assert len(selected["selected_ids"]) == 3
        state["browser_selected_ids"] = selected["selected_ids"]
        await click_branch(1)
        await settled()
        await check(
            "Hide removes bottle branch across pages and retains all selections",
            "document.querySelectorAll('.query-category-chip')[1].classList.contains('is-hidden') && document.querySelector('.research-selected-count').textContent==='3' && document.querySelectorAll('.research-main .product-card').length===100",
        )
        hidden = await server()
        assert (
            hidden["matching_count"]
            == initial["matching_count"] - initial["query_categories"][1]["count"]
        )
        assert (
            hidden["query_categories"][1]["count"]
            == initial["query_categories"][1]["count"]
        )
        geometry = await b.js(
            "(()=>{let f=document.querySelector('.research-hud-filters').getBoundingClientRect(),s=document.querySelector('.research-hud-selected').getBoundingClientRect();return {ratio:f.width/s.width,overlap:f.right>s.left}})()"
        )
        assert 3 < geometry["ratio"] < 5 and not geometry["overlap"]
        await b.shot(ROOT / ".local/categories-desktop.png")
        await click_branch(1)
        await settled()
        assert (await server())["matching_count"] == expected
        await click_branch(2, "remove")
        await settled()
        await check(
            "Remove changes title and keeps selected backpack",
            "document.querySelectorAll('.query-category-chip').length===2 && !document.querySelector('.research-heading h1').textContent.includes('Рюкзак') && document.querySelector('.research-selected-count').textContent==='3' && document.querySelector('.query-category-notice').textContent.includes('Рюкзаки')",
        )
        await b.js("document.querySelector('.query-category-notice button').click()")
        await settled()
        await check(
            "Context restore returns exact branch order",
            "document.querySelectorAll('.query-category-chip').length===3 && document.querySelectorAll('.query-category-caption strong')[2].textContent.includes('Рюкзаки')",
        )
        await b.js(
            "let e=document.querySelector('.research-desktop-filters select');e.value='BLACK';e.dispatchEvent(new Event('change',{bubbles:true}))"
        )
        await settled()
        filtered = await server()
        assert filtered["view"]["filters"]["colors"] == ["BLACK"]
        await b.js(
            "document.querySelectorAll('.query-category-chip').forEach(e=>e.querySelector('button').click())"
        )
        await check(
            "All hidden is explicit and hidden counts remain intact",
            "document.querySelector('.research-empty h2')?.textContent==='Все категории скрыты' && document.querySelectorAll('.research-main .product-card').length===0",
        )
        all_hidden = await server()
        assert all_hidden["eligible_total"] == filtered["matching_count"]
        assert [c["count"] for c in all_hidden["query_categories"]] == [
            c["count"] for c in filtered["query_categories"]
        ]
        await b.shot(ROOT / ".local/categories-all-hidden.png")
        await show_all()
        assert (await server())["view"]["filters"]["colors"] == ["BLACK"]
        await click_branch(1)
        await settled()
        before_reload = await server()
        await b.call("Page.reload", {"ignoreCache": True})
        await check(
            "F5 restores category visibility, filter and project selection",
            "document.querySelectorAll('.query-category-chip').length===3 && document.querySelectorAll('.query-category-chip')[1].classList.contains('is-hidden') && document.querySelector('.research-selected-count').textContent==='3' && document.querySelector('.research-desktop-filters select').value==='BLACK'",
        )
        assert (await server())["view"] == before_reload["view"]
        await submit("зонты")
        await check(
            "Standalone umbrella search starts visible and keeps selection",
            "document.querySelectorAll('.query-category-chip').length===1 && document.querySelector('.query-category-caption strong').textContent.includes('Зонты') && !document.querySelector('.query-category-chip').classList.contains('is-hidden') && document.querySelector('.research-selected-count').textContent==='3'",
        )
        await submit("Ручка шариковая Tippo")
        await check(
            "Model is a constraint and never a second category",
            "document.querySelectorAll('.query-category-chip').length===1 && document.querySelector('.query-category-caption strong').textContent.includes('Ручки') && document.querySelector('[aria-label=\"Активные условия\"]').textContent.includes('Tippo')",
        )
        await click_branch(0, "remove")
        await settled()
        await check(
            "Last removal is not unrestricted catalog",
            "document.querySelector('.research-empty h2')?.textContent==='В запросе не осталось категорий' && document.querySelectorAll('.research-main .product-card').length===0",
        )
        await b.js("document.querySelector('.query-category-notice button').click()")
        await settled()
        await check(
            "Last category contextual restore",
            "document.querySelectorAll('.research-main .product-card').length===6",
        )
        await submit("Molti")
        await check(
            "Brand-only explicitly has unrestricted categories",
            "document.querySelector('.query-categories').textContent.includes('Категории не ограничены') && document.querySelectorAll('.query-category-chip').length===0",
        )
        await submit("носки")
        await check(
            "Known category keeps its chip at zero matches",
            "document.querySelectorAll('.query-category-chip').length===1 && document.querySelector('.query-category-caption strong').textContent.includes('Носки')",
        )
        await submit("ручки, бутылки, рюкзаки")
        # Delay the old page response while a category mutation commits.
        await b.js(
            "window.__categoryFetch=window.fetch;window.fetch=async(...a)=>{const r=await window.__categoryFetch(...a);if(String(a[0]).includes('/products?'))await new Promise(v=>setTimeout(v,1100));return r}"
        )
        await b.js(
            "[...document.querySelectorAll('button')].find(e=>e.textContent.startsWith('Показать ещё 100')).click();document.querySelectorAll('.query-category-chip')[1].querySelector('button').click()"
        )
        await settled()
        await asyncio.sleep(1.3)
        await check(
            "Old page cannot append after category mutation",
            "document.querySelectorAll('.research-main .product-card').length===100 && document.querySelectorAll('.query-category-chip')[1].classList.contains('is-hidden')",
        )
        await b.js("window.fetch=window.__categoryFetch")
        await click_branch(0)
        await b.wait(
            "document.querySelectorAll('.query-category-chip')[0].classList.contains('is-hidden')"
        )
        await click_branch(0)
        await settled()
        await check(
            "Rapid hide then show preserves final desired state",
            "!document.querySelectorAll('.query-category-chip')[0].classList.contains('is-hidden')",
        )
        # A second actual page uses the same isolated browser profile/session.
        target = await b.call("Target.createTarget", {"url": "about:blank"})
        async with httpx.AsyncClient() as client:
            tabs = (await client.get("http://127.0.0.1:9226/json/list")).json()
        second = Browser()
        second.ws = await websockets.connect(
            next(
                t["webSocketDebuggerUrl"] for t in tabs if t["id"] == target["targetId"]
            )
        )
        second.number = 0
        await second.call("Page.enable")
        await second.call("Network.enable")
        await second.call("Page.navigate", {"url": "http://127.0.0.1:3000/"})
        await second.wait(
            "document.querySelectorAll('.query-category-chip').length===3"
        )
        await click_branch(0, browser=second)
        await settled(second)
        await click_branch(2)
        await settled()  # stale first tab rebases on 409
        await check(
            "Concurrent tab visibility merges without resurrecting branches",
            "document.querySelector('.research-empty h2')?.textContent==='Все категории скрыты'",
        )
        await second.js("window.dispatchEvent(new Event('focus'))")
        await check(
            "Second tab reconciles on focus",
            "document.querySelector('.research-empty h2')?.textContent==='Все категории скрыты'",
            second,
        )
        await show_all(second)
        await b.js("window.dispatchEvent(new Event('focus'))")
        await check(
            "Show all reconciles both tabs",
            "[...document.querySelectorAll('.query-category-chip')].every(e=>!e.classList.contains('is-hidden')) && document.querySelectorAll('.research-main .product-card').length===100",
        )
        await second.call("Page.close")
        await second.ws.close()
        second = None
        # Failed save must roll back the optimistic hidden chip and show an error.
        await b.js(
            "window.__categoryFetch=window.fetch;window.fetch=(...a)=>String(a[0]).endsWith('/view')&&a[1]?.method==='PATCH'?Promise.reject(new Error('Acceptance offline')):window.__categoryFetch(...a)"
        )
        await click_branch(1)
        await check(
            "Failed save rolls back local hidden projection",
            "document.querySelector('[role=alert]') && !document.querySelectorAll('.query-category-chip')[1].classList.contains('is-hidden')",
        )
        await b.js(
            "window.fetch=window.__categoryFetch;document.querySelector('[aria-label=\"Закрыть уведомление\"]').click()"
        )
        # Project switching ignores the old workspace and restores each own view.
        primary = state["projects"][0]
        await b.js(
            f"[...document.querySelectorAll('.project-item>button:first-child')].find(e=>e.querySelector('strong').textContent==={json.dumps(primary['name'])}).click()"
        )
        await check(
            "Project switch loads the other saved visibility/filter",
            f"document.querySelector('.research-top-project')?.textContent==={json.dumps(primary['name'])} && document.querySelector('.research-desktop-filters select')?.value==='BLACK'",
        )
        await b.js(
            f"[...document.querySelectorAll('.project-item>button:first-child')].find(e=>e.querySelector('strong').textContent==={json.dumps(project['name'])}).click()"
        )
        await b.wait(
            f"document.querySelector('.research-top-project')?.textContent==={json.dumps(project['name'])} && document.querySelectorAll('.query-category-chip').length===3"
        )
        await b.call(
            "Emulation.setDeviceMetricsOverride",
            {"width": 390, "height": 844, "deviceScaleFactor": 1, "mobile": True},
        )
        await check(
            "Mobile chips fit and retain readable labelled controls",
            "document.documentElement.scrollWidth<=390 && document.querySelectorAll('.query-category-chip').length===3 && [...document.querySelectorAll('.query-category-action')].every(e=>e.getBoundingClientRect().width>=40)",
        )
        await click_branch(1)
        await settled()
        await b.shot(ROOT / ".local/categories-mobile.png")
        state["browser_checks"] = checks
        state["hud_geometry"] = geometry
        LEDGER.write_text(json.dumps(state, ensure_ascii=False, indent=2), "utf-8")
        print("Browser checks passed:", len(checks))
    finally:
        if second:
            await second.ws.close()
        await b.ws.close()


if __name__ == "__main__":
    asyncio.run(main())
