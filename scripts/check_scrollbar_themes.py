"""Check native scrollbar pixels using the shipped CSS in installed Edge.

Uses labelled UI fixtures on an anonymous page. No database writes or login.
"""

import asyncio
import json
import secrets
import shutil
import socket
import subprocess
from pathlib import Path

import httpx
import psutil
from check_chat_workflow import save
from check_palette_browser import Browser
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "docs/screenshots/scrollbar-pulse"

FIXTURE = r"""(()=>{
  for(const e of document.body.children)e.style.display='none';
  const page=document.createElement('main');page.id='scrollbar-fixture';
  page.style.cssText='padding:28px 40px;background:var(--canvas);color:var(--text);height:100vh;box-sizing:border-box';
  page.innerHTML='<h1 style="margin:0 0 8px">'+(document.documentElement.dataset.siteTheme==='pulse'?'Pulse':'Текущая тема')+' · нативная прокрутка</h1><p>UI fixture · проверка общего CSS без пользовательских данных</p><div style="display:grid;grid-template-columns:1fr 1fr;gap:24px"></div>';
  for(const [name,cls] of [['Результаты','research-main'],['Переписка','research-messages'],['Панель','sheet-body'],['Dropdown','ui-select-options']]){
    const panel=document.createElement('section');panel.innerHTML='<h2 style="font-size:16px">'+name+'</h2>';
    const scroller=document.createElement('div');scroller.className=cls+' native-scroll';
    scroller.style.cssText='height:310px;overflow:auto;padding:16px;background:var(--panel);border-radius:8px;min-height:0;flex:none';
    for(let i=1;i<=24;i++){const p=document.createElement('p');p.style.cssText='height:50px;margin:0 0 12px;padding:12px;border-bottom:1px solid var(--border)';p.textContent='Строка '+i+' · Колесо, клавиатура и перетаскивание';scroller.append(p);}
    panel.append(scroller);page.lastElementChild.append(panel);
  }
  document.body.append(page);
})()"""


def pixels(path, rect, color):
    with Image.open(path) as picture:
        crop = picture.convert("RGB").crop(
            (
                int(rect["right"]) - 10,
                int(rect["top"]),
                int(rect["right"]),
                int(rect["bottom"]),
            )
        )
        return sum(
            count
            for count, pixel in crop.getcolors(crop.width * crop.height)
            if pixel == color
        )


