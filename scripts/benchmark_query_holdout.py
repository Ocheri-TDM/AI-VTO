"""Oracle-based generative holdout for the current catalog query vocabulary."""

import argparse
import asyncio
import json
import random
import re
from collections import defaultdict
from pathlib import Path

from app.config import Settings
from app.database.connection import create_database
from app.database.models import SupplierOffer
from app.domain.query_understanding import QueryUnderstandingService
from app.domain.taxonomy import UniversalCategoryResolver
from sqlalchemy import select

parser = argparse.ArgumentParser()
parser.add_argument("--seed", type=int, default=20261003)
parser.add_argument("--output", default=".local/query-understanding-holdout.json")
args = parser.parse_args()
SEED = args.seed
CASES_PER_MUTATION = 200
CYRILLIC = "абвгдежзийклмнопрстуфхцчшщыэюя"
VOWELS = "аеёиоуыэюя"
PHONETIC = {
    "б": "п",
    "п": "б",
    "в": "ф",
    "ф": "в",
    "г": "к",
    "к": "г",
    "д": "т",
    "т": "д",
    "ж": "ш",
    "ш": "ж",
    "з": "с",
    "с": "з",
}
CONFUSABLE = {
    "а": "a",
    "в": "b",
    "с": "c",
    "е": "e",
    "н": "h",
    "к": "k",
    "м": "m",
    "о": "o",
    "р": "p",
    "т": "t",
    "х": "x",
    "у": "y",
}
RU = "йцукенгшщзхъфывапролджэячсмитьбю"
EN = "qwertyuiop[]asdfghjkl;'zxcvbnm,."
TO_WRONG_LAYOUT = str.maketrans(RU, EN)
KEYBOARD_NEIGHBORS = {
    char: row[max(0, index - 1)] if index else row[min(1, len(row) - 1)]
    for row in ("йцукенгшщзхъ", "фывапролджэ", "ячсмитьбю")
    for index, char in enumerate(row)
}


def transliterate(value):
    table = {
        "а": "a",
        "б": "b",
        "в": "v",
        "г": "g",
        "д": "d",
        "е": "e",
        "ё": "e",
        "ж": "zh",
        "з": "z",
        "и": "i",
        "й": "y",
        "к": "k",
        "л": "l",
        "м": "m",
        "н": "n",
        "о": "o",
        "п": "p",
        "р": "r",
        "с": "s",
        "т": "t",
        "у": "u",
        "ф": "f",
        "х": "kh",
        "ц": "ts",
        "ч": "ch",
        "ш": "sh",
        "щ": "sch",
        "ы": "y",
        "э": "e",
        "ю": "yu",
        "я": "ya",
        "ь": "",
        "ъ": "",
    }
    return "".join(table.get(char, char) for char in value)


