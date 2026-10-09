from types import SimpleNamespace

import pytest

from app.domain.conversation_router import ConversationAction, ConversationQueryRouter


class Understanding:
    async def understand(self, text):
        concepts = []
        lowered = text.casefold()
        for token, concept in {
            "карандаш": "pencil", "круж": "mug", "ручк": "pen", "зонт": "umbrella"
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
