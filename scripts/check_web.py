"""Browser smoke test against the running real application, reusing its search cache."""
import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]


async def main():
    output = ROOT / ".local/web-check"
    output.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page(viewport={"width": 1440, "height": 1000}, device_scale_factor=1)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        await page.goto("http://127.0.0.1:3000", wait_until="networkidle")
        await page.screenshot(path=str(output / "desktop-empty.png"), full_page=True)
        assert await page.locator("h1").count() == 1
        await page.get_by_role("button", name="Найти товары", exact=True).click()
        await page.locator(".product-card").first.wait_for(timeout=300000)
        await page.get_by_role("button", name="Применить фильтр").wait_for()
        await page.screenshot(path=str(output / "desktop-results.png"), full_page=True)
        count = await page.locator(".product-card").count()
        await page.locator(".product-card input[type='checkbox']").first.check()
        await page.get_by_role("tab", name="Выбранное").click()
        assert await page.locator(".product-card").count() == 1
        await page.locator(".product-name").first.click()
        await page.locator(".source-link").wait_for()
        assert await page.get_by_role("dialog").is_visible()
        await page.screenshot(path=str(output / "details.png"), full_page=True)
        await page.keyboard.press("Escape")
        assert not await page.get_by_role("dialog").count()
        await page.get_by_role("tab", name="Все варианты").click()
        await page.locator("#refine-query").fill("убери всё дороже 10000")
        await page.get_by_role("button", name="Применить фильтр").click()
        await page.locator(".filter-confirmation").wait_for()
        await page.set_viewport_size({"width": 390, "height": 844})
        await page.screenshot(path=str(output / "mobile-results.png"), full_page=True)
        assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "Mobile horizontal overflow"
        await page.get_by_role("button", name="Новая подборка", exact=True).last.click()
        assert await page.locator("#search-query").is_visible()
        await page.screenshot(path=str(output / "mobile-empty.png"), full_page=True)
        await browser.close()
        assert not errors, errors
        print(json.dumps({"product_cards": count, "desktop_mobile": "passed", "selection_details_filter": "passed", "page_errors": errors}))


if __name__ == "__main__":
    asyncio.run(main())
