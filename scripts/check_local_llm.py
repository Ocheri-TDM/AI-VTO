"""Read-only structured-output check; no supplier products are fabricated."""
import asyncio
import json
from pathlib import Path

from app.ai.local import OllamaProvider, OpenAICompatibleProvider


async def main():
    prompt = 'Нужны темно-синие термокружки, рюкзаки, ежедневники и ручки. Тираж 300.'
    results = []
    for cls in (OllamaProvider, OpenAICompatibleProvider):
        provider = cls('http://127.0.0.1:11434', 'qwen3:4b', timeout=120)
        decision = await provider.decide({'session': None, 'recent_messages': []}, prompt)
        print(cls.__name__, decision.model_dump_json(), flush=True)
        assert decision.action == 'SEARCH'
        assert decision.intent.quantity == 300
        assert set(decision.intent.categories) == {'thermomug', 'backpack', 'notebook', 'pen'}
        results.append({'adapter': cls.__name__, 'decision': decision.model_dump(mode='json')})
    Path('.local/stage2/llm-check.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf8')


if __name__ == '__main__':
    asyncio.run(main())
