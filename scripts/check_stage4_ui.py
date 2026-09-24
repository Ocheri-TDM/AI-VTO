import asyncio
import json
from pathlib import Path
import httpx
from playwright.async_api import async_playwright, expect


async def main():
    async with httpx.AsyncClient(base_url='http://127.0.0.1:8000') as client:
        chat=(await client.post('/api/chats',json={'project_name':'Stage 4 — карандаши'})).json()['id']
        result=(await client.post('/api/researches',json={'chat_id':chat,'query':'Карандаши 300шт'})).json()
        assert result['intent']['categories']==['pencil'] and result['pool_count']>0
    folder=Path('.local/stage4/ui');folder.mkdir(parents=True,exist_ok=True)
    checks=[];errors=[]
    async with async_playwright() as pw:
        browser=await pw.chromium.launch()
        for width,height in [(1440,900),(390,844)]:
            context=await browser.new_context(viewport={'width':width,'height':height})
            await context.add_init_script('localStorage.setItem("forma-chat-id",'+json.dumps(chat)+')')
            page=await context.new_page()
            page.on('pageerror',lambda e:errors.append(str(e)))
            await page.goto('http://127.0.0.1:3000')
            await expect(page.locator('.product-card').first).to_be_visible()
            assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            titles=await page.locator('.product-card').all_inner_texts()
            assert all('бутылк' not in title.casefold() for title in titles)
            await page.screenshot(path=str(folder/f'{width}.png'),full_page=True)
            if width==1440:
                composer=page.locator('.research-chat textarea')
                await composer.fill('Только деревянные')
                await composer.press('Enter')
                await expect(page.locator('.research-heading')).to_contain_text('дерев')
                await composer.fill('До 2000 тенге')
                await composer.press('Enter')
                await expect(page.locator('.research-heading')).to_contain_text('2 000')
                await page.reload()
                await expect(page.locator('.research-heading')).to_contain_text('2 000')
                checks.append('local wood/price refinement and reload')
            checks.append(f'{width}: pencil cards, no bottle leakage, no overflow')
            await context.close()
        await browser.close()
    assert not errors,errors
    (folder/'checks.json').write_text(json.dumps({'checks':checks,'page_errors':errors},ensure_ascii=False,indent=2),encoding='utf-8')
    print(checks)


if __name__=='__main__':asyncio.run(main())
