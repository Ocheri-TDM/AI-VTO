"""Measure the deterministic query-understanding fast path on the live catalog vocabulary."""

import asyncio
import json
import statistics
import time
from collections import Counter
from pathlib import Path

from app.config import Settings
from app.database.connection import create_database
from app.domain.query_understanding import QueryUnderstandingService

QUERIES = [
    "корондаш", "карондаш", "карандаж", "карандашы", "крандаш",
    "ручька", "рючка", "крушка", "кружек", "зонтик", "зонтоф",
    "рюкзаг", "рюкзкоф", "фудболка", "футболак", "насоки", "наскофф",
    "даждевик", "дождевек", "синнии карандаши", "чорные зонты",
    "крассные кружки", "karandash", "ryukzak", "zont", "термо кружка",
    "ручка Parker Jotter синяя", "Blackie 3D", "Powerbank", "USB-C",
]


async def main():
    engine, sessions = create_database(Settings().database_url)
    service = QueryUnderstandingService(sessions)
    cold_started = time.perf_counter()
    await service.understand("корондаш")
    cold_ms = (time.perf_counter() - cold_started) * 1000
    timings = []
    resolvers = Counter()
    results = []
    for index in range(10):
        for query in QUERIES:
            started = time.perf_counter()
            value = await service.understand(query)
            timings.append((time.perf_counter() - started) * 1000)
            resolvers[value.resolver] += 1
            if index == 0:
                results.append(value.model_dump(mode="json"))
    ordered = sorted(timings)
    report = {
        "queries": len(timings),
        "unique_queries": len(QUERIES),
        "cold_ms": round(cold_ms, 2),
        "p50_ms": round(statistics.median(ordered), 2),
        "p95_ms": round(ordered[int(len(ordered) * 0.95) - 1], 2),
        "max_ms": round(max(ordered), 2),
        "resolver_counts": dict(resolvers),
        "llm_fallback": 0,
        "browser_invoked": False,
        "results": results,
    }
    Path(".local/query-understanding-benchmark.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    await engine.dispose()


asyncio.run(main())
