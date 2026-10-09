"""Live Playwright acceptance for one active workspace per project."""

import json
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:3001"
COMPOSITE = "Сабери 50 набороф из синих зонтоф, чорных дождевеков, белых наскофф, красных футболак от 250 штук"
SIMPLE = "карандаш"


def submit(page, value):
    composer = page.locator(".research-composer textarea").last
    composer.fill(value)
    composer.press("Enter")


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(BASE, wait_until="networkidle")
    stamp = int(time.time())
    page.get_by_role("button", name="Нет аккаунта? Создать").click()
    page.get_by_label("Имя").fill(f"workspace-parity-{stamp}")
    page.get_by_label("Пароль", exact=True).fill("Workspace-parity-2026")
    page.get_by_label("Повторите пароль").fill("Workspace-parity-2026")
    page.get_by_role("button", name="Создать аккаунт", exact=True).click()
    page.locator(".research-composer textarea").last.wait_for(timeout=30_000)

    submit(page, COMPOSITE)
    page.locator('[data-testid="hard-request"]').wait_for(timeout=120_000)
    composite_then = page.locator('[data-testid="hard-request"]').count()

    submit(page, SIMPLE)
    page.locator('[data-testid="hard-request"]').wait_for(state="detached", timeout=10_000)
    page.locator(".research-heading").wait_for(timeout=120_000)
    simple_only = {
        "hard_views": page.locator('[data-testid="hard-request"]').count(),
        "simple_views": page.locator(".research-heading").count(),
        "title": page.locator(".research-heading h1").text_content(),
    }
    page.get_by_role("button", name="История").click()
    history_has_composite = page.get_by_text(COMPOSITE, exact=True).count() > 0
    history_has_simple = page.get_by_text(SIMPLE, exact=True).count() > 0
    page.get_by_label("Закрыть").click()
    page.reload(wait_until="networkidle")
    page.locator(".research-heading").wait_for(timeout=60_000)
    reload_simple = {
        "hard_views": page.locator('[data-testid="hard-request"]').count(),
        "simple_views": page.locator(".research-heading").count(),
    }

    submit(page, COMPOSITE)
    page.locator(".research-heading").wait_for(state="detached", timeout=10_000)
    page.locator('[data-testid="hard-request"]').wait_for(timeout=120_000)
    composite_only = {
        "hard_views": page.locator('[data-testid="hard-request"]').count(),
        "simple_views": page.locator(".research-heading").count(),
    }
    page.reload(wait_until="networkidle")
    page.locator('[data-testid="hard-request"]').wait_for(timeout=60_000)
    reload_composite = {
        "hard_views": page.locator('[data-testid="hard-request"]').count(),
        "simple_views": page.locator(".research-heading").count(),
    }
    result = {
        "initial_composite_views": composite_then,
        "simple_only": simple_only,
        "history_has_composite": history_has_composite,
        "history_has_simple": history_has_simple,
        "reload_simple": reload_simple,
        "composite_only": composite_only,
        "reload_composite": reload_composite,
        "page_errors": errors,
    }
    assert simple_only["hard_views"] == 0 and simple_only["simple_views"] == 1
    assert history_has_composite and history_has_simple
    assert reload_simple == {"hard_views": 0, "simple_views": 1}
    assert composite_only == {"hard_views": 1, "simple_views": 0}
    assert reload_composite == {"hard_views": 1, "simple_views": 0}
    assert not errors
    Path(".local/active-workspace-ui.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    page.screenshot(path=".local/final/ui/active-workspace.png", full_page=True)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    browser.close()
