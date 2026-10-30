from types import SimpleNamespace

import pytest

from app.domain.conversation_router import ConversationAction, ConversationQueryRouter


class Understanding:
    async def understand(self, text):
        concepts = []
        lowered = text.casefold()
        for token, concept in {
            "карандаш": "pencil", "круж": "mug", "руч": "pen", "зонт": "umbrella",
            "термокруж": "thermomug", "рюкзак": "backpack", "usb": "flashdrive",
        }.items():
            if token in lowered:
                concepts.append(concept)
        return SimpleNamespace(search_query=text, concepts=concepts)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message", "current", "expected"),
    [
        ("оставь синие", ["pencil"], ConversationAction.REFINE_CURRENT),
        ("только металлические", ["pen"], ConversationAction.REFINE_CURRENT),
        ("до 500 тенге", ["mug"], ConversationAction.REFINE_CURRENT),
        ("убери красные", ["umbrella"], ConversationAction.REMOVE_CONSTRAINT),
        ("покажи всё", ["pencil"], ConversationAction.RESET_FILTERS),
        ("отмени", ["pencil"], ConversationAction.COMMAND),
        ("добавь кружки", ["pencil"], ConversationAction.EXPAND_CURRENT),
        ("теперь кружки", ["pencil"], ConversationAction.MODIFY_CURRENT),
        ("найди кружки", ["pencil"], ConversationAction.NEW_SEARCH),
        ("кружки", ["pencil"], ConversationAction.NEW_SEARCH),
        ("синие", [], ConversationAction.NEW_SEARCH),
    ],
)
async def test_context_aware_route(message, current, expected):
    route = await ConversationQueryRouter(Understanding()).route(message, current)
    assert route.action == expected
    assert route.llm_used is False


@pytest.mark.asyncio
async def test_existing_subject_with_constraint_is_not_new_search():
    route = await ConversationQueryRouter(Understanding()).route(
        "покажи синие карандаши", ["pencil"]
    )
    assert route.action == ConversationAction.REFINE_CURRENT


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("количество товара больше не важно", ConversationAction.REMOVE_CONSTRAINT),
        ("дабавь черные рюкзаки", ConversationAction.EXPAND_CURRENT),
    ],
)
async def test_composite_clear_and_typo_add_preserve_active_workspace(message, expected):
    route = await ConversationQueryRouter().route(message, ["umbrella", "pen", "bottle", "tshirt"])
    assert route.action == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message",
    ["покажи только термокружку черную", "теперь ищи USB-накопители"],
)
async def test_composite_can_start_an_independent_simple_search(message):
    route = await ConversationQueryRouter(Understanding()).route(
        message, ["thermomug", "notebook", "pen"], "HARD"
    )
    assert route.action == ConversationAction.NEW_SEARCH


@pytest.mark.asyncio
async def test_bare_equivalent_subject_is_a_new_search_not_a_refinement():
    route = await ConversationQueryRouter(Understanding()).route(
        "USB-накопитель", ["flashdrive"], "STANDARD"
    )
    assert route.action == ConversationAction.NEW_SEARCH


@pytest.mark.asyncio
async def test_multi_product_request_replaces_active_simple_workspace():
    route = await ConversationQueryRouter(Understanding()).route(
        "Собери 10 наборов из зонтов и ручек черного цвета",
        ["backpack"],
        "STANDARD",
    )
    assert route.action == ConversationAction.NEW_SEARCH


@pytest.mark.asyncio
async def test_explicit_position_mutation_wins_over_noisy_detected_concepts():
    route = await ConversationQueryRouter(Understanding()).route(
        "ручки сделай синими",
        ["thermomug", "notebook", "pen"],
        "HARD",
    )
    assert route.action == ConversationAction.MODIFY_CURRENT


@pytest.mark.asyncio
async def test_bare_unknown_product_term_does_not_inherit_active_subject():
    route = await ConversationQueryRouter(Understanding()).route(
        "квантосборник", ["notebook"], "STANDARD"
    )
    assert route.action == ConversationAction.NEW_SEARCH


@pytest.mark.asyncio
async def test_standalone_purpose_search_with_exclusions_replaces_composite():
    route = await ConversationQueryRouter(Understanding()).route(
        "Найди подарки для руководителей до 12000 тенге. "
        "Без пластика, без одежды, не показывай ручки.",
        ["tshirt", "powerbank", "pen"],
        "HARD",
    )
    assert route.action == ConversationAction.NEW_SEARCH
    assert "standalone-search" in route.signals
