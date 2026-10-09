"""Playwright acceptance for Composite mutation persistence and single workspace."""

import json
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

API = "http://127.0.0.1:8000"
WEB = "http://127.0.0.1:3000"

with sync_playwright() as playwright:
    api = playwright.request.new_context(base_url=API, timeout=300_000)
    stamp = int(time.time())
    assert api.post("/api/auth/register", data={
        "name": f"mutation-ui-{stamp}", "password": "Mutation-ui-2026"
    }).ok
    chat = api.post("/api/chats", data={"project_name": f"Mutation UI {stamp}"}).json()
    project = next(item for item in api.get("/api/projects").json() if item["chat_id"] == chat["id"])
    initial = api.post(f"/api/projects/{project['id']}/requests", data={
        "query": "собери 5 наборов из футболок, ручек, бутылок и кружек"
    })
    assert initial.ok, initial.text()
    mutation = api.post(f"/api/projects/{project['id']}/requests", data={
        "query": "футболки сделай красными"
    })
    assert mutation.ok, mutation.text()

    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context(storage_state=api.storage_state(), viewport={"width": 1440, "height": 900})
    context.add_init_script(f"localStorage.setItem('forma-chat-id', {json.dumps(chat['id'])})")
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(WEB, wait_until="networkidle")
    page.locator("[data-testid=hard-request]").wait_for(timeout=90_000)
    page.get_by_text("футболки сделай красными", exact=True).last.wait_for(timeout=90_000)
    page.reload(wait_until="networkidle")
    page.locator("[data-testid=hard-request]").wait_for(timeout=90_000)
    result = {
        "workspace_count": page.locator("[data-testid=hard-request]").count(),
        "simple_grid_count": page.locator(".research-grid").count(),
        "message_persisted": page.get_by_text("футболки сделай красными", exact=True).count() > 0,
        "page_errors": errors,
        "horizontal_overflow": page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth"),
    }
    assert result == {
        "workspace_count": 1,
        "simple_grid_count": 0,
        "message_persisted": True,
        "page_errors": [],
        "horizontal_overflow": False,
    }
    Path(".local/composite-mutation-ui.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    page.screenshot(path=".local/final/ui/composite-mutation.png", full_page=True)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    browser.close()
    api.dispose()
