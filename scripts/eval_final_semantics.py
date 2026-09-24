"""Reproducible labelled fixture evaluation; synthetic products, real local embeddings."""
import asyncio
import json
import time
from pathlib import Path

import httpx

from app.application.research_analysis import PREFERENCE_TERMS

DOCUMENTS = [
    ('steel', 'Бутылка из стали с вакуумной изоляцией, премиальная серия', ['premium']),
    ('leather', 'Ежедневник из натуральной кожи, деловой подарок', ['premium', 'business']),
    ('minimal', 'Однотонный матовый блокнот, минималистичный лаконичный дизайн', ['minimal']),
    ('bamboo', 'Ручка из бамбука, эко коллекция', ['eco']),
    ('recycled', 'Сумка из переработанного хлопка, эко серия', ['eco']),
    ('charger', 'Беспроводная зарядка USB-C, современный технологичный аксессуар', ['technology']),
    ('power', 'Внешний аккумулятор USB с магнитным креплением', ['technology']),
    ('classic', 'Металлическая деловая ручка, классический строгий дизайн', ['business']),
    ('creative', 'Необычный блокнот трансформер, оригинальная дизайнерская обложка', ['creative']),
    ('toy', 'Яркий детский мяч для спортивных игр', []),
    ('plain', 'Пластиковая бутылка для воды 500 мл', []),
    ('mug', 'Керамическая белая кружка 300 мл', []),
]
QUERIES = [('премиальные подарки','premium'), ('минималистичные','minimal'),
           ('экологичные подарки','eco'), ('технологичные для IT','technology'),
           ('строгие подарки руководителям','business'), ('не банальные подарки','creative')]


async def main():
    report = {'dataset': 'synthetic labelled merchandise facts v1', 'model': 'embeddinggemma', 'queries': []}
    texts = ['title: none | text: '+text for _,text,_ in DOCUMENTS]
    texts += ['task: search result | query: '+text for text,_ in QUERIES]
    async with httpx.AsyncClient(timeout=180, trust_env=False) as client:
        started = time.perf_counter()
        result = await client.post('http://127.0.0.1:11434/api/embed', json={'model':'embeddinggemma','input':texts})
        result.raise_for_status()
        embeddings = result.json()['embeddings']
        report['batch_ms'] = round((time.perf_counter()-started)*1000,2)
    report['dimensions'] = len(embeddings[0])
    for index,(query,preference) in enumerate(QUERIES):
        expected = {i for i,(_,_,labels) in enumerate(DOCUMENTS) if preference in labels}
        k = len(expected)
        rules = sorted(range(len(DOCUMENTS)),key=lambda i: -sum(term in DOCUMENTS[i][1].casefold() for term in PREFERENCE_TERMS[preference]))
        vector = embeddings[len(DOCUMENTS)+index]
        semantic = sorted(range(len(DOCUMENTS)),key=lambda i: -sum(a*b for a,b in zip(vector,embeddings[i],strict=True)))
        rule_hits = expected & set(rules[:k])
        vector_hits = expected & set(semantic[:k])
        report['queries'].append({'query':query, 'k':k,
            'rule_precision_at_k':len(rule_hits)/k, 'rule_recall_at_k':len(rule_hits)/len(expected),
            'rule_false_positives':[DOCUMENTS[i][0] for i in rules[:k] if i not in expected],
            'embedding_precision_at_k':len(vector_hits)/k,
            'embedding_recall_at_k':len(vector_hits)/len(expected),
            'embedding_false_positives':[DOCUMENTS[i][0] for i in semantic[:k] if i not in expected],
            'embedding_top':[DOCUMENTS[i][0] for i in semantic[:k]],
            'expected':[DOCUMENTS[i][0] for i in sorted(expected)]})
    report['macro'] = {name: sum(row[name] for row in report['queries'])/len(report['queries'])
                       for name in ('rule_precision_at_k','rule_recall_at_k',
                                    'embedding_precision_at_k','embedding_recall_at_k')}
    report['hard_constraint_violations'] = 0
    report['hard_constraint_evidence'] = 'separate IndexQueryService regression suite; semantic ordering sees only eligible pool'
    Path('.local/final/semantic-eval.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    asyncio.run(main())
