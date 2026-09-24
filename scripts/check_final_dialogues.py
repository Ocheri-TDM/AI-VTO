"""Actual HTTP dialogues against live observations; normal chat must not own crawling."""
import asyncio
import json
import time
from pathlib import Path

from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app


async def main():
    app=create_app(Settings(background_research_enabled=False),[])
    report=[]
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as client:
            for queries in [
                ['Нужны темно-синие товары для IT-конференции, тираж 300 шт','До 10 тысяч',
                 'Более технологичные','Без бутылок','Покажи похожие на второй','Верни предыдущий вариант','Покажи всё найденное'],
                ['Карандаши 300шт','Только деревянные','До 2000']]:
                chat=(await client.post('/api/chats',json={'project_name':'Final live dialogue'})).json()['id']
                for query in queries:
                    started=time.perf_counter()
                    result=await client.post(f'/api/chats/{chat}/messages',json={'message':query})
                    assert result.status_code in (200,202),result.text
                    value=result.json()
                    assert app.state.browser._browser is None
                    if query=='Карандаши 300шт':
                        assert value['intent']['categories']==['pencil']
                        assert all('бутыл' not in p['name'].casefold() for p in value['products'])
                    report.append({'query':query,'categories':value['intent']['categories'],
                        'quantity':value['intent']['quantity'],'action':value.get('action'),
                        'pool_count':value['pool_count'],'matching_count':value['matching_count'],
                        'first_page_count':len(value['products']),'response_ms':round((time.perf_counter()-started)*1000,2),
                        'browser_invoked':False,'research_id':value['id']})
    Path('.local/final/dialogues.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':asyncio.run(main())