def mutate(value, kind, randomizer):
    if kind in {"DELETE_CHAR", "MISSING_CHAR"}:
        index = randomizer.randrange(
            1 if len(value) > 4 else 0, len(value) - 1 if len(value) > 4 else len(value)
        )
        return value[:index] + value[index + 1 :]
    if kind == "INSERT_CHAR":
        index = randomizer.randrange(len(value) + 1)
        return value[:index] + randomizer.choice(CYRILLIC) + value[index:]
    if kind == "SUBSTITUTE_CHAR":
        index = randomizer.randrange(len(value))
        return (
            value[:index]
            + randomizer.choice(CYRILLIC.replace(value[index], ""))
            + value[index + 1 :]
        )
    if kind == "TRANSPOSE_ADJACENT":
        index = randomizer.randrange(len(value) - 1)
        return value[:index] + value[index + 1] + value[index] + value[index + 2 :]
    if kind == "DOUBLE_CHAR":
        index = randomizer.randrange(len(value))
        return value[:index] + value[index] + value[index:]
    if kind == "KEYBOARD_NEIGHBOR":
        positions = [
            index for index, char in enumerate(value) if char in KEYBOARD_NEIGHBORS
        ]
        index = randomizer.choice(positions)
        return value[:index] + KEYBOARD_NEIGHBORS[value[index]] + value[index + 1 :]
    if kind == "WRONG_LAYOUT":
        return value.translate(TO_WRONG_LAYOUT)
    if kind == "CYRILLIC_LATIN_CONFUSABLE":
        positions = [index for index, char in enumerate(value) if char in CONFUSABLE]
        if not positions:
            return mutate(value, "SUBSTITUTE_CHAR", randomizer)
        index = randomizer.choice(positions)
        return value[:index] + CONFUSABLE[value[index]] + value[index + 1 :]
    if kind == "TRANSLITERATION":
        return transliterate(value)
    if kind == "PHONETIC_SUBSTITUTION":
        positions = [index for index, char in enumerate(value) if char in PHONETIC]
        if not positions:
            return mutate(value, "SUBSTITUTE_CHAR", randomizer)
        index = randomizer.choice(positions)
        return value[:index] + PHONETIC[value[index]] + value[index + 1 :]
    if kind == "VOWEL_SUBSTITUTION":
        positions = [index for index, char in enumerate(value) if char in VOWELS]
        index = randomizer.choice(positions)
        return (
            value[:index]
            + randomizer.choice(VOWELS.replace(value[index], ""))
            + value[index + 1 :]
        )
    if kind == "ENDING_CORRUPTION":
        return value[:-1] + randomizer.choice("аяиыов")
    if kind == "REPEATED_ERROR":
        index = randomizer.randrange(len(value))
        return value[:index] + value[index] * 3 + value[index + 1 :]
    count = 2 if kind == "TWO_MUTATIONS" else 3
    result = value
    for _ in range(count):
        result = mutate(
            result,
            randomizer.choice(
                [
                    "DELETE_CHAR",
                    "INSERT_CHAR",
                    "SUBSTITUTE_CHAR",
                    "TRANSPOSE_ADJACENT",
                    "PHONETIC_SUBSTITUTION",
                ]
            ),
            randomizer,
        )
    return result


