"""Repeatable warm Composite candidate benchmark on the configured PostgreSQL."""

import asyncio
import json
import math
import statistics
import sys
import time
from pathlib import Path

from sqlalchemy import event

sys.path.insert(0, "apps/api")
from app.main import create_app

POSITIONS = ["\u0437\u043e\u043d\u0442\u044b", "\u043a\u0430\u0440\u0430\u043d\u0434\u0430\u0448\u0438", "\u0440\u0443\u0447\u043a\u0438", "\u043a\u0440\u0443\u0436\u043a\u0438"]
BLUE = ["BLUE", "NAVY", "DARK_BLUE", "LIGHT_BLUE", "ROYAL_BLUE", "BLUE_GREY"]


async def main():
    app = create_app()
    statements = 0

    @event.listens_for(app.state.engine.sync_engine, "before_cursor_execute")
    def count_statement(*_args):
        nonlocal statements
        statements += 1

    intents = []
    for position in POSITIONS:
        intent = await app.state.research.planner.parse(position)
        intent.quantity = None
        intents.append(intent)

    async def execute():
        raw_ids = await asyncio.gather(
            *(app.state.index.candidate_ids(intent) for intent in intents)
        )
        ids = await asyncio.gather(
            *(
                app.state.index.candidate_ids(intent, normalized_colors=BLUE)
                for intent in intents
            )
        )
        union = list(dict.fromkeys(value for pool in ids for value in pool))
        products = await app.state.index.candidates(intents[0], offer_ids=union)
        return raw_ids, ids, products

    await execute()
    runs = []
    for _ in range(10):
        statements = 0
        started = time.perf_counter()
        raw_pools, pools, products = await execute()
        runs.append(
            {
                "ms": round((time.perf_counter() - started) * 1000, 2),
                "sql_statements": statements,
                "candidate_id_rows": sum(map(len, pools)),
                "raw_candidate_id_rows": sum(map(len, raw_pools)),
                "rows_hydrated": len(products),
            }
        )
    timings = sorted(run["ms"] for run in runs)
    result = {
        "positions": POSITIONS,
        "runs": runs,
        "p50_ms": round(statistics.median(timings), 2),
        "p95_ms": timings[max(0, math.ceil(len(timings) * 0.95) - 1)],
        "max_ms": max(timings),
    }
    path = Path(".local/composite-sql-profile/benchmark.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    Path(".local/composite-performance.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    await app.state.engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
