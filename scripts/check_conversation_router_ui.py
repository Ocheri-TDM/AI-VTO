"""Playwright acceptance: refine, reload and undo retain the active subject."""

import json
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

API = "http://127.0.0.1:8000"
WEB = "http://127.0.0.1:3000"

with sync_playwright() as playwright:
    api = playwright.request.new_context(base_url=API)
    stamp = int(time.time())
    registered = api.post("/api/auth/register", data={"name": f"router-ui-{stamp}", "password": "Router-ui-2026"})
    assert registered.ok, registered.text()
    chat = api.post("/api/chats", data={"project_name": f"Router UI {stamp}"}).json()
    project = next(item for item in api.get("/api/projects").json() if item["chat_id"] == chat["id"])
    base = api.post(f"/api/projects/{project['id']}/requests", data={"query": "карандаш"})
    assert base.ok, base.text()

    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context(storage_state=api.storage_state(), viewport={"width": 1440, "height": 900})
    context.add_init_script(f"localStorage.setItem('forma-chat-id', {json.dumps(chat['id'])})")
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(WEB, wait_until="networkidle")
    page.locator(".research-heading").wait_for(timeout=60_000)
    title_before = page.locator(".research-heading h1").text_content()
    composer = page.locator(".research-composer textarea").last
    composer.fill("оставь синие")
    composer.press("Enter")
    page.get_by_text("оставь синие", exact=True).wait_for(timeout=60_000)
    page.wait_for_function("document.querySelector('.research-stats')?.textContent?.includes('17')")
    refined = {
        "title": page.locator(".research-heading h1").text_content(),
        "stats": page.locator(".research-stats").text_content(),
        "cards": page.locator(".research-grid .product-card").count(),
    }
    page.reload(wait_until="networkidle")
    page.locator(".research-heading").wait_for(timeout=60_000)
    reloaded = {
        "title": page.locator(".research-heading h1").text_content(),
        "stats": page.locator(".research-stats").text_content(),
        "message_visible": page.get_by_text("оставь синие", exact=True).count() > 0,
    }
    composer = page.locator(".research-composer textarea").last
    composer.fill("отмени")
    composer.press("Enter")
    page.wait_for_function("document.querySelector('.research-stats')?.textContent?.includes('240')")
    undone = {"title": page.locator(".research-heading h1").text_content(),
              "stats": page.locator(".research-stats").text_content()}
    result = {"title_before": title_before, "refined": refined, "reloaded": reloaded,
              "undone": undone, "page_errors": errors}
    assert title_before == refined["title"] == reloaded["title"] == undone["title"] == "карандаш"
    assert refined["cards"] == 17 and reloaded["message_visible"] and not errors
    Path(".local/conversation-router-ui.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    page.screenshot(path=".local/final/ui/conversation-router.png", full_page=True)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    browser.close()
    api.dispose()
