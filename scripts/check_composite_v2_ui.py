"""Verify persisted Composite V2 Case View through the real frontend and F5."""

import json
from pathlib import Path

from playwright.sync_api import sync_playwright

live = json.loads(Path(".local/composite-assembly-live.json").read_text(encoding="utf-8"))
scenario = live["scenarios"]["dense_blue_20"]

with sync_playwright() as playwright:
    api = playwright.request.new_context(base_url="http://127.0.0.1:8000")
    login = api.post(
        "/api/auth/login",
        data={"name": live["user"], "password": "Composite-pass-2026"},
    )
    assert login.ok, login.text()
    active = api.get(f"/api/projects/{scenario['project_id']}/active-composite").json()
    current_cases = api.get(f"/api/hard-requests/{active['id']}/cases?offset=0&limit=20").json()
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context(storage_state=api.storage_state(), viewport={"width": 1440, "height": 900})
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto("http://127.0.0.1:3000", wait_until="networkidle")
    stamp = live["user"].removeprefix("composite-v2-")
    page.get_by_text(f"Composite V2 dense_blue_20 {stamp}", exact=True).first.click()
    page.get_by_test_id("hard-request").wait_for()
    page.get_by_test_id("hard-request").locator(".hard-case").first.wait_for()
    before = page.get_by_test_id("hard-request").locator(".hard-case").count()
    page.reload(wait_until="networkidle")
    page.get_by_test_id("hard-request").wait_for()
    page.get_by_test_id("hard-request").locator(".hard-case").first.wait_for()
    after = page.get_by_test_id("hard-request").locator(".hard-case").count()
    result = {
        "request_id": scenario["request_id"],
        "initial_total": scenario["case_total"],
        "active_request_id": active["id"],
        "active_revision": active["revision"],
        "current_total": current_cases["total"],
        "visible_before_reload": before,
        "visible_after_reload": after,
        "same_active_workspace_after_f5": before == after,
        "page_errors": errors,
        "horizontal_overflow": page.evaluate(
            "document.documentElement.scrollWidth > document.documentElement.clientWidth"
        ),
    }
    Path(".local/composite-ui-e2e.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    page.screenshot(path=".local/composite-v2-ui.png", full_page=True)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    browser.close()
    api.dispose()
