import asyncio
from pathlib import Path

import httpx
from playwright.async_api import async_playwright


async def main():
    output = Path('.local/stage2')
    chat = (output / 'chat-id.txt').read_text().strip()
    async with httpx.AsyncClient() as client:
        response = await client.get(f'http://127.0.0.1:8000/api/chats/{chat}')
        response.raise_for_status()
        if not response.json().get('search_session_id'):
            raise SystemExit('Acceptance session expired. Run scripts/check_stage2.py before the UI check.')
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page(viewport={'width': 1440, 'height': 1000})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        await page.add_init_script(f"localStorage.setItem('forma-chat-id', '{chat}')")
        await page.goto('http://127.0.0.1:3000')
        await page.locator('.product-card').first.wait_for(timeout=30000)
        assert await page.get_by_role('log').count() == 1
        await page.locator('#search-query').fill('Покажи сначала самые дешевые')
        await page.get_by_role('button', name='Отправить сообщение').click()
        await page.get_by_role('button', name='Отправить сообщение').wait_for(state='visible')
        await page.wait_for_function("document.querySelector('#search-query').disabled === false")
        await page.wait_for_function("[...document.querySelectorAll('.product-card img')].slice(0,2).every(x=>x.complete && x.naturalWidth > 0)", timeout=60000)
        await page.screenshot(path=str(output / 'desktop.png'), full_page=True)
        await page.set_viewport_size({'width': 390, 'height': 844})
        await page.screenshot(path=str(output / 'mobile.png'), full_page=True)
        assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert not errors, errors
        print('Desktop/mobile chat checked, no page errors')
        await browser.close()


if __name__ == '__main__':
    asyncio.run(main())
