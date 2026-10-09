"""Live deterministic-composite and semantic-fallback acceptance."""

import asyncio
import json
import time
from pathlib import Path

from app.ai.local import OllamaProvider
from app.application.hard_requests import HardRequestService
from app.config import Settings
from app.database.connection import create_database
from app.domain.query_understanding import QueryUnderstandingService
from app.domain.taxonomy import UniversalCategoryResolver

FAST_QUERY = (
    "Сабери 50 набороф из синих зонтоф, чорных дождевеков, "
    "белых наскофф, красных футболак от 250 штук"
)
SEMANTIC_QUERY = (
    "Собери более статусный набор из ручек и блокнотов для руководителей, "
    "но без ощущения дешёвого промо"
)


async def main():
    settings = Settings()
    engine, sessions = create_database(settings.database_url)
    understanding = QueryUnderstandingService(sessions)
    model = OllamaProvider(
        settings.llm_base_url,
        settings.llm_model,
        settings.llm_timeout_seconds,
        settings.llm_structured_retries,
    )
    service = HardRequestService(sessions, None, None, model, understanding)
    started = time.perf_counter()
    fast = await service.parse(FAST_QUERY)
    fast_ms = (time.perf_counter() - started) * 1000
    started = time.perf_counter()
    semantic = await service.parse(SEMANTIC_QUERY)
    semantic_ms = (time.perf_counter() - started) * 1000
    resolver = UniversalCategoryResolver()
    report = {
        "fast": {
            "query": FAST_QUERY,
            "mode": fast.request_mode,
            "target_cases": fast.target_sets,
            "minimum_quantity": fast.quantity.minimum,
            "positions": [
                {
                    "category": position.category_text,
                    "concept": resolver.resolve(position.category_text),
                    "colors": (position.constraints.color.values if position.constraints.color else []),
                }
                for position in fast.positions
            ],
            "confidence": fast.query_understanding["intent_completeness"]["confidence"],
            "query_understanding_ms": round(fast_ms, 2),
            "llm_used": fast.query_understanding["llm_used"],
            "llm_reason": fast.query_understanding["llm_reason"],
        },
        "semantic": {
            "query": SEMANTIC_QUERY,
            "query_understanding_ms": round(semantic_ms, 2),
            "llm_used": semantic.query_understanding["llm_used"],
            "llm_reason": semantic.query_understanding["llm_reason"],
            "llm_ms": semantic.query_understanding["llm_latency_ms"],
            "validation": semantic.query_understanding["llm_validation_result"],
        },
        "browser_invocations": 0,
        "background_jobs": 0,
    }
    output = Path(".local/query-escalation-live.json")
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    await engine.dispose()


asyncio.run(main())
