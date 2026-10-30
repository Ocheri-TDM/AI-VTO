import itertools
import random

from app.application.hard_requests import HardRequestService
from app.domain.hard_request import (
    ColorConstraint,
    HardRequestIntent,
    QuantityConstraint,
    deterministic_hard_request,
)
from app.domain.models import AvailabilityRecord, ColorGroup, Product, ProductColor, utcnow


def offer(identifier, records=None, stale=False):
    return Product(
        id=identifier,
        supplier="fixture",
        source_url=f"https://example.test/{identifier}",
        name=identifier,
        availability_records=records or [],
        metadata={"availability_stale": stale},
    )


def stock(state, free=None, reserved=None):
    return AvailabilityRecord(
        state=state,
        free_quantity=free,
        reserved_quantity=reserved,
        observed_at=utcnow(),
        parser_version="fixture",
        source_label="fixture",
    )


def test_strict_composite_quantity_accepts_only_fresh_current_free_stock():
    service = HardRequestService(None, None, None)
    assert service.strict_procurement_evaluation(offer("ok", [stock("ON_HAND", 250)]), 250).result == "PASS"
    assert service.strict_procurement_evaluation(offer("incoming", [stock("INCOMING", 500)]), 250).reason_code == "INCOMING_ONLY"
    assert service.strict_procurement_evaluation(offer("remote", [stock("REMOTE_STOCK", 500)]), 250).reason_code == "REMOTE_ONLY"
    assert service.strict_procurement_evaluation(offer("reserved", [stock("ON_HAND", 0, 500)]), 250).reason_code == "RESERVED_ONLY"
    stale = service.strict_procurement_evaluation(offer("stale", [stock("ON_HAND", 500)], True), 250)
    assert (stale.result, stale.reason_code) == ("PASS", "LAST_KNOWN_FREE_SUFFICIENT")
    assert service.strict_procurement_evaluation(offer("unknown", [stock("ON_HAND")]), 250).reason_code == "UNKNOWN_QUANTITY"


def test_maximum_matching_recovers_from_greedy_dead_end():
    service = HardRequestService(None, None, None)
    plan = HardRequestIntent(target_sets=1, categories=["A", "B"])
    shared, alternative = offer("shared"), offer("alternative")
    pools = {"A": [shared, alternative], "B": [shared]}
    identities = {"shared": "shared", "alternative": "alternative"}

    result = service.assembly_plan(plan, pools, identities)

    assert len(result) == 1
    selected = [chosen[0].id for _category, chosen in result[0]]
    assert set(selected) == {"shared", "alternative"}


def test_multiple_required_items_create_selected_slots_with_independent_alternatives():
    service = HardRequestService(None, None, None)
    products = [offer(f"pen-{number}") for number in range(9)]
    plan = HardRequestIntent(
        target_sets=1, categories=["pen"], items_per_position=3,
        candidates_per_position=3,
    )

    result = service.assembly_plan(
        plan, {"pen": products}, {product.id: product.id for product in products}
    )

    assert len(result) == 1
    assert len(result[0]) == 3
    assert all(len(chosen) == 3 for _category, chosen in result[0])
    assert len({chosen[0].id for _category, chosen in result[0]}) == 3


def test_multiple_required_items_return_bounded_partial_capacity():
    service = HardRequestService(None, None, None)
    products = [offer(f"umbrella-{number}") for number in range(7)]
    plan = HardRequestIntent(target_sets=5, categories=["umbrella"], items_per_position=3)

    result = service.assembly_plan(
        plan, {"umbrella": products}, {product.id: product.id for product in products}
    )

    assert len(result) == 2
    assert all(len(case) == 3 for case in result)


def test_random_color_means_deterministic_any_known_base_color():
    service = HardRequestService(None, None, None)
    plan = deterministic_hard_request(
        "Собери 5 наборов из зонтов, ручек рандомного цвета от 20 штук"
    )
    known = offer("known")
    known.colors = [ProductColor(original_color="синий", normalized_color=ColorGroup.BLUE)]
    unknown = offer("unknown")
    unknown.colors = [ProductColor(original_color="неизвестный", normalized_color=ColorGroup.UNKNOWN)]

    assert plan.color.mode == "DIVERSE"
    assert service.color_eligible(known, plan.color)
    assert not service.color_eligible(unknown, plan.color)


def brute_force_capacity(categories, pools, target):
    slots = list(itertools.chain.from_iterable(categories for _ in range(target)))

    def feasible(count):
        needed = slots[: count * len(categories)]

        def visit(index, used):
            if index == len(needed):
                return True
            return any(
                identity not in used and visit(index + 1, used | {identity})
                for identity in pools[needed[index]]
            )

        return visit(0, set())

    return max((count for count in range(target + 1) if feasible(count)), default=0)


def test_allocator_matches_brute_force_on_generated_small_overlapping_pools():
    rng = random.Random(20261003)
    service = HardRequestService(None, None, None)
    categories = ["A", "B", "C"]
    universe = [f"p{number}" for number in range(6)]
    products = {identity: offer(identity) for identity in universe}
    identities = {identity: identity for identity in universe}

    for _ in range(80):
        identity_pools = {
            category: [identity for identity in universe if rng.random() < 0.55]
            for category in categories
        }
        pools = {
            category: [products[identity] for identity in values]
            for category, values in identity_pools.items()
        }
        plan = HardRequestIntent(target_sets=3, categories=categories)
        actual = len(service.assembly_plan(plan, pools, identities))
        assert actual == brute_force_capacity(categories, identity_pools, 3)


def test_2592_generated_optional_constraint_combinations_are_null_safe():
    """Exercise normalized constraints through eligibility, assembly and serialization."""
    service = HardRequestService(None, None, None)
    checked = 0
    for minimum, price, color_mode, materials, has_stock, target, category_count in itertools.product(
        (None, 1, 20, 250),
        (None, 100.0, 1000.0),
        ("BASE_COLORS", "REQUIRED", "DIVERSE"),
        ([], ["хлопок"], ["без пластика"]),
        (False, True),
        (1, 10, 50),
        (1, 2, 4, 6),
    ):
        categories = [f"category-{number}" for number in range(category_count)]
        color = ColorConstraint(
            mode=color_mode,
            values=["BLUE"] if color_mode == "REQUIRED" else [],
        )
        plan = HardRequestIntent(
            target_sets=target,
            categories=categories,
            quantity=QuantityConstraint(minimum=minimum),
            color=color,
            price_max=price,
            materials=materials,
        )
        records = [stock("ON_HAND", 500)] if has_stock else []
        products = {}
        identities = {}
        for category in categories:
            item = offer(f"{category}-offer", records)
            item.colors = [ProductColor(original_color="синий", normalized_color=ColorGroup.BLUE)]
            effective = plan.effective(category)
            assert effective.required_quantity == minimum
            service.strict_procurement_evaluation(item, effective.required_quantity)
            service.color_eligible(item, effective.color)
            service.quantity_eligible(item, plan)
            products[category] = [item]
            identities[item.id] = item.id
        service.assembly_plan(plan, products, identities)
        plan.model_dump_json()
        checked += 1
    assert checked == 2592
