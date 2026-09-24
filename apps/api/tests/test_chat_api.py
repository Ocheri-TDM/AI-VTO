import json
from datetime import timedelta

from conftest import FakeProvider
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.database.models import AgentExecution, Base, Chat, SearchSession
from app.domain.models import utcnow
from app.main import create_app


async def test_persistent_chat_sse_selection_expiry_and_debug(settings):
    provider = FakeProvider()
    app = create_app(settings, [provider])
    async with app.state.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            chat = (await client.post('/api/chats', json={})).json()['id']
            r = await client.post(f'/api/chats/{chat}/messages/stream?legacy=true', json={'message': 'Термокружки navy, тираж 300'})
            assert r.status_code == 200
            assert 'event: agent.started' in r.text and 'event: agent.completed' in r.text
            blocks = r.text.split('\n\n')
            data = json.loads(next(b for b in blocks if 'event: agent.completed' in b).split('data: ')[1])
            assert 'debug' not in data and len(data['session']['products']) == 1
            product = data['session']['products'][0]
            assert not {'supplier', 'stock_quantity', 'metadata'}.intersection(product)
            selection = (await client.post(f'/api/chats/{chat}/selection', json={'product_ids': [product['id']]})).json()
            assert selection['selected_product_ids'] == [product['id']]
            restored = (await client.get(f'/api/chats/{chat}')).json()
            assert restored['current']['selected_product_ids'] == [product['id']]
            assert len(restored['messages']) == 4
            assert provider.calls == 1
            async with app.state.conversations.sessions.begin() as db:
                record = await db.get(SearchSession, data['search_session_id'])
                record.expires_at = utcnow() - timedelta(seconds=1)
            expired = (await client.post(f'/api/chats/{chat}/messages?legacy=true', json={'message': 'Оставь до 10000'})).json()
            assert expired['action'] == 'ASK_CLARIFICATION'
            assert provider.calls == 1
            assert (await client.post(f'/api/chats/{chat}/messages', json={'message': ' '})).status_code == 422
            assert (await client.post(f'/api/chats/{chat}/selection', json={'product_ids': [], 'shell': 'x'})).status_code == 422
            async with app.state.conversations.sessions() as db:
                executions = (await db.scalars(select(AgentExecution))).all()
                assert len(executions) == 3 and all(e.status == 'completed' for e in executions)
                assert (await db.get(Chat, chat)).last_intent
