from decimal import Decimal

import pytest
from conftest import make_product

from app.ai.basic import BasicIntentParser
from app.application.cache import cache_key
from app.application.planner import SearchPlanner
from app.domain.errors import CurrencyError
from app.domain.models import Budget, ColorGroup, ProductColor, SearchIntent
from app.domain.services import (
    AvailabilityService,
    CategoryMatcher,
    ColorNormalizer,
    CurrencyService,
    DeduplicationService,
)
from app.domain.taxonomy import (
    SEEDS,
    CategoryMatch,
    ProductCategoryMatcher,
    QueryVocabulary,
    UniversalCategoryResolver,
)


@pytest.mark.parametrize(
    ("amount", "currency", "expected"),
    [
        ("100.5", "KZT", 101),
        ("100.49", "KZT", 100),
        ("1", "RUB", 6),
        ("100.5", "RUB", 553),
        ("0", "KZT", 0),
        ("10000", "KZT", 10000),
    ],
)
def test_currency_rounds_half_up(amount, currency, expected):
    assert CurrencyService(Decimal("5.5")).to_kzt(Decimal(amount), currency) == expected


@pytest.mark.parametrize(
    ("amount", "currency", "rate"),
    [
        ("1", "RUB", None),
        ("1", "USD", "5"),
        ("NaN", "KZT", "5"),
        ("-1", "KZT", "5"),
        ("Infinity", "KZT", "5"),
    ],
)
def test_currency_never_invents_prices(amount, currency, rate):
    with pytest.raises(CurrencyError):
        CurrencyService(Decimal(rate) if rate else None).to_kzt(Decimal(amount), currency)


@pytest.mark.parametrize(
    ("color", "group"),
    [
        ("тёмно-синий", ColorGroup.DARK_BLUE),
        ("Темно синие", ColorGroup.DARK_BLUE),
        ("midnight blue", ColorGroup.NAVY),
        ("deep blue", ColorGroup.NAVY),
        ("Navy", ColorGroup.NAVY),
        ("светло-синий", ColorGroup.LIGHT_BLUE),
        ("синий", ColorGroup.BLUE),
        ("infrared", ColorGroup.UNKNOWN),
    ],
)
def test_color_aliases(color, group):
    normalized = ColorNormalizer().normalize(color)
    assert normalized.normalized_color == group
    assert normalized.original_color == color


def test_dark_blue_similarity_does_not_accept_generic_blue():
    normalizer = ColorNormalizer()
    assert normalizer.matches([normalizer.normalize("navy")], [ColorGroup.DARK_BLUE])
    assert not normalizer.matches([normalizer.normalize("blue")], [ColorGroup.NAVY])


@pytest.mark.parametrize(
    ("stock", "expected"), [(None, False), (0, False), (299, False), (300, True), (301, True)]
)
def test_availability_requires_numeric_variant_stock(stock, expected):
    assert AvailabilityService().accepts(make_product(stock_quantity=stock, available=True), 300) is expected


async def test_acceptance_intent_and_planner():
    intent = await BasicIntentParser().parse_intent(
        "Темно-синие термокружки, рюкзаки, ежедневники и ручки. Тираж 300 шт."
    )
    assert intent.quantity == 300
    assert intent.categories == ["thermomug", "backpack", "notebook", "pen"]
    assert intent.colors == [ColorGroup.DARK_BLUE]
    assert [task.query for task in SearchPlanner().plan(intent)] == [
        "термокружка",
        "рюкзак",
        "ежедневник",
        "ручка",
    ]


def test_related_search_results_do_not_become_requested_category():
    matcher = CategoryMatcher()
    assert not matcher.matches("Термос MARK с датчиком", ["thermomug"])
    assert matcher.matches("GRACE, дорожная чашка из стали", ["thermomug"])
    assert matcher.matches("Ежедневник недатированный", ["notebook"])


def test_multilingual_storage_and_power_concepts_are_canonical():
    resolver = UniversalCategoryResolver()
    for query in (
        "powerbank", "power bank", "Power Bank", "повербанк", "пауэрбанк",
        "павербанк", "внешний аккумулятор", "портативный аккумулятор",
    ):
        assert resolver.resolve(query) == ["powerbank"]
    for query in (
        "флешка", "флешки", "флеш-накопитель", "USB-накопитель",
        "USB flash drive", "flash drive", "USB memory stick", "флеш память",
    ):
        assert resolver.resolve(query) == ["flashdrive"]


@pytest.mark.parametrize(
    "query",
    (
        "шоппер",
        "шопер",
        "шопперов",
        "шоперов",
        "сумка-шоппер",
        "сумка-шопер",
        "shopper bag",
    ),
)
def test_shopper_spelling_and_inflection_resolve_to_one_concept(query):
    assert UniversalCategoryResolver().resolve(query) == ["shopperbag"]


