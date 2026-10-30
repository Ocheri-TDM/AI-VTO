"""Deterministic routing of conversational search follow-ups."""

from __future__ import annotations

import re
import time
from enum import StrEnum

from pydantic import BaseModel, Field


class ConversationAction(StrEnum):
    NEW_SEARCH = "NEW_SEARCH"
    REFINE_CURRENT = "REFINE_CURRENT"
    MODIFY_CURRENT = "MODIFY_CURRENT"
    EXPAND_CURRENT = "EXPAND_CURRENT"
    REMOVE_CONSTRAINT = "REMOVE_CONSTRAINT"
    RESET_FILTERS = "RESET_FILTERS"
    COMMAND = "COMMAND"


class ConversationRoute(BaseModel):
    action: ConversationAction
    confidence: float = Field(ge=0, le=1)
    original_message: str
    normalized_message: str
    current_subjects: list[str] = Field(default_factory=list)
    detected_subjects: list[str] = Field(default_factory=list)
    signals: list[str] = Field(default_factory=list)
    reason: str
    llm_used: bool = False
    latency_ms: float = 0


_RESET = re.compile(r"\b(?:сброс(?:ь|ить)?|покажи\s+вс[её]|без\s+фильтров|исходн\w*\s+выбор)\b", re.I)
_COMMAND = re.compile(r"\b(?:назад|отмени|верни|выбери|похож\w*|обнови|сначала\s+(?:дешев|дорог))\b", re.I)
_EXPAND = re.compile(r"\b(?:д[ао]бав\w*|ещ[её]|также|плюс|расширь|продолжи)\b", re.I)
_REMOVE = re.compile(r"\b(?:уб[еёи]р\w*|исключи|без|не\s+показывай|сними)\b", re.I)
_MODIFY = re.compile(r"\b(?:замени|поменяй|сделай|теперь|вместо|измени)\b", re.I)
_REFINE = re.compile(r"\b(?:оставь|только|покажи|нужны|давай|строго|до|от|дешевле|дороже|в\s+наличии)\b", re.I)
_NEW = re.compile(r"\b(?:новый\s+поиск|начни\s+заново|найди|ищу|подбери)\b", re.I)
_STANDALONE_SEARCH = re.compile(
    r"^\s*(?:(?:а\s+)?теперь\s+)?(?:покажи|найди|ищи|подбери)\s+(?:мне\s+)?(?:только\s+)?",
    re.I,
)
_ATTRIBUTE = re.compile(
    r"\b(?:син\w*|голуб\w*|ч[её]рн\w*|бел\w*|красн\w*|зел[её]н\w*|желт\w*|"
    r"сер\w*|розов\w*|фиолет\w*|оранж\w*|металл\w*|дерев\w*|пластик\w*|"
    r"кож\w*|хлоп\w*|наличи\w*|склад\w*|\d+[\s\u00a0]*(?:₽|руб|₸|тг|шт))\b",
    re.I,
)
_CLEAR_CONSTRAINT = re.compile(
    r"(?:количество|тираж)(?:\s+т[оа]вар\w*)?(?:\s+больше)?\s+не\s+важ"
    r"|(?:цвет|материал|цена|бюджет)(?:\s+\w+){0,3}\s+не\s+важ"
    r"|без\s+ограничени\w*\s+по\s+(?:количеству|тиражу|цвету|материалу|цене|бюджету)",
    re.I,
)


