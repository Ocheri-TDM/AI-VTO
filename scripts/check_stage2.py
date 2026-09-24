"""Opt-in real dialogue against local API; never substitutes supplier data."""
import asyncio
import json
from pathlib import Path

import httpx

MESSAGES = [
    'Нужны темно-синие термокружки, рюкзаки, ежедневники и ручки. Тираж 300.',
    'Убери всё дороже 10 тысяч.', 'Оставь только рюкзаки.',
    'Добавь бутылки.', 'Выбери 5 лучших.', 'Верни предыдущий вариант.',
]


async def main():
    output = Path('.local/stage2')
    output.mkdir(exist_ok=True)
    async with httpx.AsyncClient(base_url='http://127.0.0.1:8000', timeout=900) as client:
        r = await client.post('/api/chats', json={'project_name': 'Проверка этапа 2'})
        r.raise_for_status()
        chat = r.json()['id']
        (output / 'chat-id.txt').write_text(chat)
        session_id = None
        for index, message in enumerate(MESSAGES):
            async with client.stream('POST', f'/api/chats/{chat}/messages/stream', json={'message': message}) as r:
                r.raise_for_status()
                event = ''
                result = None
                async for line in r.aiter_lines():
                    if line.startswith('event: '):
                        event = line[7:]
                    if line.startswith('data: '):
                        data = json.loads(line[6:])
                        if event in ('agent.completed', 'agent.error'):
                            result = data
                        elif event in ('tool.started', 'supplier.started', 'supplier.completed'):
                            print(index, event, data, flush=True)
            assert result is not None
            (output / f'message-{index}.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf8')
            expected = ['SEARCH', 'FILTER_RESULTS', 'FILTER_RESULTS', 'EXPAND_RESULTS', 'SELECT_PRODUCTS', 'UNDO']
            assert result['action'] == expected[index], result['assistant_message']
            if index == 0:
                session_id = result['search_session_id']
            assert result['search_session_id'] == session_id
            if index == 1:
                assert all(p['price_kzt'] <= 10000 for p in result['session']['products'])
            if index == 2:
                assert all(p['category'] == 'backpack' for p in result['session']['products'])
            if index == 3:
                assert 'bottle' in result['session']['intent']['categories']
            if index == 4:
                assert len(result['selected_product_ids']) == min(5, len(result['session']['products']))
            if index == 5:
                assert not result['selected_product_ids']
            for p in result['session']['products']:
                assert isinstance(p['price_kzt'], int)
                assert not any(key in p for key in ['supplier', 'stock_quantity', 'metadata'])
            print(index, result['action'], result['result_summary']['visible_count'], len(result['selected_product_ids']), flush=True)
        print('CHAT', chat, flush=True)


if __name__ == '__main__':
    asyncio.run(main())
