"""Real Chromium desktop/mobile diagnostics, cards, chat sheet and responsive overflow."""
import asyncio
import json
from pathlib import Path

import httpx
from playwright.async_api import async_playwright, expect


async def main():
    folder=Path('.local/final/ui');folder.mkdir(parents=True,exist_ok=True)
    async with httpx.AsyncClient(base_url='http://127.0.0.1:8000') as client:
        chat=(await client.post('/api/chats',json={'project_name':'Final UI acceptance'})).json()['id']
        result=(await client.post('/api/researches',json={'chat_id':chat,'query':'Мерч для IT конференции 300 шт'})).json()
        assert result['pool_count']>0
    errors=[];checks=[]
    async with async_playwright() as pw:
        browser=await pw.chromium.launch()
        for width,height in [(1920,1080),(1440,900),(1280,800),(768,1024),(430,932),(390,844)]:
            context=await browser.new_context(viewport={'width':width,'height':height})
            await context.add_init_script('localStorage.setItem("forma-chat-id",'+json.dumps(chat)+')')
            page=await context.new_page();page.on('pageerror',lambda error:errors.append(str(error)))
            await page.goto('http://127.0.0.1:3000')
            await expect(page.locator('.product-card').first).to_be_visible()
            assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            text=' '.join(await page.locator('.product-card').all_inner_texts()).casefold()
            assert not any(word in text for word in ['поставщик','количество на складе','sku','gifts.ru','ucontay'])
            if width<768:
                await page.locator('.research-mobile-nav').get_by_role('button',name='AI',exact=True).click()
                await expect(page.locator('dialog')).to_be_visible()
                await page.keyboard.press('Escape')
                await expect(page.locator('dialog')).not_to_be_visible()
            await page.screenshot(path=str(folder/f'{width}.png'),full_page=True)
            checks.append(f'{width}x{height}: cards, no technical fields, no horizontal overflow')
            await context.close()
        page=await browser.new_page(viewport={'width':1440,'height':900})
        await page.goto('http://127.0.0.1:3000/dev/index')
        await expect(page.get_by_role('heading',name='Research Index · Developer')).to_be_visible()
        await expect(page.locator('h2').filter(has_text='gifts').first).to_be_visible()
        await page.screenshot(path=str(folder/'diagnostics.png'),full_page=True)
        await browser.close()
    assert not errors,errors
    (folder/'checks.json').write_text(json.dumps({'checks':checks,'page_errors':errors},ensure_ascii=False,indent=2),encoding='utf-8')
    print(checks)


if __name__=='__main__':asyncio.run(main())
