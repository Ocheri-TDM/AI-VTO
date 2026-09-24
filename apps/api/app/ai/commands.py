"""Explicit deterministic fallback for user commands, never regex of model output."""

import re

from app.ai.decision import AgentAction as A
from app.ai.decision import AgentDecision
from app.application.tools import SelectInput
from app.domain.intent import CategoryResolver, ColorIntentResolver
from app.domain.models import Budget, SearchIntent, SoftPreferences
from app.domain.search_state import SearchState, ViewFilters
from app.domain.services import normalize_text


def amount(text: str) -> int | None:
    match = re.search(r"(?:до|дороже|дешевле|бюджет|максимум)\s*(\d[\d ]*)(?:\s*(тыс\w*|к\b))?", text)
    if not match:
        return None
    return int(match[1].replace(" ", "")) * (1000 if match[2] else 1)


class CommandParser:
    def decide(self, message: str, snapshot=None, last_intent: dict | None = None) -> AgentDecision:
        text = normalize_text(message)
        resolver = CategoryResolver()
        categories = resolver.classify(text)
        colors = ColorIntentResolver().from_text(text).acceptable
        maximum = amount(text)
        quantity_match = re.search(
            r"(?:тираж|на|около|количество)\s*(\d+)\s*(?:человек|шт|штук)?|(\d+)\s*(?:шт|штук|человек)", text
        )
        quantity = int(quantity_match[1] or quantity_match[2]) if quantity_match else None
        preferences = SoftPreferences(
            **{
                key: any(word in text for word in words)
                for key, words in {
                    "premium": ["премиал", "руководител"],
                    "minimal": ["минимал"],
                    "technology": ["технолог", "айти", "it "],
                    "eco": ["эко"],
                    "business": ["делов", "классичес"],
                    "creative": ["креатив"],
                    "sport": ["спорт"],
                }.items()
            }
        )
        if re.search(r"обнови\w* (?:поиск|наличие|остат|подбор)|refresh", text):
            if snapshot:
                return AgentDecision(action=A.REFRESH_SEARCH, requires_refresh=True)
            if last_intent:
                return AgentDecision(
                    action=A.SEARCH,
                    intent=SearchIntent.model_validate(last_intent),
                    requires_search=True,
                    requires_refresh=True,
                )
        if snapshot:
            if quantity is not None:
                return AgentDecision(action=A.FILTER_RESULTS, filters=ViewFilters(quantity=quantity))
            if any(x in text for x in ("назад", "отмени", "верни как было", "предыдущ", "верни предыдущие")):
                return AgentDecision(action=A.UNDO)
            if "верни дорог" in text:
                return AgentDecision(action=A.RESTORE_RESULTS, restore_mode="budget")
            if "что ты убрал" in text or "убранные" in text:
                return AgentDecision(action=A.RESTORE_RESULTS, restore_mode="removed")
            if "сброс" in text or "верни все" in text:
                return AgentDecision(action=A.RESET_FILTERS)
            if re.search(r"выбер\w*|выбра\w*|сними выбор|оставь \d+ (?:вариант|лучш)", text):
                count = re.search(r"\b(\d+)\b", text)
                mode = "clear" if "убери выбран" in text or "сними выбор" in text else "replace"
                return AgentDecision(
                    action=A.SELECT_PRODUCTS,
                    selection=SelectInput(
                        count=int(count[1]) if count else 5,
                        categories=categories,
                        per_category="каждой категор" in text,
                        mode=mode,
                    ),
                    preferences=preferences,
                )
            if "дешев" in text and maximum is None or "сначала" in text and "цен" in text:
                return AgentDecision(action=A.SORT_RESULTS, sorting="price_asc")
            if "дорогие сначала" in text:
                return AgentDecision(action=A.SORT_RESULTS, sorting="price_desc")
            if maximum is not None:
                return AgentDecision(action=A.FILTER_RESULTS, filters=ViewFilters(budget=Budget(max=maximum)))
            if ("темн" in text or "темнее" in text) and any(x in text for x in ("более", "сделай", "покажи")):
                return AgentDecision(action=A.SORT_RESULTS, sorting="darker")
            if "добав" in text or "еще" in text and categories:
                state = SearchState.model_validate(snapshot.state)
                intent = snapshot.intent.model_copy(deep=True)
                intent.categories = list(
                    dict.fromkeys((state.filters.categories or intent.categories) + categories)
                )
                intent.budget = state.filters.budget
                intent.colors = state.filters.colors or []
                return AgentDecision(action=A.EXPAND_RESULTS, intent=intent, requires_search=True)
            if "убери" in text or "исключ" in text:
                return AgentDecision(
                    action=A.REMOVE_RESULTS,
                    filters=ViewFilters(categories=categories or None, colors=colors or None),
                )
            if categories or colors:
                values = {}
                if categories:
                    values["categories"] = categories
                if colors:
                    values["colors"] = colors
                return AgentDecision(action=A.FILTER_RESULTS, filters=ViewFilters(**values))
            if any(preferences.model_dump().values()):
                return AgentDecision(action=A.SORT_RESULTS, sorting="relevance", preferences=preferences)
            return AgentDecision(action=A.SHOW_RESULTS)
        if any(x in text for x in ("такие же", "такой же", "как на фото")):
            return AgentDecision(
                action=A.ASK_CLARIFICATION,
                user_response_hint="Уточните, какие товары или образец вы имеете в виду.",
            )
        quantity_match = re.search(
            r"(?:тираж|на|около)\s*(\d+)\s*(?:человек|шт|штук)?|(\d+)\s*(?:шт|штук|человек)", text
        )
        quantity = int(quantity_match[1] or quantity_match[2]) if quantity_match else None
        intent = SearchIntent(
            raw_query=message,
            quantity=quantity,
            categories=resolver.resolve(text),
            colors=colors,
            budget=Budget(max=maximum) if maximum is not None else None,
            preferences=preferences,
        )
        return AgentDecision(action=A.SEARCH, intent=intent, requires_search=True)
