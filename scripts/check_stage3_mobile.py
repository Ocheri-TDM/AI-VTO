"""Continue a fresh live acceptance session on mobile without a supplier search."""
import asyncio
from pathlib import Path

from playwright.async_api import async_playwright, expect


async def main():
    output=Path('.local/stage3/live')
    chat=(output/'chat-id.txt').read_text().strip()
    async with async_playwright() as pw:
        browser=await pw.chromium.launch()
        page=await browser.new_page(viewport={'width':390,'height':844})
        await page.add_init_script(f"localStorage.setItem('forma-chat-id', '{chat}')")
        await page.goto('http://127.0.0.1:3000')
        await expect(page.locator('.studio-main .product-card').first).to_be_visible()
        await page.get_by_role('button',name='AI',exact=True).click()
        log=page.locator('.chat-sheet .conversation')
        await expect(log).to_be_visible()
        await page.wait_for_function("(()=>{const e=document.querySelector('.chat-sheet .conversation');return e.scrollHeight-e.scrollTop-e.clientHeight<80})()")
        await log.evaluate('(e)=>e.scrollTop=120')
        await page.wait_for_timeout(100)
        await page.keyboard.press('Escape')
        await page.get_by_role('button',name='AI',exact=True).click()
        await page.wait_for_function("Math.abs(document.querySelector('.chat-sheet .conversation').scrollTop-120)<2")
        await page.locator('.chat-sheet #search-query').fill('Покажи сначала самые дешевые')
        await page.locator('.chat-sheet #search-query').press('Enter')
        await page.wait_for_function("document.querySelector('.chat-sheet #search-query').disabled===false",timeout=180000)
        await page.screenshot(path=str(output/'mobile-chat.png'))
        await page.keyboard.press('Escape')
        await expect(page.get_by_label('Сортировка товаров')).to_have_value('price_asc')
        await page.get_by_role('button',name='Фильтры',exact=True).click()
        await page.get_by_label('До',exact=True).fill('5000')
        await page.get_by_role('button',name='Применить фильтры').click()
        await expect(page.get_by_role('button',name='Убрать фильтр: До 5 000 ₸')).to_be_visible()
        await page.get_by_role('button',name='Отменить',exact=True).click()
        await expect(page.get_by_role('button',name='Убрать фильтр: До 10 000 ₸')).to_be_visible()
        await page.reload()
        await expect(page.locator('.selection-trigger')).to_have_text('Выбрано 2')
        await page.screenshot(path=str(output/'mobile.png'),full_page=True)
        print('Live mobile chat, scroll restoration, sort, filters, undo and reload passed')
        await browser.close()


if __name__=='__main__':asyncio.run(main())
