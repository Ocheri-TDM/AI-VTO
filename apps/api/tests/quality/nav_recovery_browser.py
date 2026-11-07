"""Installed Edge geometry and task-owned offer actions."""

import asyncio
import json
import socket
import subprocess

import httpx
from check_palette_browser import Browser


async def run(state, root, ledger, public, before=False):
    from check_chat_workflow import save

    profile = root / ".local" / ("nav-recovery-edge-" + state["run"])
    if not state.get("edge_pid"):
        with socket.socket() as probe:
            assert probe.connect_ex(("127.0.0.1", 9226)) != 0
        edge = subprocess.Popen(
            [
                "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
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
        state.update(edge_pid=edge.pid, edge_profile=str(profile))
        save(ledger, state)
        async with httpx.AsyncClient() as client:
            for _ in range(100):
                try:
                    if (await client.get("http://127.0.0.1:9226/json/list")).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.2)
    b = Browser()
    await b.connect(state["users"][0]["cookies"])
    evidence = []
    try:
        if not before:
            from quality.nav_recovery_browser_cases import run as cases

            return await cases(b, state, root, ledger, public)
        await b.call(
            "Emulation.setDeviceMetricsOverride",
            {"width": 1440, "height": 1000, "deviceScaleFactor": 1, "mobile": False},
        )
        for page in ["/", "/offers"]:
            await b.call("Page.navigate", {"url": "http://127.0.0.1:3000" + page})
            await b.wait("!!document.querySelector('.research-top')")
            await asyncio.sleep(0.5)
            geometry = await b.js(
                "(()=>{const h=document.querySelector('.research-top'),r=h.getBoundingClientRect();return {header:{height:r.height,top:r.top,bottom:r.bottom},controls:Array.from(h.querySelectorAll('button,a')).map(e=>{const a=e.getBoundingClientRect();return {label:e.textContent,height:a.height,top:a.top,bottom:a.bottom}}),overflow:document.documentElement.scrollWidth>innerWidth}})()"
            )
            evidence.append({"page": page, **geometry})
            await b.shot(
                public
                / (("before-" if before else "after-") + ("chat" if page == "/" else "offers") + ".png")
            )
        save(public / ("navbar-before.json" if before else "navbar-after.json"), evidence)
        print("Edge geometry saved", json.dumps(evidence, ensure_ascii=True))
    finally:
        await b.ws.close()
