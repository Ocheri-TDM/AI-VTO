from types import SimpleNamespace

import pytest

from app.application.hard_requests import HardRequestService
from app.application.research_planner import ResearchPlanner
from app.domain.hard_request import deterministic_hard_request
from app.domain.taxonomy import UniversalCategoryResolver


def parse(text):
    return deterministic_hard_request(text)


def concepts(value):
    resolver = UniversalCategoryResolver()
    return [resolver.resolve(category)[0] for category in value.categories]


def test_it_conference_functional_positions_are_products_not_prose():
    value = parse(
        "Подбери 15 подарочных наборов для IT-конференции. В каждом должно быть что-то "
        "для записи, что-то для напитков и что-то полезное для ноутбука. Всё в чёрном "
        "цвете, от 100 штук"
    )
    assert value.request_mode == "COMPOSITE"
    assert value.target_sets == 15
    assert value.categories == ["pen", "thermomug", "backpack"]
    assert value.color.values == ["BLACK"]
    assert value.quantity.minimum == 100


def test_mixed_language_set_count_typo_and_exact_navy():
    value = parse(
        "Собери 10 сетов из powerbank, шопперов, термо кружек и карондашей. "
        "Цвет navy blue, тираж минимум 150шт"
    )
    assert value.request_mode == "COMPOSITE"
    assert value.target_sets == 10
    assert value.categories == ["powerbank", "shopperbag", "thermomug", "pencil"]
    assert value.color.values == ["NAVY"]
    assert value.quantity.minimum == 150


def test_shopper_single_and_double_consonant_have_identical_effective_intent():
    template = (
        "Собери 10 сетов из powerbank, {shopper}, термо кружек и карондашей. "
        "Цвет navy blue, тираж минимум 150шт"
    )
    double = parse(template.format(shopper="шопперов"))
    single = parse(template.format(shopper="шоперов"))

    assert single.model_dump(exclude={"original_query", "normalized_query"}) == double.model_dump(
        exclude={"original_query", "normalized_query"}
    )
    assert single.categories == ["powerbank", "shopperbag", "thermomug", "pencil"]


def test_position_constraints_and_global_exclusion():
    value = parse(
        "Нужно 8 наборов: красные футболки от 200 шт, чёрные повербанки от 100 шт "
        "и синие ручки от 500 шт. Никаких белых товаров"
    )
    assert value.target_sets == 8
    assert value.categories == ["tshirt", "powerbank", "pen"]
    by_category = {item.category_text: item.constraints for item in value.positions}
    assert by_category["tshirt"].color.values == ["RED"]
    assert by_category["tshirt"].quantity.minimum == 200
    assert by_category["powerbank"].color.values == ["BLACK"]
    assert by_category["powerbank"].quantity.minimum == 100
    assert by_category["pen"].color.values == ["BLUE"]
    assert by_category["pen"].quantity.minimum == 500
    assert value.excluded_colors == ["WHITE"]


def test_executive_discovery_keeps_roles_out_of_categories():
    value = parse(
        "Найди подарки для руководителей до 12000 тенге за штуку. Без пластика, без "
        "одежды, желательно металл или дерево. Не показывай обычные канцелярские ручки"
    )
    assert value.request_mode == "SIMPLE"
    assert value.categories == []
    assert value.audience == "executives"
    assert value.price_max == 12000
    assert value.excluded_materials == ["PLASTIC"]
    assert set(value.semantic_preferences) >= {"METAL", "WOOD"}
    assert "pen" in value.exclusions


def test_writing_pen_negation_does_not_create_positions():
    value = parse(
        "Нужны ручки из металла, но не те, которыми открывают двери или носят сумки. "
        "Чёрные, от 250 штук"
    )
    assert value.request_mode == "SIMPLE"
    assert value.categories == ["pen"]
    assert value.materials == ["METAL"]
    assert value.color.values == ["BLACK"]
    assert value.quantity.minimum == 250


def test_complex_requirements_create_required_slots_not_alternatives():
    value = parse(
        "Собери ровно 30 разных наборов из зонтов, дождевиков, носков и термосов. "
        "В каждом по 3 разных товара каждой категории, от 300 штук. Не повторяй "
        "товары между наборами"
    )
    assert value.target_sets == 30
    assert value.categories == ["umbrella", "raincoat", "socks", "thermos"]
    assert value.items_per_position == 3
    assert value.require_cross_set_uniqueness
    assert value.unsupported_constraints == []
    assert all(position.constraints.items_required == 3 for position in value.positions)


