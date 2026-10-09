"""Playwright acceptance for a no-quantity Composite and quantity follow-up."""

import json
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

with sync_playwright() as playwright:
    stamp = int(time.time())
    api = playwright.request.new_context(base_url="http://127.0.0.1:8000")
    response = api.post(
        "/api/auth/register",
        data={"name": f"no-quantity-ui-{stamp}", "password": "No-quantity-ui-2026"},
    )
    assert response.ok, response.text()
    chat = api.post("/api/chats", data={"project_name": f"No quantity UI {stamp}"}).json()
    project = next(row for row in api.get("/api/projects").json() if row["chat_id"] == chat["id"])
    created = api.post(
        f"/api/projects/{project['id']}/requests",
        data={"query": "собери 50 наборов из футболок, ручек, бутылок и кружек"},
        timeout=120_000,
    )
    assert created.ok, created.text()

    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context(storage_state=api.storage_state(), viewport={"width": 1440, "height": 900})
    context.add_init_script(f"localStorage.setItem('forma-chat-id', {json.dumps(chat['id'])})")
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto("http://127.0.0.1:3000", wait_until="networkidle")
    workspace = page.get_by_test_id("hard-request")
    workspace.wait_for(timeout=60_000)
    workspace.locator(".hard-case").first.wait_for(timeout=60_000)
    before = workspace.locator(".hard-case").count()
    message_before = page.get_by_text(
        "собери 50 наборов из футболок, ручек, бутылок и кружек", exact=True
    ).count()
    page.reload(wait_until="networkidle")
    workspace = page.get_by_test_id("hard-request")
    workspace.locator(".hard-case").first.wait_for(timeout=60_000)
    after = workspace.locator(".hard-case").count()
    composer = page.locator(".research-composer textarea").last
    composer.fill("теперь от 250 штук")
    composer.press("Enter")
    page.get_by_text("теперь от 250 штук", exact=True).first.wait_for(timeout=90_000)
    page.wait_for_timeout(1000)
    active = api.get(f"/api/projects/{project['id']}/active-composite").json()
    result = {
        "visible_cases_before_reload": before,
        "visible_cases_after_reload": after,
        "message_persisted": bool(message_before),
        "active_target_cases": active["plan"]["target_sets"],
        "active_minimum_quantity": active["plan"]["quantity"]["minimum"],
        "active_quantity_required": active["plan"]["quantity"]["required"],
        "one_active_workspace": page.get_by_test_id("hard-request").count() == 1,
        "page_errors": errors,
        "horizontal_overflow": page.evaluate(
            "document.documentElement.scrollWidth > document.documentElement.clientWidth"
        ),
    }
    assert before > 0 and before == after
    assert result["message_persisted"] and result["active_minimum_quantity"] == 250
    assert result["one_active_workspace"] and not errors and not result["horizontal_overflow"]
    Path(".local/composite-no-quantity-ui.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    page.screenshot(path=".local/final/ui/composite-no-quantity.png", full_page=True)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    browser.close()
    api.dispose()
