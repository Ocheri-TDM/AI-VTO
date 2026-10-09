import random

from app.domain.hard_request import deterministic_hard_request
from app.domain.intent_mutation import IntentMutationService


def mutate(service, plan, message):
    result = service.plan(plan, message, message)
    assert result.patches, message
    return service.apply(plan, result), result


def concepts(service, plan):
    return [service._concepts(position.category_text)[0] for position in plan.positions]


def test_required_composite_mutation_chain_is_scoped_and_immutable():
    service = IntentMutationService()
    initial = deterministic_hard_request(
        "собери 50 наборов из зонтов, ручек, бутылок и футболок"
    )
    plan, _ = mutate(service, initial, "футболки сделай красными")
    assert plan.effective("футболок").color.values == ["RED"]
    assert initial.effective("футболок").color.mode == "BASE_COLORS"
    plan, _ = mutate(service, plan, "ручки сделай синими")
    assert "BLUE" in plan.effective("ручек").color.values
    plan, _ = mutate(service, plan, "добавь черные рюкзаки")
    assert "backpack" in concepts(service, plan)
    plan, _ = mutate(service, plan, "замени бутылки на белые термокружки")
    assert "bottle" not in concepts(service, plan)
    assert "thermomug" in concepts(service, plan)
    plan, _ = mutate(service, plan, "убери зонты")
    assert "umbrella" not in concepts(service, plan)
    plan, _ = mutate(service, plan, "теперь от 250 штук")
    assert plan.required_quantity == 250
    plan, _ = mutate(service, plan, "сделай 20 наборов")
    assert plan.target_sets == 20 and plan.required_quantity == 250
    plan, _ = mutate(service, plan, "количество не важно")
    assert plan.required_quantity is None and plan.target_sets == 20
    plan, _ = mutate(service, plan, "оставь только ручки, футболки и рюкзаки")
    assert set(concepts(service, plan)) == {"pen", "tshirt", "backpack"}


def test_exact_reference_chain_checks_every_semantic_revision():
    service = IntentMutationService()
    plan = deterministic_hard_request(
        "собери 50 наборов из зонтов, ручек, бутылок и футболок"
    )

    def state():
        return {
            "target": plan.target_sets,
            "quantity": plan.required_quantity,
            "positions": {
                service._concepts(position.category_text)[0]: (
                    plan.effective(position.category_text).color.values
                )
                for position in plan.positions
            },
        }

    assert set(state()["positions"]) == {"umbrella", "pen", "bottle", "tshirt"}
    plan, _ = mutate(service, plan, "замени цвет футболок на красный")
    assert state()["positions"]["tshirt"] == ["RED"]
    plan, _ = mutate(service, plan, "ручки сделай синими")
    assert "BLUE" in state()["positions"]["pen"]
    plan, _ = mutate(service, plan, "добавь черные рюкзаки")
    assert state()["positions"]["backpack"] == ["BLACK"]
    plan, _ = mutate(service, plan, "замени бутылки на белые кружки")
    assert "bottle" not in state()["positions"]
    assert state()["positions"]["mug"] == ["WHITE"]
    plan, _ = mutate(service, plan, "убери зонты")
    assert "umbrella" not in state()["positions"]
    plan, _ = mutate(service, plan, "теперь от 250 штук")
    assert state()["quantity"] == 250
    plan, _ = mutate(service, plan, "сделай 20 наборов")
    assert state()["target"] == 20 and state()["quantity"] == 250
    plan, _ = mutate(service, plan, "количество товара больше не важно")
    assert state()["target"] == 20 and state()["quantity"] is None
    plan, _ = mutate(service, plan, "оставь только ручки, футболки и рюкзаки")
    final = state()
    assert set(final["positions"]) == {"pen", "tshirt", "backpack"}
    assert "BLUE" in final["positions"]["pen"]
    assert final["positions"]["tshirt"] == ["RED"]
    assert final["positions"]["backpack"] == ["BLACK"]


def test_multiple_position_colors_do_not_leak():
    service = IntentMutationService()
    plan = deterministic_hard_request(
        "собери 10 наборов из футболок, ручек, бутылок и кружек"
    )
    revised, result = mutate(
        service, plan, "футболки сделай красными, ручки синими, бутылки белыми"
    )
    values = {
        service._concepts(position.category_text)[0]: revised.effective(position.category_text).color.values
        for position in revised.positions
    }
    assert values["tshirt"] == ["RED"]
    assert "BLUE" in values["pen"]
    assert values["bottle"] == ["WHITE"]
    assert values["mug"] == []
    assert len([p for p in result.patches if p.field == "COLOR"]) == 3


def test_open_vocabulary_position_color_does_not_become_global():
    service = IntentMutationService()
    plan = deterministic_hard_request("собери 1 набор из значков, диффузоров")
    revised, result = mutate(service, plan, "диффузоры сделай красными")
    colors = {
        position.category_text: revised.effective(position.category_text).color.values
        for position in revised.positions
    }
    assert colors["значков"] == []
    assert colors["диффузоров"] == ["RED"]
    assert len([patch for patch in result.patches if patch.field == "COLOR"]) == 1
    assert result.patches[-1].scope == "POSITION"


def test_named_position_color_clear_is_local():
    service = IntentMutationService()
    plan = deterministic_hard_request("собери 5 наборов из ручек, футболок")
    plan, _ = mutate(service, plan, "футболки красные")
    revised, result = mutate(service, plan, "цвет футболок не важен")
    colors = {
        service._concepts(position.category_text)[0]: revised.effective(position.category_text).color.values
        for position in revised.positions
    }
    assert colors["tshirt"] == []
    assert colors["pen"] == []
    assert len(result.patches) == 1
    assert result.patches[0].scope == "POSITION"


def test_price_material_and_clear_operations_are_semantic():
    service = IntentMutationService()
    plan = deterministic_hard_request("собери 10 наборов из ручек и кружек")
    plan, _ = mutate(service, plan, "ручки из металла")
    assert plan.effective("ручек").materials
    plan, _ = mutate(service, plan, "бюджет от 100 до 5000 тенге")
    assert plan.price_min == 100 and plan.price_max == 5000
    plan, _ = mutate(service, plan, "цена не важна")
    assert plan.price_min is None and plan.price_max is None
    plan, _ = mutate(service, plan, "материал ручек не важен")
    assert plan.effective("ручек").materials == []


def test_generated_transition_sequences_are_null_safe_and_deterministic():
    service = IntentMutationService()
    messages = [
        "футболки красные", "ручки синие", "бутылки белые",
        "теперь от 20 штук", "количество не важно", "сделай 30 наборов",
        "до 5000 тенге", "цена не важна", "футболки из хлопка",
        "материал футболок не важен", "добавь рюкзаки", "убери рюкзаки",
        "оставь только ручки и кружки", "оставь только синие",
    ]
    rng = random.Random(20261005)
    exceptions = []
    transitions = 0
    # 5,000 reproducible state sequences, each 3-15 transitions.
    for _ in range(5000):
        plan = deterministic_hard_request(
            "собери 50 наборов из футболок, ручек, бутылок и кружек"
        )
        for _ in range(rng.randint(3, 15)):
            message = rng.choice(messages)
            try:
                result = service.plan(plan, message, message)
                if result.patches:
                    plan = service.apply(plan, result)
                    plan.model_dump(mode="json")
                transitions += 1
            except Exception as exc:  # test reports the exact stage/type instead of masking it
                exceptions.append((message, type(exc).__name__, str(exc)))
                break
    assert transitions >= 15000
    assert exceptions == []
