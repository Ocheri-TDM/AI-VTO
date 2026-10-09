"""Compare the production maximum matcher with exhaustive small-pool search."""

import itertools
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, "apps/api")

from app.application.hard_requests import HardRequestService
from app.domain.hard_request import HardRequestIntent
from app.domain.models import Product


def brute_force(categories, pools, target):
    def feasible(case_count):
        slots = list(itertools.chain.from_iterable(categories for _ in range(case_count)))

        def visit(index, used):
            if index == len(slots):
                return True
            return any(
                value not in used and visit(index + 1, used | {value})
                for value in pools[slots[index]]
            )

        return visit(0, set())

    return max(value for value in range(target + 1) if feasible(value))


def main():
    rng = random.Random(20261003)
    categories = ["A", "B", "C", "D"]
    identities = [f"p{number}" for number in range(8)]
    products = {
        value: Product(
            id=value,
            supplier="fixture",
            source_url=f"https://example.test/{value}",
            name=value,
        )
        for value in identities
    }
    service = HardRequestService(None, None, None)
    mismatches = []
    cases = 250
    for iteration in range(cases):
        raw = {
            category: [value for value in identities if rng.random() < 0.45]
            for category in categories
        }
        pools = {key: [products[value] for value in values] for key, values in raw.items()}
        plan = HardRequestIntent(target_sets=2, categories=categories)
        actual = len(service.assembly_plan(plan, pools, {value: value for value in identities}))
        expected = brute_force(categories, raw, 2)
        if actual != expected:
            mismatches.append(
                {"iteration": iteration, "pools": raw, "expected": expected, "actual": actual}
            )
    result = {
        "seed": 20261003,
        "datasets": cases,
        "matched_brute_force": cases - len(mismatches),
        "mismatches": mismatches,
        "pass_rate": (cases - len(mismatches)) / cases,
        "algorithm": "maximum bipartite matching over case-position slots and canonical products",
    }
    Path(".local").mkdir(exist_ok=True)
    Path(".local/composite-allocator-validation.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
