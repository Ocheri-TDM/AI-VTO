"""Opt-in live UI acceptance. Uses real API, PostgreSQL, local LLM and suppliers."""
import asyncio
import json
import os
from pathlib import Path

import httpx
from playwright.async_api import async_playwright, expect


async def main():
    output = Path('.local/stage3/live')
    output.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as pw, httpx.AsyncClient(base_url='http://127.0.0.1:8000', timeout=30) as api:
        browser = await pw.chromium.launch()
        page = await browser.new_page(viewport={'width':1440,'height':900})
        errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        await page.goto(os.getenv('WEB_TEST_URL','http://127.0.0.1:3001'))
        await expect(page.get_by_role('button',name='Новый проект',exact=True)).to_be_enabled()
        await page.get_by_role('button',name='Новый проект',exact=True).click()
        await page.get_by_label('Название проекта').fill('Halyk Tech Gifts')
        await page.get_by_role('button',name='Создать проект',exact=True).click()
        await expect(page.locator('.workspace-heading h1')).to_have_text('Halyk Tech Gifts')
        await page.locator('#initial-query').fill('Нужны темно-синие термокружки, рюкзаки, ежедневники и ручки. Тираж 300.')
        await page.get_by_role('button',name='Начать подбор').click()
        await page.wait_for_function("document.querySelector('#search-query')?.disabled === false",timeout=900000)
        chat=await page.evaluate("localStorage.getItem('forma-chat-id')")
        (output/'chat-id.txt').write_text(chat)
        async def snapshot(label):
            response=await api.get(f'/api/chats/{chat}');response.raise_for_status();data=response.json()
            (output/f'{label}.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
            current=data['current']
            print(label, current['pool_count'], current['result_summary']['visible_count'], len(current['selected_product_ids']),flush=True)
            return current
        initial=await snapshot('01-search')
        assert len(initial['session']['products'])>=2,initial['assistant_message']
        session=initial['search_session_id']
        for index in range(2):
            await page.locator('.studio-main .product-checkbox input').nth(index).check()
            await page.wait_for_function("document.querySelector('#search-query').disabled === false",timeout=30000)
        manual=await snapshot('02-manual-selection');assert len(manual['selected_product_ids'])==2
        async def message(text,label):
            await page.locator('#search-query').fill(text);await page.locator('#search-query').press('Enter')
            await page.wait_for_function("document.querySelector('#search-query').disabled === false",timeout=900000)
            current=await snapshot(label);assert current['search_session_id']==session
            return current
        budget=await message('Убери всё дороже 10000.','03-budget')
        assert budget['active_state']['filters']['budget']['max']==10000
        await page.get_by_role('button',name='Фильтры',exact=True).click()
        await expect(page.get_by_label('До',exact=True)).to_have_value('10000');await page.keyboard.press('Escape')
        backpacks=await message('Оставь только рюкзаки.','04-backpacks')
        assert backpacks['active_state']['filters']['categories']==['backpack']
        bottles=await message('Добавь бутылки.','05-incremental')
        assert 'bottle' in bottles['active_state']['filters']['categories']
        chosen=await message('Выбери 5 лучших.','06-ai-selection')
        assert len(chosen['selected_product_ids'])==min(5,len(chosen['session']['products']))
        await page.locator('.selection-trigger').click()
        await expect(page.locator('.selected-sheet .product-card')).to_have_count(len(chosen['selected_product_ids']))
        await page.screenshot(path=str(output/'selected.png'))
        await page.keyboard.press('Escape');await page.get_by_role('button',name='Отменить',exact=True).click()
        await page.wait_for_function("document.querySelector('#search-query').disabled === false")
        undone=await snapshot('07-undo');assert undone['selected_product_ids']==manual['selected_product_ids']
        await page.reload();await expect(page.locator('.studio-main .product-card').first).to_be_visible()
        restored=await snapshot('08-reload');assert restored['active_state']==undone['active_state']
        await page.wait_for_function("[...document.querySelectorAll('.studio-main .product-card img')].slice(0,2).every(x=>x.complete && x.naturalWidth>0)",timeout=90000)
        await page.screenshot(path=str(output/'desktop.png'))
        await page.set_viewport_size({'width':390,'height':844});await page.screenshot(path=str(output/'mobile.png'),full_page=True)
        await page.get_by_role('button',name='AI',exact=True).click();await expect(page.locator('.chat-sheet')).to_be_visible()
        await page.screenshot(path=str(output/'mobile-chat.png'))
        assert not errors,errors
        print('Live Stage 3 acceptance passed',chat,flush=True)
        await browser.close()


if __name__=='__main__':asyncio.run(main())
