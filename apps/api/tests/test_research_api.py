from httpx import ASGITransport, AsyncClient
from test_research import ResearchProvider

from app.database.models import Base
from app.main import create_app


async def test_research_api_persistence_conflict_chat_sse_and_privacy(settings):
    app = create_app(settings, [ResearchProvider()])
    async with app.state.engine.begin() as db:
        await db.run_sync(Base.metadata.create_all)
    provider = ResearchProvider()
    from app.domain.research import ResearchCoverage
    branch = ResearchCoverage(supplier='gifts',category='bottle',query='бутылка')
    for number in range(2):
        branch.cursor.page_number = number
        page = await provider.research_listing(branch)
        for url in page.urls:
            await app.state.index.ingest(await provider.research_products(url))
    async def immediate(call, supplier=None):
        return await call()
    app.state.research._request = immediate
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as client:
            chat = (await client.post('/api/chats',json={'project_name':'Research'})).json()['id']
            started = await client.post('/api/researches',json={'chat_id':chat,'query':'Синие бутылки, 300 шт.',
                                                              'budget':{'max_query_expansions':1}})
            assert started.status_code == 202
            identifier = started.json()['id']
            base = '/api/researches/'+identifier
            result = (await client.get(base)).json()
            assert len(result['products']) == 50 and result['matching_count'] == 60
            tail = (await client.get(base+'/products', params={'cursor': result['next_cursor']})).json()
            assert len(tail['products']) == 10 and tail['next_cursor'] is None
            assert len({p['id'] for p in result['products'] + tail['products']}) == 60
            assert not {'supplier','stock_quantity','metadata','source_url'} & result['products'][0].keys()
            selected = [result['products'][0]['id']]
            result = (await client.patch(base+'/view',json={'version':0,'selected_ids':selected})).json()
            assert result['selected_products'][0]['id'] == selected[0]
            assert (await client.patch(base+'/view',json={'version':0,'selected_ids':[]})).status_code == 409
            assert (await client.patch(base+'/view',json={'version':1,'selected_ids':['fake']})).status_code == 422
            result = (await client.post(base+'/messages',json={'message':'Убери всё дороже 100'})).json()
            assert result['products']==[] and len(result['selected_products'])==1
            result = (await client.post(base+'/messages',json={'message':'Верни предыдущий вариант'})).json()
            assert len(result['products'])==50 and result['matching_count']==60
            latest = (await client.get('/api/researches/chat/'+chat)).json()
            assert latest['view']['selected_ids']==selected
            assert 'research.updated' in (await client.get(base+'/events')).text
            history = (await client.get('/api/chats/'+chat)).json()
            assert any(m['content']=='Убери всё дороже 100' for m in history['messages'])
