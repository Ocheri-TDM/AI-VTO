import asyncio
import json
import time
from pathlib import Path
import httpx


async def main():
    path=Path('.local/research/acceptance/conference.json')
    previous=json.loads(path.read_text(encoding='utf-8'))
    async with httpx.AsyncClient(base_url='http://127.0.0.1:8000',timeout=30,trust_env=False) as client:
        started=time.monotonic()
        response=await client.post('/api/researches/'+previous['research_id']+'/continue')
        response.raise_for_status()
        while True:
            await asyncio.sleep(10)
            response=await client.get('/api/researches/'+previous['research_id'])
            response.raise_for_status();value=response.json()
            print(value['status'],value['pool_count'],len({b['category'] for b in value['coverage'] if b['products_seen']}),flush=True)
            if value['status'] not in ('RUNNING','QUEUED'):
                break
        previous.update(pool_count=value['pool_count'],status=value['status'],coverage=value['coverage'],
                        continuation_seconds=round(time.monotonic()-started,1))
        path.write_text(json.dumps(previous,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':asyncio.run(main())
