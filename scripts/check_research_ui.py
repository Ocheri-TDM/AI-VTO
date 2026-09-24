"""Chromium validation against real persisted research, no product fixtures."""
import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright, expect


async def main():
    folder=Path('.local/research/ui');folder.mkdir(parents=True,exist_ok=True)
    report=json.loads(Path('.local/research/acceptance/bottles.json').read_text(encoding='utf-8'))
    errors=[];checks=[]
    async with async_playwright() as pw:
        browser=await pw.chromium.launch()
        for width,height in [(1920,1080),(1440,900),(1280,800),(768,1024),(430,932),(390,844)]:
            context=await browser.new_context(viewport={'width':width,'height':height})
            await context.add_init_script('localStorage.setItem("forma-chat-id",'+json.dumps(report['chat_id'])+')')
            page=await context.new_page()
            page.on('pageerror',lambda error:errors.append(str(error)))
            await page.goto('http://127.0.0.1:3000')
            await expect(page.locator('.product-card').first).to_be_visible(timeout=20000)
            assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth'),f'overflow {width}'
            cards=await page.locator('.product-card').all_inner_texts()
            assert all(not any(word in text.casefold() for word in ['gifts','ucontay','oasis','склад','артикул']) for text in cards)
            await page.screenshot(path=str(folder/f'{width}x{height}.png'),full_page=True)
            if width==1440:
                box=page.locator('.product-card input[type=checkbox]').first
                before=await box.is_checked();await box.click()
                await expect(box).to_be_checked(checked=not before)
                await page.wait_for_timeout(700);await page.reload()
                await expect(page.locator('.product-card input[type=checkbox]').first).to_be_checked(checked=not before)
                checks.append('server selection survives reload')
                await page.get_by_role('button',name='Фильтры',exact=True).click()
                await expect(page.get_by_role('dialog')).to_be_visible()
                await page.screenshot(path=str(folder/'filters.png'))
                await page.keyboard.press('Escape')
                await expect(page.get_by_role('dialog')).to_have_count(0)
                checks.append('dynamic facets and Escape')
            if width==390:
                await page.get_by_role('button',name='AI',exact=True).click()
                await expect(page.get_by_role('dialog')).to_be_visible()
                await expect(page.get_by_role('dialog').locator('textarea')).to_be_visible()
                await page.screenshot(path=str(folder/'mobile-chat.png'))
                await page.keyboard.press('Escape')
                checks.append('mobile research chat drawer')
            checks.append(f'{width}x{height}: cards, privacy, no horizontal overflow')
            await context.close()
        await browser.close()
    assert not errors,errors
    (folder/'checks.json').write_text(json.dumps({'checks':checks,'page_errors':errors},ensure_ascii=False,indent=2),encoding='utf-8')
    print(len(checks),'checks passed')


if __name__=='__main__':asyncio.run(main())
