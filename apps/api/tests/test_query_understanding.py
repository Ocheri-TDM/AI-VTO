import pytest

from app.domain.query_understanding import DecisionAction, QueryUnderstandingService


@pytest.mark.parametrize("query", ["тарцен", "вумква", "ценква", "шопбер", "блогнот"])
async def test_precision_first_preserves_known_false_corrections(repository, query):
    result = await QueryUnderstandingService(repository.sessions).understand(query)
    assert result.normalized_query == query
    assert result.corrections == []
    assert result.decisions[0].action in {DecisionAction.PRESERVE, DecisionAction.AMBIGUOUS}
    assert result.decisions[0].runner_up_score >= 0
    assert result.decisions[0].rejection_reason


@pytest.mark.parametrize(
    ("query", "normalized", "concept"),
    [
        ("корондаш", "карандаш", "pencil"),
        ("карондаш", "карандаш", "pencil"),
        ("карандаж", "карандаш", "pencil"),
        ("крандаш", "карандаш", "pencil"),
        ("крушка", "кружка", "mug"),
        ("рюкзаг", "рюкзак", "backpack"),
        ("фудболка", "футболк", "tshirt"),
    ],
)
async def test_catalog_backed_typo_resolution(repository, query, normalized, concept):
    result = await QueryUnderstandingService(repository.sessions).understand(query)
    assert normalized in result.normalized_query
    assert concept in result.concepts
    assert result.llm_used is False


@pytest.mark.parametrize("query", ["Parker", "Parker Jotter", "Blackie 3D", "Powerbank", "USB-C"])
async def test_named_terms_are_not_rewritten(repository, query):
    result = await QueryUnderstandingService(repository.sessions).understand(query)
    assert result.original_query == query
    assert result.normalized_query == query
    assert result.corrections == []


async def test_numbers_negation_and_original_query_survive(repository):
    query = "найди синие корондаши от 250 штук без футляра"
    result = await QueryUnderstandingService(repository.sessions).understand(query)
    assert result.original_query == query
    assert "250" in result.normalized_query
    assert "без" in result.normalized_query
    assert "карандаш" in result.normalized_query


async def test_regression_corpus_has_at_least_150_general_mutations(repository):
    service = QueryUnderstandingService(repository.sessions)
    bases = {
        "карандаш": "pencil",
        "рюкзак": "backpack",
        "футболка": "tshirt",
        "термокружка": "thermomug",
        "зонт": "umbrella",
    }
    cases = []
    substitutions = "абвгдежзиклмнопрстуфхцчш"
    for word, concept in bases.items():
        cases.extend([(word, concept), (f"синий {word} 250 шт", concept)])
        for index in range(len(word)):
            replacement = substitutions[index % len(substitutions)]
            if replacement == word[index]:
                replacement = substitutions[(index + 1) % len(substitutions)]
            cases.append((word[:index] + replacement + word[index + 1 :], concept))
            cases.append((word[:index] + replacement + word[index:], concept))
            if index + 1 < len(word):
                cases.append((word[:index] + word[index + 1] + word[index] + word[index + 2 :], concept))
            cases.append((word[:index] + word[index + 1 :], concept))
    assert len(cases) >= 150
    passed = 0
    for query, concept in cases:
        result = await service.understand(query)
        passed += concept in result.concepts
    # The corpus deliberately includes ambiguous very-short deletions. It is
    # safer to leave those unchanged than to force a wrong correction.
    assert passed / len(cases) >= 0.80


@pytest.mark.parametrize(
    ("query", "concept"),
    [
        ("корондаш", "pencil"), ("карондаш", "pencil"),
        ("карандаж", "pencil"), ("карандашы", "pencil"),
        ("крандаш", "pencil"), ("ручька", "pen"), ("рючка", "pen"),
        ("крушка", "mug"), ("кружек", "mug"),
        ("зонтик", "umbrella"), ("зонтоф", "umbrella"),
        ("рюкзаг", "backpack"), ("рюкзкоф", "backpack"),
        ("фудболка", "tshirt"), ("футболак", "tshirt"),
        ("насоки", "socks"), ("наскофф", "socks"),
        ("даждевик", "raincoat"), ("дождевек", "raincoat"),
        ("karandash", "pencil"), ("ryukzak", "backpack"), ("zont", "umbrella"),
        ("pучка", "pen"), ("каpандаш", "pencil"),
    ],
)
async def test_required_language_cases(repository, query, concept):
    result = await QueryUnderstandingService(repository.sessions).understand(query)
    assert concept in result.concepts


@pytest.mark.parametrize("query", ["термо кружка", "термо-кружка", "термокружка"])
async def test_multiword_separator_normalization(repository, query):
    result = await QueryUnderstandingService(repository.sessions).understand(query)
    assert "thermomug" in result.concepts


@pytest.mark.parametrize(("query", "quantity"), [
    ("карандаши 250шт", 250), ("карандаши 250 штук", 250),
    ("карандаши от 250", 250), ("карандаши не меньше 250", 250),
    ("карандаши минимум 250", 250), ("карандаши >=250", 250),
])
async def test_quantity_forms_are_deterministic(query, quantity):
    from app.ai.basic import BasicIntentParser

    intent = await BasicIntentParser().parse_intent(query)
    assert intent.quantity == quantity


async def test_negated_color_is_not_required():
    from app.ai.basic import BasicIntentParser
    from app.domain.models import ColorGroup

    intent = await BasicIntentParser().parse_intent("ручки не красные")
    assert ColorGroup.RED not in intent.colors
    assert ColorGroup.RED in intent.excluded_colors


async def test_unknown_catalog_term_is_not_arbitrarily_rewritten(repository):
    result = await QueryUnderstandingService(repository.sessions).understand("квантосборник")
    assert result.normalized_query == "квантосборник"
    assert result.corrections == []