def test_hybrid_pencil_preferences_do_not_become_hard_filters():
    value = parse(
        "Найди вечные карандаши 2 в 1, желательно металлические, но не исключай "
        "модели с пластиком. Покажи сначала чёрные"
    )
    assert value.request_mode == "SIMPLE"
    assert value.categories == ["pencil"]
    assert "METAL" in value.semantic_preferences
    assert "PLASTIC" not in value.excluded_materials
    assert value.color.mode == "BASE_COLORS"
    assert value.sort_preferences == ["BLACK"]


def test_travel_discovery_quantity_is_not_a_category():
    value = parse(
        "Мне нужно что-нибудь небанальное для сотрудников, кто постоянно в дороге. "
        "Без кружек и футболок. Бюджет 7000 тенге на предмет, штук 80"
    )
    assert value.request_mode == "SIMPLE"
    assert value.categories == []
    assert value.audience == "traveling_employees"
    assert value.price_max == 7000
    assert value.quantity.minimum == 80
    assert set(value.exclusions) >= {"mug", "tshirt"}


def test_mixed_required_item_counts_are_bound_to_categories():
    value = parse("Собери набор: две ручки, один ежедневник, три термокружки")
    assert value.request_mode == "COMPOSITE"
    assert {
        position.category_text: position.constraints.items_required
        for position in value.positions
    } == {"pen": 2, "notebook": 1, "thermomug": 3}


@pytest.mark.asyncio
async def test_resolved_purpose_does_not_call_llm():
    class UnexpectedModel:
        async def complete(self, *_args, **_kwargs):
            raise AssertionError("resolved purpose must remain deterministic")

    service = HardRequestService(None, None, None, model=UnexpectedModel())
    value = await service.parse(
        "Мне нужно что-нибудь небанальное для сотрудников, кто постоянно в дороге. "
        "Без кружек и футболок. Бюджет 7000 тенге на предмет, штук 80"
    )
    assert value.audience == "traveling_employees"
    assert value.query_understanding["llm_used"] is False


def test_long_one_category_request_with_commas_stays_simple():
    value = parse("Нужны чёрные металлические ручки, желательно матовые, но без пластика, от 50 штук")
    assert value.request_mode == "SIMPLE"
    assert value.categories == ["pen"]


@pytest.mark.asyncio
async def test_composite_parser_keeps_raw_text_when_query_rewrite_is_corrupt():
    class CorruptUnderstanding:
        async def understand(self, _text):
            return SimpleNamespace(
                search_query="�������� powerbank",
                confidence=1.0,
                model_dump=lambda **_: {"search_query": "�������� powerbank", "confidence": 1.0},
            )

    service = HardRequestService(None, None, None, query_understanding=CorruptUnderstanding())
    value = await service.parse(
        "Собери 10 сетов из powerbank, шопперов, термо кружек и карондашей. "
        "Цвет navy blue, тираж минимум 150шт"
    )
    assert value.target_sets == 10
    assert value.categories == ["powerbank", "shopperbag", "thermomug", "pencil"]
    assert value.color.values == ["NAVY"]
    assert value.quantity.minimum == 150


@pytest.mark.asyncio
async def test_simple_planner_uses_role_scoped_concepts_and_purpose_discovery():
    planner = ResearchPlanner()
    pens = await planner.parse(
        "Нужны ручки из металла, но не те, которыми открывают двери или носят сумки. "
        "Чёрные, от 250 штук"
    )
    assert pens.categories == ["pen"]
    assert pens.quantity == 250
    assert pens.material == "металл"

    executives = await planner.parse(
        "Найди подарки для руководителей до 12000 тенге за штуку. Без пластика, "
        "без одежды, желательно металл или дерево. Не показывай обычные канцелярские ручки"
    )
    assert executives.audience == "executives"
    assert executives.budget.max == 12000
    assert set(executives.excluded_categories) >= {"pen", "tshirt"}
    assert "bag" not in executives.categories
