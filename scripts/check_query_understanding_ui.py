"""Verify typo results and interpretation survive a browser reload."""

import json
from pathlib import Path

from playwright.sync_api import sync_playwright

source = json.loads(Path(".local/query-understanding-live.json").read_text(encoding="utf-8"))
with sync_playwright() as playwright:
    api = playwright.request.new_context(base_url="http://127.0.0.1:8000")
    response = api.post(
        "/api/auth/login",
        data={"name": source["user"], "password": source["password"]},
    )
    assert response.ok, response.text()
    projects = api.get("/api/projects").json()
    project = next(
        item for item in projects if item["id"] == source["simple"]["query"]["project_id"]
    )
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context(storage_state=api.storage_state(), viewport={"width": 1440, "height": 900})
    context.add_init_script(
        f"localStorage.setItem('forma-chat-id', {json.dumps(project['chat_id'])})"
    )
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto("http://127.0.0.1:3000", wait_until="networkidle")
    page.locator(".research-heading").wait_for(timeout=60000)
    before_title = page.locator(".research-heading h1").text_content()
    before_cards = page.locator(".research-grid .product-card").count()
    page.reload(wait_until="networkidle")
    page.locator(".research-heading").wait_for()
    result = {
        "title_before": before_title,
        "title_after": page.locator(".research-heading h1").text_content(),
        "cards_before": before_cards,
        "cards_after": page.locator(".research-grid .product-card").count(),
        "original_query_visible": page.get_by_text("корондаш", exact=True).count() > 0,
        "page_errors": errors,
        "horizontal_overflow": page.evaluate(
            "document.documentElement.scrollWidth > document.documentElement.clientWidth"
        ),
    }
    Path(".local/query-understanding-ui.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    page.screenshot(path=".local/final/ui/query-understanding-reload.png", full_page=True)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    browser.close()
    api.dispose()
