"""HTTP acceptance against real indexed observations, with browser disabled by construction."""
import asyncio
import json
import time
from pathlib import Path

from httpx import ASGITransport, AsyncClient
from app.config import Settings
from app.main import create_app


async def main():
    app=create_app(Settings(background_research_enabled=False),[])
    checks=[]
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as client:
            chat=(await client.post('/api/chats',json={'project_name':'Stage 4 live index acceptance'})).json()['id']
            for query in ['Карандаши 300шт','Только деревянные','До 2000 тенге','Ручки 300шт',
                          'Синие бутылки 300шт','Синий мерч для IT конференции 300шт']:
                start=time.perf_counter()
                response=await client.post(f'/api/chats/{chat}/messages',json={'message':query})
                value=response.json()
                assert response.status_code in (200,202),value
                assert app.state.browser._browser is None
                checks.append({'query':query,'response_ms':round((time.perf_counter()-start)*1000,2),
                    'research_id':value['id'],'categories':value['intent']['categories'],
                    'pool_count':value['pool_count'],'visible_count':len(value['products']),
                    'timings':value['timings'],'browser_invoked':False})
    path=Path('.local/stage4/api.json')
    path.write_text(json.dumps(checks,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(checks,ensure_ascii=False))


if __name__=='__main__':asyncio.run(main())
