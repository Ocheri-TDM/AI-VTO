"""Edge fault checks: frontend assets are read-only; every /api goes to 18000.

The loopback proxy lives only inside this harness process and closes in finally.
No malformed query, injected event or artificial failure reaches the main API.
"""

import asyncio
import json
import time

import httpx
from check_palette_browser import Browser

from quality.acceptance import BudgetClient, submit


async def run(state, record):
    BudgetClient.counter = state["isolated_http_count"]
    # One bounded final rerun after the real hydration concurrency defect was
    # repaired. Load phases remain capped at their original 5000 requests.
    BudgetClient.limit = 5032
    state["terminal_browser_budget"] = {
        "original": 5000,
        "extended": 5032,
        "reason": "one final isolated Edge SSE/XSS run after hydration fix; no further load",
    }
    record(state)
    BudgetClient.deadline = time.monotonic() + 180
    user = state["http_users"][0]
    project = user["project"]
    checks = []
    async with BudgetClient(base_url="http://127.0.0.1:18000", cookies=user["cookies"], timeout=20) as api:
        original = await submit(api, project["id"], "ручки, бутылки, рюкзаки")
        collection = (await api.get(f"/api/projects/{project['id']}/collection")).json()
        async with httpx.AsyncClient(base_url="http://127.0.0.1:3000", timeout=20) as assets:

            async def forward(reader, writer):
                try:
                    head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
                    lines = head.decode("latin1").split("\r\n")
                    method, path, _ = lines[0].split(" ", 2)
                    assert path.startswith("/") and not path.startswith("//")
                    headers = dict(line.split(": ", 1) for line in lines[1:] if ": " in line)
                    length = int(next((v for k, v in headers.items() if k.lower() == "content-length"), "0"))
                    assert length <= 65536
                    body = await reader.readexactly(length)
                    headers = {
                        k: v
                        for k, v in headers.items()
                        if k.lower() not in ("connection", "accept-encoding", "transfer-encoding")
                    }
                    backend = api if path.startswith("/api/") else assets
                    response = await backend.request(method, path, headers=headers, content=body)
                    result_headers = [
                        (k, v)
                        for k, v in response.headers.multi_items()
                        if k.lower()
                        not in ("content-encoding", "content-length", "connection", "transfer-encoding")
                    ]
                    payload = response.content
                    output = f"HTTP/1.1 {response.status_code} {response.reason_phrase}\r\n"
                    output += "".join(k + ": " + v + "\r\n" for k, v in result_headers)
                    output += f"Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n"
                    writer.write(output.encode("latin1") + payload)
                    await writer.drain()
                finally:
                    writer.close()
                    await writer.wait_closed()

            server = await asyncio.start_server(forward, "127.0.0.1", 18001)
            b = Browser()
            await b.connect(user["cookies"])
            setup = None
            try:
                setup = await b.call(
                    "Page.addScriptToEvaluateOnNewDocument",
                    {
                        "source": """
                  localStorage.setItem('forma-chat-id', CHAT_ID);
                  window.__sources=[];
                  window.EventSource=class {
                    constructor(url){this.url=url;this.listeners={};window.__sources.push(this)}
                    addEventListener(name,callback){this.listeners[name]=callback}
                    close(){this.closed=true}
                  };
                  const nativeFetch=window.fetch;let first=true;
                  window.fetch=async(...args)=>{
                    const response=await nativeFetch(...args);const url=String(args[0]);
                    if(first&&url.includes('/researches/chat/')){
                      first=false;const value=await response.clone().json();window.__oldId=value.id;
                      value.status='RUNNING';return new Response(JSON.stringify(value),{status:200,headers:{'Content-Type':'application/json'}});
                    }
                    if(window.__oldId&&url.endsWith('/researches/'+window.__oldId)){
                      window.__lateRead=true;await new Promise(resolve=>setTimeout(resolve,500));
                    }
                    return response;
                  };
                """.replace("CHAT_ID", json.dumps(project["chat_id"]))
                    },
                )
                await b.call("Page.navigate", {"url": "http://127.0.0.1:18001/"})
                await b.wait(
                    "window.__sources?.length>0 && document.querySelectorAll('.query-category-chip').length===3"
                )
                await b.js("document.querySelector('.research-composer textarea').focus()")
                await b.call("Input.insertText", {"text": "Ручка шариковая Tippo"})
                await b.call(
                    "Input.dispatchKeyEvent",
                    {"type": "keyDown", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13},
                )
                await b.call(
                    "Input.dispatchKeyEvent",
                    {"type": "keyUp", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13},
                )
                await b.wait(
                    "window.__sources[0].closed===true && document.querySelectorAll('.query-category-chip').length===1"
                )
                await b.js(
                    "window.__sources[0].listeners['research.updated'](new MessageEvent('research.updated',{data:JSON.stringify({id:window.__oldId,version:0})}))"
                )
                await b.wait("window.__lateRead===true")
                await asyncio.sleep(0.7)
                assert await b.js(
                    "document.querySelectorAll('.query-category-chip').length===1 && !document.querySelector('[role=alert]')"
                ), await b.js(
                    "({chips:[...document.querySelectorAll('.query-category-chip')].map(e=>e.textContent),alerts:[...document.querySelectorAll('[role=alert]')].map(e=>e.textContent)})"
                )
                active = (await api.get(f"/api/researches/chat/{project['chat_id']}")).json()
                assert active["id"] != original["id"] and len(active["query_categories"]) == 1
                after = (await api.get(f"/api/projects/{project['id']}/collection")).json()
                assert (
                    after["selected_ids"] == collection["selected_ids"]
                    and after["version"] == collection["version"]
                )
                checks.append(
                    {"check": "late closed SSE callback cannot restore the old workspace", "pass": True}
                )
                # React must render request text as text, including in history.
                await b.js("document.querySelector('.research-composer textarea').focus()")
                await b.call(
                    "Input.insertText", {"text": 'ручки <img src=x onerror="window.__qualityCanary=1">'}
                )
                await b.call(
                    "Input.dispatchKeyEvent",
                    {"type": "keyDown", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13},
                )
                await b.call(
                    "Input.dispatchKeyEvent",
                    {"type": "keyUp", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13},
                )
                await b.wait(
                    "document.querySelector('.research-composer textarea').value==='' && document.querySelector('.research-composer button').textContent!=='Обрабатываю…'"
                )
                assert await b.js("!window.__qualityCanary && !document.querySelector('img[onerror]')")
                checks.append(
                    {"check": "harmless HTML/XSS canary remains inert in rendered UI", "pass": True}
                )
                return {
                    "status": "PASS",
                    "checks": checks,
                    "api": "isolated PostgreSQL/API only",
                    "frontend": "read-only local production assets",
                    "http_requests": BudgetClient.counter - state["isolated_http_count"],
                }
            finally:
                if setup:
                    await b.call(
                        "Page.removeScriptToEvaluateOnNewDocument", {"identifier": setup["identifier"]}
                    )
                await b.call("Page.navigate", {"url": "about:blank"})
                await b.ws.close()
                server.close()
                await server.wait_closed()
                state["isolated_http_count"] = BudgetClient.counter
                record(state)