async def main():
    PUBLIC.mkdir(parents=True, exist_ok=True)
    with socket.socket() as probe:
        assert probe.connect_ex(("127.0.0.1", 9226)) != 0
    profile = (ROOT / ".local" / ("scrollbar-theme-" + secrets.token_hex(8))).resolve()
    process = await asyncio.to_thread(
        subprocess.Popen,
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
    b = None
    evidence = []

    async def check(label, condition, **details):
        evidence.append(
            {"check": label, "status": "PASS" if condition else "FAIL", **details}
        )
        save(PUBLIC / "checks.json", evidence)
        print(("PASS " if condition else "FAIL ") + label, flush=True)
        assert condition, label

    try:
        async with httpx.AsyncClient() as client:
            for _ in range(100):
                try:
                    if (
                        await client.get("http://127.0.0.1:9226/json/list")
                    ).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.2)
        b = Browser()
        await b.connect({})
        await b.call("Page.navigate", {"url": "http://127.0.0.1:3000/"})
        await b.wait("!!document.querySelector('.auth-card')")
        for theme, normal, track, hover, active in [
            (
                "current",
                (115, 127, 118),
                (24, 30, 26),
                (163, 178, 143),
                (163, 178, 143),
            ),
            ("pulse", (123, 131, 139), (243, 245, 242), (244, 41, 2), (207, 38, 8)),
        ]:
            await b.js(
                "localStorage.setItem('souvenir:site-theme'," + json.dumps(theme) + ")"
            )
            await b.call("Page.reload", {"ignoreCache": True})
            await b.wait("!!document.querySelector('.auth-card')")
            await check(
                theme + " restored by real theme bootstrap",
                await b.js(
                    "document.documentElement.dataset.siteTheme===" + json.dumps(theme)
                ),
            )
            await b.js(FIXTURE)
            await b.call(
                "Input.dispatchMouseEvent", {"type": "mouseMoved", "x": 10, "y": 10}
            )
            await asyncio.sleep(0.3)
            metrics = await b.js(
                "Array.from(document.querySelectorAll('#scrollbar-fixture .native-scroll')).map(n=>({class:n.className,width:getComputedStyle(n,'::-webkit-scrollbar').width,thumb:getComputedStyle(n,'::-webkit-scrollbar-thumb').backgroundColor,track:getComputedStyle(n,'::-webkit-scrollbar-track').backgroundColor,standard:getComputedStyle(n).scrollbarColor}))"
            )
            await check(
                theme + " applies distinct colors to every native scroll container",
                all(
                    v["width"] == "10px"
                    and v["standard"] == "auto"
                    and v["thumb"] == "rgb(" + ", ".join(map(str, normal)) + ")"
                    and v["track"] == "rgb(" + ", ".join(map(str, track)) + ")"
                    for v in metrics
                ),
                metrics=metrics,
            )
            rect = await b.js(
                "document.querySelector('#scrollbar-fixture .native-scroll').getBoundingClientRect().toJSON()"
            )
            normal_path = PUBLIC / (theme + "-normal.png")
            await b.shot(normal_path)
            await check(
                theme + " normal thumb is painted with its theme color",
                pixels(normal_path, rect, normal) > 30,
            )
            point = {"x": rect["right"] - 5, "y": rect["top"] + 20}
            await b.call("Input.dispatchMouseEvent", {"type": "mouseMoved", **point})
            await asyncio.sleep(0.3)
            hover_path = PUBLIC / (theme + "-hover.png")
            await b.shot(hover_path)
            await check(
                theme + " native hover uses its theme accent",
                pixels(hover_path, rect, hover) > 30,
            )
            await b.call(
                "Input.dispatchMouseEvent",
                {"type": "mousePressed", "button": "left", "clickCount": 1, **point},
            )
            await asyncio.sleep(0.2)
            active_path = PUBLIC / (theme + "-active.png")
            await b.shot(active_path)
            await check(
                theme + " active thumb uses its theme accent",
                pixels(active_path, rect, active) > 30,
            )
            await b.call(
                "Input.dispatchMouseEvent",
                {
                    "type": "mouseMoved",
                    "button": "left",
                    "buttons": 1,
                    "x": point["x"],
                    "y": point["y"] + 80,
                },
            )
            await asyncio.sleep(0.25)
            await b.call(
                "Input.dispatchMouseEvent",
                {
                    "type": "mouseReleased",
                    "button": "left",
                    "clickCount": 1,
                    "x": point["x"],
                    "y": point["y"] + 80,
                },
            )
            await asyncio.sleep(0.25)
            await check(
                theme + " native scrollbar drag scrolls content",
                await b.js(
                    "document.querySelector('#scrollbar-fixture .native-scroll').scrollTop>100"
                ),
            )
            await b.call(
                "Emulation.setEmulatedMedia",
                {"features": [{"name": "forced-colors", "value": "active"}]},
            )
            await check(
                theme + " high contrast keeps system scrollbar",
                await b.js(
                    "getComputedStyle(document.querySelector('#scrollbar-fixture .native-scroll')).scrollbarColor==='auto'&&getComputedStyle(document.querySelector('#scrollbar-fixture .native-scroll'),'::-webkit-scrollbar').width==='auto'"
                ),
            )
            await b.call("Emulation.setEmulatedMedia", {"features": []})
    finally:
        if b:
            await b.ws.close()
        if psutil.pid_exists(process.pid):
            owned = psutil.Process(process.pid)
            assert "--user-data-dir=" + str(profile) in owned.cmdline()
            tree = [owned, *owned.children(recursive=True)]
            for p in reversed(tree):
                try:
                    p.terminate()
                except psutil.NoSuchProcess:
                    pass
            _, alive = await asyncio.to_thread(psutil.wait_procs, tree, timeout=5)
            for p in alive:
                p.kill()
            await asyncio.to_thread(psutil.wait_procs, alive, timeout=5)
        assert profile.parent == (
            ROOT / ".local"
        ).resolve() and profile.name.startswith("scrollbar-theme-")
        if profile.exists():
            shutil.rmtree(profile)


if __name__ == "__main__":
    asyncio.run(main())
