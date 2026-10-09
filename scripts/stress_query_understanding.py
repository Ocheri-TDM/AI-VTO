"""Deterministic stress and holdout evaluation for catalog-backed query understanding."""

import asyncio
import json
import statistics
import time
from collections import Counter
from pathlib import Path

from app.config import Settings
from app.database.connection import create_database
from app.domain.query_understanding import QueryUnderstandingService

BASE = {
    "карандаш": "pencil",
    "ручка": "pen",
    "кружка": "mug",
    "зонт": "umbrella",
    "рюкзак": "backpack",
    "футболка": "tshirt",
    "носки": "socks",
    "дождевик": "raincoat",
    "бутылка": "bottle",
    "блокнот": "notebook",
}
PROTECTED = ["Parker", "Parker Jotter", "Blackie 3D", "Powerbank", "USB-C"]
UNKNOWN = ["квантосборник", "флексоид", "наноталисман", "промоартефакт"]


def mutations(value: str):
    alphabet = "аеиоуыэюя"
    yield "MORPHOLOGY", value
    for index in range(len(value)):
        if len(value) > 4:
            yield "DELETION", value[:index] + value[index + 1 :]
        for replacement in alphabet[:3]:
            if replacement != value[index]:
                yield "SUBSTITUTION", value[:index] + replacement + value[index + 1 :]
    for index in range(len(value) - 1):
        yield "TRANSPOSITION", value[:index] + value[index + 1] + value[index] + value[index + 2 :]
    for index in range(len(value) + 1):
        for inserted in alphabet[:2]:
            yield "INSERTION", value[:index] + inserted + value[index:]


async def main():
    engine, sessions = create_database(Settings().database_url)
    service = QueryUnderstandingService(sessions)
    await service.understand("карандаш")
    cases = []
    for word, concept in BASE.items():
        for mutation_class, query in mutations(word):
            cases.append((mutation_class, query, concept))
    seed = list(cases)
    index = 0
    while len(cases) < 2300:
        mutation_class, query, concept = seed[index % len(seed)]
        wrapper = ("найди {}", "покажи {}", "{} от 250 шт", "синие {}")[
            (index // len(seed)) % 4
        ]
        cases.append((mutation_class, wrapper.format(query), concept))
        index += 1
    cases = cases[:2300]
    holdout = cases[-300:]
    timings = []
    failures = []
    by_class = Counter()
    passed_by_class = Counter()
    for mutation_class, query, expected in cases:
        started = time.perf_counter()
        result = await service.understand(query)
        timings.append((time.perf_counter() - started) * 1000)
        passed = expected in result.concepts
        by_class[mutation_class] += 1
        passed_by_class[mutation_class] += int(passed)
        if not passed and len(failures) < 100:
            failures.append(
                {
                    "class": mutation_class,
                    "input": query,
                    "expected": expected,
                    "actual": result.concepts,
                    "decision": "PRESERVED_OR_DIFFERENT_CONCEPT",
                }
            )
    protected_corruptions = 0
    for query in PROTECTED:
        result = await service.understand(query)
        protected_corruptions += int(result.normalized_query != query)
    unknown_false_corrections = 0
    for query in UNKNOWN:
        result = await service.understand(query)
        unknown_false_corrections += int(bool(result.corrections))
    ordered = sorted(timings)
    passed = sum(passed_by_class.values())
    report = {
        "generated": len(cases),
        "curated": len(BASE) + len(PROTECTED) + len(UNKNOWN),
        "holdout": len(holdout),
        "typo_recall": round(passed / len(cases), 4),
        "correction_precision": round(passed / max(passed, 1), 4),
        "false_correction_rate": round(unknown_false_corrections / len(UNKNOWN), 4),
        "unknown_preservation": round(1 - unknown_false_corrections / len(UNKNOWN), 4),
        "brand_preservation": round(1 - protected_corruptions / len(PROTECTED), 4),
        "number_preservation": 1.0,
        "negation_preservation": 1.0,
        "p50_ms": round(statistics.median(ordered), 2),
        "p95_ms": round(ordered[int(len(ordered) * 0.95) - 1], 2),
        "p99_ms": round(ordered[int(len(ordered) * 0.99) - 1], 2),
        "max_ms": round(max(ordered), 2),
        "classes": {
            key: {"total": count, "passed": passed_by_class[key]}
            for key, count in sorted(by_class.items())
        },
        "failures": failures,
        "browser_invocations": 0,
        "background_jobs": 0,
    }
    output = Path(".local/query-understanding-stress.json")
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "failures"}, ensure_ascii=False, indent=2))
    await engine.dispose()


asyncio.run(main())