class ConversationQueryRouter:
    """Classify against the active intent. Absence of a new subject preserves it."""

    def __init__(self, query_understanding=None):
        self.query_understanding = query_understanding

    async def route(
        self,
        message: str,
        current_subjects: list[str] | None = None,
        current_mode: str | None = None,
    ) -> ConversationRoute:
        started = time.perf_counter()
        current = list(dict.fromkeys(current_subjects or []))
        understanding = await self.query_understanding.understand(message) if self.query_understanding else None
        normalized = understanding.search_query if understanding else " ".join(message.split())
        detected = [x for x in (understanding.concepts if understanding else []) if not x.startswith("_")]
        detected = list(dict.fromkeys(detected))
        signals: list[str] = []

        def result(action: ConversationAction, confidence: float, reason: str) -> ConversationRoute:
            return ConversationRoute(
                action=action, confidence=confidence, original_message=message,
                normalized_message=normalized, current_subjects=current,
                detected_subjects=detected, signals=signals, reason=reason,
                latency_ms=round((time.perf_counter() - started) * 1000, 2),
            )

        if not current:
            return result(ConversationAction.NEW_SEARCH, 1.0, "No active subject")
        if _RESET.search(normalized):
            signals.append("reset")
            return result(ConversationAction.RESET_FILTERS, .99, "Explicit reset")
        if _COMMAND.search(normalized):
            signals.append("command")
            return result(ConversationAction.COMMAND, .98, "Workspace command")
        if _CLEAR_CONSTRAINT.search(normalized) or _CLEAR_CONSTRAINT.search(message):
            signals.append("clear-constraint")
            return result(ConversationAction.REMOVE_CONSTRAINT, .98, "Explicit constraint removal")
        new_subjects = [value for value in detected if value not in current]
        existing_subjects = [value for value in detected if value in current]
        standalone = _STANDALONE_SEARCH.search(normalized) or _STANDALONE_SEARCH.search(message)
        explicit_standalone = bool(
            re.search(r"\b(?:найди|ищи|подбери)\b|\bпокажи\s+(?:мне\s+)?только\b", message, re.I)
        )
        if detected and standalone and (
            explicit_standalone
            or bool(new_subjects)
            or current_mode == "HARD" and len(current) > 1
        ):
            signals.append("standalone-search")
            return result(ConversationAction.NEW_SEARCH, .98, "Explicit standalone product search")
        # A standalone search may legitimately contain exclusions (for example,
        # "найди подарки ... без пластика").  The search verb defines
        # the workspace action; the exclusion belongs to the new intent.  Only
        # treat removal words as a patch after ruling out that stronger signal.
        if _REMOVE.search(normalized) or _REMOVE.search(message):
            signals.append("remove")
            return result(ConversationAction.REMOVE_CONSTRAINT, .97, "Explicit removal")
        if _EXPAND.search(normalized) or _EXPAND.search(message):
            signals.append("expand")
            return result(ConversationAction.EXPAND_CURRENT, .96, "Explicit expansion")
        if _MODIFY.search(normalized):
            signals.append("modify")
            return result(ConversationAction.MODIFY_CURRENT, .96, "Explicit modification")
        if len(detected) >= 2 and new_subjects:
            signals.append("multiple-product-subjects")
            return result(ConversationAction.NEW_SEARCH, .98, "Independent multi-product request")
        if _NEW.search(normalized) and new_subjects:
            signals.extend(["new-search-verb", "new-subject"])
            return result(ConversationAction.NEW_SEARCH, .98, "Explicit search for a new subject")
        if new_subjects and not existing_subjects and not _ATTRIBUTE.search(normalized):
            signals.append("new-subject")
            return result(ConversationAction.NEW_SEARCH, .93, "Independent product subject")
        if detected and not any(
            pattern.search(normalized)
            for pattern in (_RESET, _COMMAND, _EXPAND, _REMOVE, _MODIFY, _REFINE, _NEW, _ATTRIBUTE)
        ):
            signals.append("bare-subject")
            return result(ConversationAction.NEW_SEARCH, .92, "Standalone product subject")
        if not detected and re.fullmatch(r"[a-zа-яё][\w-]{2,}", normalized, re.I):
            signals.append("bare-open-vocabulary-subject")
            return result(ConversationAction.NEW_SEARCH, .88, "Standalone unknown product subject")
        if _REFINE.search(normalized) or _ATTRIBUTE.search(normalized) or not detected:
            signals.append("constraint-or-anaphora")
            return result(ConversationAction.REFINE_CURRENT, .95, "Constraint applies to active subject")
        return result(ConversationAction.REFINE_CURRENT, .80, "Conservative context preservation")
