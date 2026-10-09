import json
import os
from pathlib import Path

from playwright.sync_api import sync_playwright

live = json.loads(Path(os.environ.get(
    "COMPOSITE_SOURCE", ".local/composite-conversation-live-v2.json"
)).read_text(encoding="utf-8-sig"))
with sync_playwright() as p:
    api = p.request.new_context(base_url="http://127.0.0.1:8000")
    response = api.post(
        "/api/auth/login",
        data={"name": live["user"], "password": "Composite-pass-2026"},
    )
    assert response.ok, response.text()
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(
        storage_state=api.storage_state(), viewport={"width": 1440, "height": 900}
    )
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto("http://127.0.0.1:3000", wait_until="networkidle")
    page.get_by_text("Composite persistence acceptance", exact=True).first.click()
    page.get_by_test_id("hard-request").wait_for()
    before = page.get_by_test_id("hard-request").locator(".hard-case").count()
    assert page.locator("aside.research-chat textarea").is_visible()
    messages_before = page.locator("aside.research-chat .research-message").count()
    pagination = page.locator(".hard-pagination span").text_content()
    page.reload(wait_until="networkidle")
    page.get_by_test_id("hard-request").wait_for()
    result = {
        "before": before,
        "after": page.get_by_test_id("hard-request").locator(".hard-case").count(),
        "messagesBefore": messages_before,
        "messagesAfter": page.locator("aside.research-chat .research-message").count(),
        "textareaRestored": page.locator("aside.research-chat textarea").is_visible(),
        "pagination": pagination,
        "pageErrors": errors,
        "horizontalOverflow": page.evaluate(
            "document.documentElement.scrollWidth > document.documentElement.clientWidth"
        ),
    }
    page.screenshot(
        path=".local/final/ui/composite-conversation-reload.png", full_page=True
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    browser.close()
    api.dispose()