def test_shopper_concept_does_not_expand_to_backpacks_or_generic_tote_bags():
    resolver = UniversalCategoryResolver()
    assert resolver.resolve("рюкзак для ноутбука") == ["backpack"]
    assert "shopperbag" not in resolver.resolve("рюкзак для ноутбука")
    assert "tote bag" not in QueryVocabulary.equivalents("shopperbag")
    assert "tote bag" in QueryVocabulary.related("shopperbag")


def test_canonical_taxonomy_ids_resolve_to_themselves():
    resolver = UniversalCategoryResolver()
    for concept in SEEDS:
        assert concept in resolver.resolve(concept)
    matcher = ProductCategoryMatcher()
    shopper = make_product(name="Shopper FUTURE", category="bag")
    backpack = make_product(name="Рюкзак для ноутбука Conttori", category="bag")
    assert matcher.match(shopper, "shopperbag", "шопер") == CategoryMatch.EXACT
    assert matcher.match(backpack, "shopperbag", "шопер") == CategoryMatch.REJECTED


def test_thermal_product_relationships_are_not_flattened():
    resolver = UniversalCategoryResolver()
    assert resolver.resolve("термокружка") == ["thermomug"]
    assert resolver.resolve("термостакан") == ["thermocup"]
    assert resolver.resolve("термос") == ["thermos"]


def test_usb_attribute_is_not_flash_storage_category_evidence():
    matcher = ProductCategoryMatcher()
    humidifier = make_product(
        name="Светодиодный USB увлажнитель Egg",
        category=None,
        metadata={"supplier_category": "Увлажнители"},
    )
    flash = make_product(
        name="Флешка Twist 32 ГБ",
        category=None,
        metadata={"supplier_category": "USB флешки"},
    )
    assert matcher.match(humidifier, "flashdrive", "флешка") == CategoryMatch.REJECTED
    assert matcher.match(flash, "flashdrive", "USB flash drive") == CategoryMatch.EXACT


def test_bounded_phrase_modifier_preserves_powerbank_precision_and_recall():
    matcher = ProductCategoryMatcher()
    powerbank = make_product(
        name="FERMAT. Портативный алюминиевый аккумулятор емкостью 7200 мАч",
        category=None,
    )
    grinder = make_product(
        name="Портативная кофемолка Полинара с аккумулятором",
        category=None,
    )
    assert matcher.match(powerbank, "powerbank", "powerbank") == CategoryMatch.EXACT
    assert matcher.match(grinder, "powerbank", "портативный аккумулятор") == CategoryMatch.REJECTED


def test_hybrid_lighter_flash_storage_is_related_instead_of_exact():
    product = make_product(
        name="Зажигалка-накопитель USB Abigail",
        category=None,
    )
    assert (
        ProductCategoryMatcher().match(product, "flashdrive", "флешка")
        == CategoryMatch.RELATED
    )


def test_multifunction_name_can_match_multiple_product_concepts():
    matcher = ProductCategoryMatcher()
    product = make_product(
        name="Металлическая ручка и вечный карандаш Van Gogh",
        category=None,
        metadata={"supplier_category": "Письменные принадлежности"},
    )
    assert matcher.match(product, "pen", "ручка") == CategoryMatch.EXACT
    assert matcher.match(product, "pencil", "карандаш") == CategoryMatch.EXACT


async def test_parser_quantity_budget_and_thousands():
    intent = await BasicIntentParser().parse_intent("Ручки 1 000 шт., до 10 000 тенге")
    assert intent.quantity == 1000
    assert intent.budget.max == 10000


def test_cache_canonicalization_and_filter_isolation():
    base = SearchIntent(raw_query="  ТЁМНО-СИНИЕ   ручки ", colors=[ColorGroup.NAVY], quantity=300)
    equivalent = base.model_copy(update={"raw_query": "тёмно-синие ручки", "colors": [ColorGroup.DARK_BLUE]})

    def key(intent):
        return cache_key(intent, ["ucontay", "oasis"], "5.5", (2, 12))

    assert key(base) == key(equivalent)
    for update in (
        {"quantity": 301},
        {"budget": Budget(max=10)},
        {"categories": ["pen"]},
        {"colors": [ColorGroup.BLUE]},
    ):
        assert key(base) != key(base.model_copy(update=update))
    assert key(base) != cache_key(base, ["oasis"], "5.5", (2, 12))
    assert key(base) != cache_key(base, ["ucontay", "oasis"], "6.0", (2, 12))


def test_deduplication_keeps_distinct_supplier_variants():
    first = make_product()
    second = make_product(id="ucontay:2", supplier="ucontay")
    red = make_product(
        id="ucontay:3",
        supplier="ucontay",
        colors=[ProductColor(original_color="red", normalized_color=ColorGroup.RED)],
    )
    result = DeduplicationService().group([first, first, second, red])
    assert len(result) == 3
    assert first.duplicate_group_id == second.duplicate_group_id is not None
    assert red.duplicate_group_id is None
