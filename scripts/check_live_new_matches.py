"""Observe actual background refresh while a real HTTP research stays open."""
import asyncio
import json
import time
from pathlib import Path

import httpx


async def main():
    report={'kind':'LIVE HTTP and supplier background worker','observations':[]}
    async with httpx.AsyncClient(base_url='http://127.0.0.1:8000',timeout=30) as client:
        chat=(await client.post('/api/chats',json={'project_name':'Background live acceptance'})).json()['id']
        initial=(await client.post('/api/researches',json={'chat_id':chat,'query':'Мерч для IT конференции 300 шт'})).json()
        identifier=initial['id']
        before=(await client.post(f'/api/researches/{identifier}/messages',json={'message':'До 10000'})).json()
        base_count=before['pool_count'];version=before['view']['version']
        for _ in range(20):
            started=time.perf_counter()
            result=(await client.get(f'/api/researches/{identifier}')).json()
            report['observations'].append({'pool':result['pool_count'],'pending':result['new_matches_count'],
                                           'response_ms':round((time.perf_counter()-started)*1000,2)})
            assert result['view']['version']==version
            if result['new_matches_count']:
                accepted=(await client.post(f'/api/researches/{identifier}/new-matches')).json()
                assert accepted['view']['filters']==before['view']['filters']
                assert accepted['view']['selected_ids']==before['view']['selected_ids']
                assert accepted['pool_count']>=result['pool_count']
                report.update(notification_observed=True,accepted=True,initial_pool=base_count,
                              pending=result['new_matches_count'],accepted_pool=accepted['pool_count'],state_preserved=True)
                break
            await asyncio.sleep(3)
        else:
            report.update(notification_observed=False,accepted=False,reason='No new acceptable match observed in 60-second live window')
    Path('.local/final/live-new-matches.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))


if __name__=='__main__':asyncio.run(main())
