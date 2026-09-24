"""Playwright regression for the Research header's three collision-free zones."""

import asyncio
import json
import uuid
from pathlib import Path

from playwright.async_api import BrowserContext, Page, async_playwright


BASE_URL = "http://127.0.0.1:3000"
DESKTOP_WIDTHS = (1920, 1600, 1440, 1366, 1280, 1024)
MOBILE_WIDTHS = (430, 390, 375)
ZOOMS = (0.8, 0.9, 1.0, 1.1, 1.25, 1.5)


async def prepare(context: BrowserContext, user_name: str, project_name: str) -> Page:
    password = "header-layout-password"
    response = await context.request.post(
        f"{BASE_URL}/api/auth/register",
        data={"name": user_name, "password": password},
    )
    assert response.status == 201, await response.text()
    response = await context.request.post(
        f"{BASE_URL}/api/chats", data={"project_name": project_name}
    )
    assert response.ok, await response.text()
    page = await context.new_page()
    await page.goto(BASE_URL, wait_until="networkidle")
    await page.get_by_test_id("header-brand").wait_for()
    return page


async def measure(page: Page, width: int, zoom: float, mobile: bool) -> dict:
    await page.set_viewport_size({"width": width, "height": 900})
    await page.evaluate("value => { document.documentElement.style.zoom = value; }", zoom)
    await page.wait_for_timeout(50)
    brand = await page.get_by_test_id("header-brand").bounding_box()
    project = await page.get_by_test_id("header-project-title").bounding_box()
    account = await page.get_by_test_id("header-account").bounding_box()
    logout = await page.get_by_test_id("header-logout").bounding_box()
    assert brand and account and logout
    epsilon = 0.5
    separated = (
        project is None and brand["x"] + brand["width"] <= account["x"] + epsilon
        if mobile
        else project is not None
        and brand["x"] + brand["width"] <= project["x"] + epsilon
        and project["x"] + project["width"] <= account["x"] + epsilon
    )
    header_fits = await page.locator(".research-top").evaluate(
        "element => element.scrollWidth <= element.clientWidth + 1"
    )
    result = {
        "separated": separated,
        "headerFits": header_fits,
        "logoutVisible": logout["width"] >= 44,
        "brand": brand,
        "project": project,
        "account": account,
        "logout": logout,
    }
    assert result["separated"], (width, zoom, result)
    assert result["headerFits"], (width, zoom, result)
    assert result["logoutVisible"], (width, zoom, result)
    return {"width": width, "zoom": zoom, "mobile": mobile, **result}


async def main() -> None:
    results = []
    suffix = uuid.uuid4().hex[:10]
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        normal = await browser.new_context(viewport={"width": 1440, "height": 900})
        page = await prepare(normal, f"header-{suffix}", "Обычный проект")
        for width in DESKTOP_WIDTHS:
            results.append(await measure(page, width, 1.0, False))
        await normal.close()

        long_values = await browser.new_context(viewport={"width": 1440, "height": 900})
        page = await prepare(
            long_values,
            f"Очень длинное имя пользователя для проверки безопасного усечения {suffix} " * 2,
            "Очень длинное название проекта для проверки многократного безопасного усечения " * 2,
        )
        for width in DESKTOP_WIDTHS:
            for zoom in ZOOMS:
                results.append(await measure(page, width, zoom, False))
        for width in MOBILE_WIDTHS:
            for zoom in ZOOMS:
                results.append(await measure(page, width, zoom, True))
        await long_values.close()
        await browser.close()

    output = Path(".local/final/header-layout.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"header layout: {len(results)} cases passed; {output}")


if __name__ == "__main__":
    asyncio.run(main())