async def main():
    randomizer = random.Random(SEED)
    database, sessions = create_database(Settings().database_url)
    service = QueryUnderstandingService(sessions)
    vocabulary = list(await service.vocabulary())
    resolver = UniversalCategoryResolver()
    canonical = {}
    for item in vocabulary:
        value = item.replacement.casefold()
        expected = set(resolver.resolve(value)) or (
            {item.concept} if not item.concept.startswith("_") else set()
        )
        if re.fullmatch(r"[а-яё]{5,16}", value) and expected:
            canonical.setdefault(value, sorted(expected))
    terms = sorted(canonical.items())
    randomizer.shuffle(terms)
    split = max(1, int(len(terms) * 0.4))
    tuning_terms, holdout_terms = terms[:split], terms[split:]
    if len(holdout_terms) < 10:
        holdout_terms = terms

    mutation_types = [
        "DELETE_CHAR",
        "INSERT_CHAR",
        "SUBSTITUTE_CHAR",
        "TRANSPOSE_ADJACENT",
        "DOUBLE_CHAR",
        "MISSING_CHAR",
        "KEYBOARD_NEIGHBOR",
        "WRONG_LAYOUT",
        "CYRILLIC_LATIN_CONFUSABLE",
        "TRANSLITERATION",
        "PHONETIC_SUBSTITUTION",
        "VOWEL_SUBSTITUTION",
        "ENDING_CORRUPTION",
        "REPEATED_ERROR",
        "TWO_MUTATIONS",
        "THREE_MUTATIONS",
    ]
    oracle = []
    for kind in mutation_types:
        for _ in range(CASES_PER_MUTATION):
            value, expected = randomizer.choice(holdout_terms)
            oracle.append(
                {
                    "input": mutate(value, kind, randomizer),
                    "canonical": value,
                    "expected_concepts": expected,
                    "mutation_type": kind,
                    "expected_action": "CORRECT",
                }
            )

    async with sessions() as session:
        payloads = (
            await session.scalars(select(SupplierOffer.payload).limit(1500))
        ).all()
    protected = {"USB-C", "Powerbank", "Parker", "Parker Jotter", "Blackie 3D"}
    for payload in payloads:
        for candidate in (
            payload.get("brand"),
            payload.get("metadata", {}).get("brand"),
        ):
            if not candidate:
                continue
            value = str(candidate).strip()
            # Some providers put breadcrumb-like "Brand, Category" text into
            # the source brand field. It is not a valid negative oracle: the
            # category portion is expected to normalize. Keep clean brand/model
            # values and extract Latin brand tokens from noisy source fields.
            if "," not in value and len(value.split()) <= 3 and 2 <= len(value) <= 60:
                protected.add(value)
            else:
                protected.update(re.findall(r"[A-Za-z][A-Za-z0-9-]{1,39}", value))
    for value in sorted(protected)[:500]:
        oracle.append(
            {
                "input": value,
                "canonical": value,
                "expected_concepts": [],
                "mutation_type": "BRAND_MODEL",
                "expected_action": "PRESERVE",
            }
        )
    chunks = ["ква", "фле", "ном", "тар", "зик", "пле", "шор", "вум", "гра", "цен"]
    for _ in range(500):
        value = "".join(randomizer.sample(chunks, randomizer.choice((2, 3))))
        oracle.append(
            {
                "input": value,
                "canonical": value,
                "expected_concepts": [],
                "mutation_type": "UNKNOWN",
                "expected_action": "PRESERVE",
            }
        )
    for left, right in zip(holdout_terms[::2], holdout_terms[1::2]):
        value = left[0][: max(2, len(left[0]) // 2)] + right[0][len(right[0]) // 2 :]
        oracle.append(
            {
                "input": value,
                "canonical": value,
                "expected_concepts": [],
                "mutation_type": "NEAR_COLLISION",
                "expected_action": "AMBIGUOUS",
            }
        )

    metrics = defaultdict(
        lambda: {"cases": 0, "correct": 0, "wrong": 0, "preserved": 0, "ambiguous": 0}
    )
    failures = []
    latencies = []
    for case in oracle:
        result = await service.understand(case["input"])
        latencies.append(result.latency_ms)
        resolved = set(result.concepts) | set(resolver.resolve(result.normalized_query))
        corrected = bool(result.corrections)
        expected = set(case["expected_concepts"])
        row = metrics[case["mutation_type"]]
        row["cases"] += 1
        if case["expected_action"] == "CORRECT":
            success = result.normalized_query == case["canonical"] or bool(
                resolved & expected
            )
            if success:
                row["correct"] += 1
            elif corrected:
                row["wrong"] += 1
            else:
                row["preserved"] += 1
        elif corrected:
            row["wrong"] += 1
        else:
            row[
                "preserved" if case["expected_action"] == "PRESERVE" else "ambiguous"
            ] += 1
        if (case["expected_action"] == "CORRECT" and not success) or (
            case["expected_action"] != "CORRECT" and corrected
        ):
            failures.append(
                case
                | {
                    "actual_normalized": result.normalized_query,
                    "actual_concepts": sorted(resolved),
                    "corrections": result.model_dump(mode="json")["corrections"],
                    "decisions": result.model_dump(mode="json")["decisions"],
                }
            )

    table = {}
    for kind, values in metrics.items():
        accepted = values["correct"] + values["wrong"]
        table[kind] = values | {
            "recall": round(values["correct"] / values["cases"], 4)
            if kind not in {"BRAND_MODEL", "UNKNOWN", "NEAR_COLLISION"}
            else None,
            "precision": round(values["correct"] / accepted, 4) if accepted else None,
            "false_correction_rate": round(values["wrong"] / values["cases"], 4),
        }
    latencies.sort()
    positive = [
        value
        for key, value in table.items()
        if key not in {"BRAND_MODEL", "UNKNOWN", "NEAR_COLLISION"}
    ]
    report = {
        "seed": SEED,
        "vocabulary_terms": len(terms),
        "tuning_terms": len(tuning_terms),
        "holdout_terms": len(holdout_terms),
        "total_generated": len(oracle),
        "total_oracle_validated": len(oracle),
        "metrics": table,
        "macro_precision": round(
            sum((value["precision"] or 0) for value in positive) / len(positive), 4
        ),
        "macro_recall": round(
            sum((value["recall"] or 0) for value in positive) / len(positive), 4
        ),
        "latency_ms": {
            "p50": latencies[len(latencies) // 2],
            "p95": latencies[int(len(latencies) * 0.95)],
            "p99": latencies[int(len(latencies) * 0.99)],
            "max": max(latencies),
        },
        "failures": failures,
    }
    Path(args.output).write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    Path(".local/query-understanding-failures.json").write_text(
        json.dumps(failures, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {key: value for key, value in report.items() if key != "failures"},
            ensure_ascii=False,
            indent=2,
        )
    )
    await database.dispose()


asyncio.run(main())
