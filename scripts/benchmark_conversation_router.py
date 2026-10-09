"""Deterministic generated holdout for conversational routing."""

import asyncio
import json
import random
import statistics
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.domain.conversation_router import ConversationAction, ConversationQueryRouter

SUBJECTS = {"карандаш": "pencil", "кружка": "mug", "ручка": "pen", "зонт": "umbrella"}


class OracleUnderstanding:
    async def understand(self, text):
        concepts = [concept for word, concept in SUBJECTS.items() if word[:4] in text.casefold()]
        return SimpleNamespace(search_query=text, concepts=concepts)


async def main():
    rng = random.Random(20261004)
    router = ConversationQueryRouter(OracleUnderstanding())
    refinements = ["оставь синие", "только металлические", "до 500 тенге", "в наличии", "без пластика"]
    commands = ["покажи всё", "отмени", "назад", "выбери 5", "похожие на 3"]
    rows = []
    latencies = []
    for index in range(2400):
        current_word, current = rng.choice(list(SUBJECTS.items()))
        kind = index % 4
        if kind == 0:
            message, expected = rng.choice(refinements), ConversationAction.REFINE_CURRENT
            if message.startswith("без"):
                expected = ConversationAction.REMOVE_CONSTRAINT
        elif kind == 1:
            message, expected = rng.choice(commands), ConversationAction.COMMAND
            if message == "покажи всё":
                expected = ConversationAction.RESET_FILTERS
        elif kind == 2:
            choices = [(w, c) for w, c in SUBJECTS.items() if c != current]
            word, _ = rng.choice(choices)
            message, expected = f"найди {word}", ConversationAction.NEW_SEARCH
        else:
            message, expected = f"покажи синие {current_word}", ConversationAction.REFINE_CURRENT
        started = time.perf_counter()
        route = await router.route(message, [current])
        latencies.append((time.perf_counter() - started) * 1000)
        rows.append({"input": message, "current": current, "expected": expected.value,
                     "actual": route.action.value, "passed": route.action == expected})
    failures = [row for row in rows if not row["passed"]]
    result = {
        "seed": 20261004, "generated": len(rows), "oracle_validated": len(rows),
        "passed": len(rows) - len(failures), "pass_rate": (len(rows) - len(failures)) / len(rows),
        "subject_loss_rate": sum(r["expected"] != "NEW_SEARCH" and r["actual"] == "NEW_SEARCH" for r in rows) / len(rows),
        "false_inheritance_rate": sum(r["expected"] == "NEW_SEARCH" and r["actual"] != "NEW_SEARCH" for r in rows) / len(rows),
        "latency_ms": {"p50": statistics.median(latencies), "p95": sorted(latencies)[int(len(latencies) * .95)], "max": max(latencies)},
        "failures": failures[:50],
    }
    output = ROOT / ".local" / "conversation-router-holdout.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(bool(failures))


if __name__ == "__main__":
    asyncio.run(main())
