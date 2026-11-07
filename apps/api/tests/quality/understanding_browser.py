"""Real installed Edge submits the four controls through the UI."""

import asyncio
import json
import os
import socket
import subprocess
from pathlib import Path

import httpx
from check_palette_browser import Browser

from quality.regression_acceptance import CONTROLS, LEDGER, PUBLIC, ROOT, call, save


async def run(state):
    with socket.socket() as probe:
        occupied = probe.connect_ex(("127.0.0.1", 9226)) == 0
    edge = Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe")
    profile = ROOT / ".local" / ("understanding-edge-" + state["run"])
    if occupied:
        import psutil

        parent = psutil.Process(state["edge_pid"])
        assert "--user-data-dir=" + str(profile) in parent.cmdline()
        processes = [parent, *parent.children(recursive=True)]
        listeners = {
            c.pid
            for c in psutil.net_connections(kind="tcp")
            if c.laddr and c.laddr.port == 9226 and c.status == "LISTEN"
        }
        assert listeners <= {p.pid for p in processes}
    else:
        process = subprocess.Popen(
            [
                str(edge),
                "--headless=new",
                "--disable-extensions",
                "--remote-debugging-port=9226",
                "--user-data-dir=" + str(profile),
                "--no-first-run",
                "--no-default-browser-check",
                "about:blank",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        state.update(edge_pid=process.pid, edge_profile=str(profile))
        save(LEDGER, state)
    async with httpx.AsyncClient() as client:
        for _ in range(80):
            try:
                if (await client.get("http://127.0.0.1:9226/json/list")).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.2)
    b = Browser()
    await b.connect(state["users"][0]["cookies"])
    record = PUBLIC / "browser.json"
    results = (
        json.loads(record.read_text("utf-8"))["results"]
        if os.environ.get("UNDERSTANDING_BROWSER_RESUME") == "1"
        else []
    )
    try:
        async with httpx.AsyncClient(
            base_url="http://127.0.0.1:8000", cookies=state["users"][0]["cookies"], timeout=60
        ) as client:
            for mobile in (False, True):
                await b.call(
                    "Emulation.setDeviceMetricsOverride",
                    {
                        "width": 390 if mobile else 1440,
                        "height": 844 if mobile else 1000,
                        "deviceScaleFactor": 1,
                        "mobile": mobile,
                    },
                )
                for name, query in CONTROLS.items():
                    if any(r["case"] == name and r["mobile"] == mobile for r in results):
                        continue
                    chat = await call(
                        client, "POST", "/api/chats", json={"project_name": "Edge understanding " + name}
                    )
                    project = next(
                        p for p in await call(client, "GET", "/api/projects") if p["chat_id"] == chat["id"]
                    )
                    state["projects"].append(project["id"])
                    save(LEDGER, state)
                    await b.call(
                        "Page.addScriptToEvaluateOnNewDocument",
                        {"source": f"localStorage.setItem('forma-chat-id',{json.dumps(chat['id'])})"},
                    )
                    await b.call("Page.navigate", {"url": "http://127.0.0.1:3000/"})
                    await b.wait("!!document.querySelector('#initial-query, #research-query')", timeout=45)
                    await b.js("document.querySelector('#initial-query, #research-query').focus()")
                    await b.call("Input.insertText", {"text": query})
                    await b.wait(
                        "!document.querySelector('#initial-query, #research-query').closest('form').querySelector('button').disabled"
                    )
                    await b.js(
                        "document.querySelector('#initial-query, #research-query').closest('form').querySelector('button').click()"
                    )
                    await b.wait("document.querySelectorAll('.query-category-chip').length===4", timeout=90)
                    await b.wait("!document.querySelector('.research-hud-status [role=status]')")
                    await b.wait(
                        "Array.from(document.querySelectorAll('.research-desktop-filters .ui-select-trigger')).some(e=>e.textContent.includes('Красный')) && Array.from(document.querySelectorAll('.research-results-hud input')).some(e=>e.value==='200')"
                    )
                    research = await b.js(
                        "fetch('/api/researches/chat/" + chat["id"] + "').then(r=>r.json())"
                    )
                    assert research["source_mode"] == "index"  # GRID API contract, no legacy case allocator.
                    assert research["intent"]["models"] == []
                    assert len(research["intent"]["branches"]) == 4
                    assert all(
                        branch["colors"] == ["RED"] and branch["quantity"] == 200
                        for branch in research["intent"]["branches"]
                    )
                    hud = await b.js("document.querySelector('.research-results-hud').textContent")
                    assert "Красн" in hud
                    assert await b.js(
                        "Array.from(document.querySelectorAll('.research-results-hud input')).some(e=>e.value==='200')"
                    )
                    assert "Блокноты" in hud and "Ежедневники" not in hud
                    if mobile:
                        await b.js("document.querySelector('.research-mobile-filters-button').click()")
                        await b.wait(
                            "Array.from(document.querySelectorAll('dialog[open] .ui-select-trigger')).some(e=>e.textContent.includes('Красный')) && Array.from(document.querySelectorAll('dialog[open] input')).some(e=>e.value==='200')"
                        )
                        await b.call(
                            "Input.dispatchKeyEvent", {"type": "keyDown", "key": "Escape", "code": "Escape"}
                        )
                        await b.call(
                            "Input.dispatchKeyEvent", {"type": "keyUp", "key": "Escape", "code": "Escape"}
                        )
                    checkbox = await b.js(
                        "Boolean(document.querySelector('.product-card input[type=checkbox]'))"
                    )
                    if checkbox:
                        await b.js("document.querySelector('.product-card input[type=checkbox]').click()")
                        await b.wait(
                            "Number(document.querySelector('.research-selected-count')?.textContent)>=1"
                        )
                    await b.call("Page.reload", {"ignoreCache": True})
                    await b.wait("document.querySelectorAll('.query-category-chip').length===4")
                    if checkbox:
                        await b.wait(
                            "Number(document.querySelector('.research-selected-count')?.textContent)>=1"
                        )
                    assert await b.js("document.documentElement.scrollWidth<=innerWidth")
                    assert not await b.js(
                        "document.body.innerText.includes('Слишком много запросов')||document.body.innerText.includes('Повторить сохранение')"
                    )
                    path = PUBLIC / (name + ("-mobile" if mobile else "-desktop") + ".png")
                    await b.shot(path)
                    results.append(
                        {
                            "case": name,
                            "mobile": mobile,
                            "query": query,
                            "chips": 4,
                            "hud": hud,
                            "matching_count": research.get("matching_count"),
                            "selection_checked": checkbox,
                            "f5": True,
                            "no_overflow": True,
                            "no_rate_error": True,
                        }
                    )
                    save(PUBLIC / "browser.json", {"browser": "installed Microsoft Edge", "results": results})
                    print("Edge", name, "mobile" if mobile else "desktop", flush=True)
    finally:
        await b.ws.close()
