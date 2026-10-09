"""Precision-first catalog-backed query normalization."""

from __future__ import annotations

import re
import time
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select

from app.database.models import ProductConcept, SupplierOffer
from app.domain.intent import CATEGORY_TERMS
from app.domain.taxonomy import SEEDS, terms


class DecisionAction(StrEnum):
    CORRECT = "CORRECT"
    PRESERVE = "PRESERVE"
    AMBIGUOUS = "AMBIGUOUS"


class TokenRole(StrEnum):
    COMMAND = "COMMAND"
    CATEGORY = "CATEGORY"
    COLOR = "COLOR"
    QUANTITY = "QUANTITY"
    BRAND_MODEL = "BRAND_MODEL"
    ATTRIBUTE = "ATTRIBUTE"
    UNKNOWN = "UNKNOWN"


class CandidateTrace(BaseModel):
    candidate: str
    concept: str
    score: float
    edit: float = 0
    keyboard: float = 0
    phonetic: float = 0
    morphology: float = 0
    catalog: float = 0
    context: float = 0
    role_penalty: float = 0
    evidence: list[str] = Field(default_factory=list)


class CorrectionDecision(BaseModel):
    original_token: str
    candidate: str | None = None
    action: DecisionAction
    confidence: float = Field(ge=0, le=1)
    score: float = 0
    runner_up_score: float = 0
    margin: float = 0
    evidence: list[str] = Field(default_factory=list)
    token_role: TokenRole = TokenRole.UNKNOWN
    candidate_role: TokenRole | None = None
    rejection_reason: str | None = None
    candidates: list[CandidateTrace] = Field(default_factory=list)


class QueryCorrection(BaseModel):
    original: str
    replacement: str
    concept: str
    confidence: float = Field(ge=0, le=1)
    resolver: Literal["MORPHOLOGY", "FUZZY", "TRANSLITERATION", "KEYBOARD"]
    evidence: list[str] = Field(default_factory=list)


class QueryUnderstanding(BaseModel):
    original_query: str
    normalized_query: str
    search_query: str
    concepts: list[str] = Field(default_factory=list)
    corrections: list[QueryCorrection] = Field(default_factory=list)
    decisions: list[CorrectionDecision] = Field(default_factory=list)
    confidence: float = Field(default=1, ge=0, le=1)
    resolver: str = "EXACT"
    llm_used: bool = False
    llm_latency_ms: float | None = None
    llm_validation_result: str = "NOT_USED"
    latency_ms: float = 0
    version: str = "query-understanding-v2"


@dataclass(frozen=True)
class VocabularyTerm:
    value: str
    replacement: str
    concept: str
    quality: str = "NORMALIZED_CONCEPT"
    role: TokenRole = TokenRole.CATEGORY

    @property
    def trusted(self):
        return self.quality in {"CANONICAL_TAXONOMY", "SYNTAX"}


_CYRILLIC = re.compile(r"^[а-яё-]{4,}$", re.I)
_TOKEN = re.compile(r"[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё0-9-]*|\d+(?:[.,]\d+)?")
_PROTECTED = re.compile(r"[A-ZА-ЯЁ].*[A-Z0-9]|[A-Za-z]+[-0-9][A-Za-z0-9-]*|^[A-Z][a-z]+$")
_STOP = {
    "из",
    "для",
    "или",
    "и",
    "от",
    "до",
    "не",
    "без",
    "только",
    "штук",
    "штуки",
    "штука",
    "цвета",
    "цвет",
    "набор",
    "наборов",
    "комплект",
    "комплектов",
}
_COMMANDS = {"собери", "найди", "покажи", "подбери", "собрать", "найти", "показать", "подобрать"}
_COLORS = {
    "синий",
    "синего",
    "синих",
    "синие",
    "черный",
    "чёрный",
    "черного",
    "чёрного",
    "черных",
    "чёрных",
    "белый",
    "белого",
    "белых",
    "белые",
    "красный",
    "красного",
    "красных",
    "красные",
}
_L2C = str.maketrans("qwertyuiop[]asdfghjkl;'zxcvbnm,.", "йцукенгшщзхъфывапролджэячсмитьбю")
_CONF = str.maketrans(
    {
        "a": "а",
        "b": "в",
        "c": "с",
        "e": "е",
        "h": "н",
        "k": "к",
        "m": "м",
        "o": "о",
        "p": "р",
        "t": "т",
        "x": "х",
        "y": "у",
    }
)
_GRAMMAR = {
    "собери": ("собери", "собрать"),
    "найди": ("найди", "найти"),
    "покажи": ("покажи", "показать"),
    "подбери": ("подбери", "подобрать"),
    "синий": ("синий", "синего", "синих", "синие"),
    "черный": ("черный", "чёрный", "черного", "чёрного", "черных", "чёрных"),
    "белый": ("белый", "белого", "белых", "белые"),
    "красный": ("красный", "красного", "красных", "красные"),
}


def _neighbors():
    rows = ("йцукенгшщзхъ", "фывапролджэ", "ячсмитьбю")
    pos = {}
    for r, row in enumerate(rows):
        off = (0, 0.45, 0.85)[r]
        for c, ch in enumerate(row):
            pos[ch] = (r, c + off)
    return {
        ch: frozenset(
            o for o, (rr, cc) in pos.items() if o != ch and abs(r - rr) <= 1 and abs(c - cc) <= 1.15
        )
        for ch, (r, c) in pos.items()
    }


_NEIGHBORS = _neighbors()


def _clean(v):
    v = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", v).strip())
    v = re.sub(r"\bтермо[\s-]+(?=[а-яё])", "термо", v, flags=re.I)
    return re.sub(r"\bpower[\s-]+bank\b", "powerbank", v, flags=re.I)


def _stem(v):
    x = terms(v)
    return x[0] if x else v.casefold()


def _phonetic(v):
    v = re.sub(r"(.)\1+", r"\1", v.casefold().replace("ё", "е"))
    v = re.sub(r"(?:ами|ями|ого|ему|ому|ов|ев|оф|еф|ей|ах|ях|ы|и|а|я|у|ю|ом|ем|е)$", "", v)
    v = v.translate(str.maketrans({"б": "п", "в": "ф", "г": "к", "д": "т", "ж": "ш", "з": "с"}))
    return re.sub(r"[аеёиоуыэюяьъ-]", "", v)


def _translit(v):
    t = {
        "а": "a",
        "б": "b",
        "в": "v",
        "г": "g",
        "д": "d",
        "е": "e",
        "ё": "e",
        "ж": "zh",
        "з": "z",
        "и": "i",
        "й": "y",
        "к": "k",
        "л": "l",
        "м": "m",
        "н": "n",
        "о": "o",
        "п": "p",
        "р": "r",
        "с": "s",
        "т": "t",
        "у": "u",
        "ф": "f",
        "х": "kh",
        "ц": "ts",
        "ч": "ch",
        "ш": "sh",
        "щ": "sch",
        "ы": "y",
        "э": "e",
        "ю": "yu",
        "я": "ya",
        "ь": "",
        "ъ": "",
    }
    return "".join(t.get(c, c) for c in v.casefold())


def _distance(a, b, keyboard=False):
    d = [[0.0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a) + 1):
        d[i][0] = i * 0.82
    for j in range(len(b) + 1):
        d[0][j] = j * 0.82
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            sub = (
                0
                if a[i - 1] == b[j - 1]
                else 0.32
                if keyboard and b[j - 1] in _NEIGHBORS.get(a[i - 1], ())
                else 1
            )
            d[i][j] = min(d[i - 1][j] + 0.82, d[i][j - 1] + 0.82, d[i - 1][j - 1] + sub)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 0.42)
    return d[-1][-1]


def _ksim(a, b):
    return max(0.0, 1 - _distance(a, b, True) / max(len(a), len(b), 1))


def _input_role(token, index, tokens):
    low = token.casefold()
    if token[0].isdigit():
        return TokenRole.QUANTITY
    if _PROTECTED.match(token):
        return TokenRole.BRAND_MODEL
    if low in _COMMANDS or index == 0 and low.endswith(("и", "й")):
        return TokenRole.COMMAND
    if low in _COLORS or re.search(r"(?:ый|ий|ая|яя|ое|ее|ые|ие|ого|его|ых|их)$", low):
        return TokenRole.ATTRIBUTE
    if index and tokens[index - 1].casefold() in {"модель", "артикул", "sku", "бренд"}:
        return TokenRole.BRAND_MODEL
    if any(x.casefold() == "из" for x in tokens[:index]) or index and tokens[index - 1].casefold() in _COLORS:
        return TokenRole.CATEGORY
    return TokenRole.UNKNOWN


class QueryUnderstandingService:
    def __init__(self, sessions, cache_seconds=300, max_terms=4000, **_):
        self.sessions = sessions
        self.cache_seconds = cache_seconds
        self.max_terms = max_terms
        self._cached_at = 0.0
        self._vocabulary = ()
        self._by_length = {}
        self._exact = {}
        self._phonetic = {}
        self._transliterated = {}
        self._trusted_concepts: set[str] = set()
        self._protected_terms: set[str] = set()

    async def vocabulary(self):
        now = time.monotonic()
        if self._vocabulary and now - self._cached_at < self.cache_seconds:
            return self._vocabulary
        values = {}
        for replacement, labels in _GRAMMAR.items():
            role = TokenRole.COMMAND if replacement in _COMMANDS else TokenRole.COLOR
            for label in labels:
                values[(label, "_syntax")] = VocabularyTerm(label, replacement, "_syntax", "SYNTAX", role)
        for concept, labels in SEEDS.items():
            for label in labels:
                for token in _TOKEN.findall(label.casefold()):
                    if len(token) >= 3:
                        values[(token, concept)] = VocabularyTerm(
                            token, labels[1], concept, "CANONICAL_TAXONOMY"
                        )
        for concept, label in CATEGORY_TERMS.items():
            for token in _TOKEN.findall(label.casefold()):
                if len(token) >= 3:
                    values[(token, concept)] = VocabularyTerm(token, token, concept, "CANONICAL_TAXONOMY")
        async with self.sessions() as db:
            rows = (
                await db.execute(
                    select(ProductConcept.canonical_name, ProductConcept.display_name, ProductConcept.aliases)
                    .where(ProductConcept.products_count > 0)
                    .order_by(ProductConcept.products_count.desc())
                    .limit(2000)
                )
            ).all()
            payloads = (await db.scalars(select(SupplierOffer.payload).limit(2000))).all()
        protected = set()
        for payload in payloads:
            metadata = payload.get("metadata") or {}
            for value in (
                payload.get("brand"),
                metadata.get("brand"),
                metadata.get("model"),
                payload.get("article"),
            ):
                if value:
                    protected.update(
                        token.casefold() for token in _TOKEN.findall(str(value)) if len(token) >= 2
                    )
        self._protected_terms = protected
        for canonical, display, aliases in rows:
            labels = [canonical, display, *(aliases or [])]
            replacement = min(
                (x for x in _TOKEN.findall(canonical.casefold()) if len(x) >= 3),
                key=len,
                default=canonical.casefold(),
            )
            for label in labels:
                for token in _TOKEN.findall(str(label).casefold()):
                    if 3 <= len(token) <= 64 and "�" not in token:
                        values[(token, canonical)] = VocabularyTerm(token, replacement, canonical)
                    if len(values) >= self.max_terms:
                        break
        self._vocabulary = tuple(values.values())
        by = {}
        exact = {}
        phon = {}
        trans = {}
        for x in self._vocabulary:
            by.setdefault(len(x.value), []).append(x)
            exact.setdefault(x.value, []).append(x)
            if x.trusted:
                phon.setdefault(_phonetic(x.value), []).append(x)
                trans.setdefault(_translit(x.value), []).append(x)
        self._by_length = {k: tuple(v) for k, v in by.items()}
        self._exact = {k: tuple(v) for k, v in exact.items()}
        self._phonetic = {k: tuple(v) for k, v in phon.items()}
        self._transliterated = {k: tuple(v) for k, v in trans.items()}
        self._trusted_concepts = {item.concept for item in self._vocabulary if item.trusted}
        self._cached_at = now
        return self._vocabulary

    def _trace(self, token, item, role, context):
        edit = SequenceMatcher(None, token, item.value).ratio()
        keyboard = _ksim(token, item.value) if _CYRILLIC.match(token) and edit >= 0.55 else 0
        morph = float(_stem(token) == _stem(item.value))
        phon = float(_phonetic(token) == _phonetic(item.value))
        catalog = (
            1
            if item.quality == "CANONICAL_TAXONOMY" or item.concept in self._trusted_concepts
            else 0.85
            if item.quality == "NORMALIZED_CONCEPT"
            else 0.55
        )
        ctx = 0.12 if context and item.role == TokenRole.CATEGORY else 0
        penalty = 0
        if item.role == TokenRole.COMMAND and role != TokenRole.COMMAND:
            penalty = 0.28
        elif item.role == TokenRole.COLOR and role not in {TokenRole.ATTRIBUTE, TokenRole.CATEGORY}:
            penalty = 0.32
        elif role == TokenRole.BRAND_MODEL:
            penalty = 0.5
        ev = []
        if edit >= 0.72:
            ev.append("EDIT")
        if keyboard > edit + 0.015:
            ev.append("KEYBOARD")
        if phon:
            ev.append("PHONETIC")
        if morph:
            ev.append("MORPHOLOGY")
        ending_recovery = bool(
            phon
            and item.trusted
            and re.search(r"(?:\u0438|\u044b|\u043e\u0432|\u043e\u0444|\u043e\u0444\u0444)$", token)
        )
        if ending_recovery:
            ev.append("ENDING")
        ev.append("TAXONOMY" if item.quality == "CANONICAL_TAXONOMY" else "CATALOG")
        if ctx:
            ev.append("CONTEXT")
        score = max(edit, keyboard) * 0.78 + catalog * 0.12 + ctx + (0.07 if phon and edit >= 0.72 else 0)
        score = max(score, 0.985 if morph else 0)
        if ending_recovery:
            score = max(score, 0.82)
        score = max(0, min(1, score - penalty))
        return CandidateTrace(
            candidate=item.replacement,
            concept=item.concept,
            score=round(score, 4),
            edit=round(edit, 4),
            keyboard=round(keyboard, 4),
            phonetic=phon,
            morphology=morph,
            catalog=catalog,
            context=ctx,
            role_penalty=penalty,
            evidence=ev,
        )

    def _decide(self, token, index, tokens):
        low = token.casefold()
        role = _input_role(token, index, tokens)
        if token[0].isdigit() or low in _STOP or _PROTECTED.match(token) or low in self._protected_terms:
            return CorrectionDecision(
                original_token=token,
                action=DecisionAction.PRESERVE,
                confidence=1,
                token_role=role,
                rejection_reason="PROTECTED_OR_GRAMMAR",
            )
        exact = self._exact.get(low, ())
        trusted_exact = [item for item in exact if item.trusted]
        if trusted_exact:
            return CorrectionDecision(
                original_token=token,
                candidate=token,
                action=DecisionAction.PRESERVE,
                confidence=1,
                score=1,
                margin=1,
                evidence=["EXACT"],
                token_role=role,
                candidate_role=trusted_exact[0].role,
            )
        layout = low.translate(_L2C) if low.isascii() else ""
        conf = low.translate(_CONF) if not low.isascii() else ""
        pool = set(self._phonetic.get(_phonetic(low), ()))
        if low.isascii():
            pool.update(self._transliterated.get(low, ()))
            pool.update(self._exact.get(layout, ()))
        if conf:
            pool.update(self._exact.get(conf, ()))
        for n in range(max(3, len(low) - 3), len(low) + 4):
            pool.update(self._by_length.get(n, ()))
        context = (
            any(x.casefold() == "из" for x in tokens[:index])
            or index > 0
            and tokens[index - 1].casefold() in _COLORS
        )
        traces = []
        for item in pool:
            if item.value == low:
                continue
            tr = self._trace(low, item, role, context)
            if layout == item.value or conf == item.value or low.isascii() and _translit(item.value) == low:
                tr.score = 0.99
                tr.evidence.append(
                    "TRANSLITERATION" if low.isascii() and _translit(item.value) == low else "KEYBOARD"
                )
            traces.append(tr)
        grouped = {}
        for tr in traces:
            key = tr.concept if not tr.concept.startswith("_") else tr.candidate
            if key not in grouped or tr.score > grouped[key].score:
                grouped[key] = tr
        ranked = sorted(grouped.values(), key=lambda x: x.score, reverse=True)
        trusted_ranked = [trace for trace in ranked if trace.catalog >= 1]
        if trusted_ranked and trusted_ranked[0].score >= 0.72:
            ranked = trusted_ranked
        if not ranked:
            return CorrectionDecision(
                original_token=token,
                action=DecisionAction.PRESERVE,
                confidence=1,
                token_role=role,
                rejection_reason="NO_CANDIDATE",
            )
        best = ranked[0]
        second = ranked[1].score if len(ranked) > 1 else 0
        margin = best.score - second
        direct = best.score >= 0.98 and bool({"TRANSLITERATION", "KEYBOARD"} & set(best.evidence))
        if best.catalog >= 1 and "PHONETIC" in best.evidence and best.edit >= 0.75:
            threshold = 0.77
        elif best.catalog >= 1 and best.edit >= 0.8:
            threshold = 0.74
        elif best.catalog >= 0.8 and best.edit >= 0.8:
            threshold = 0.78
        else:
            threshold = 0.86 if best.catalog >= 1 else 0.93
        cross = best.role_penalty > 0
        accepted = (
            direct
            or "MORPHOLOGY" in best.evidence
            or "ENDING" in best.evidence
            and best.edit >= 0.5
            and margin >= 0.05
            or best.score >= threshold
            and margin >= (0.045 if context else 0.05)
            and bool({"EDIT", "KEYBOARD"} & set(best.evidence))
            and ("PHONETIC" not in best.evidence or best.edit >= 0.72)
            and not cross
        )
        action = (
            DecisionAction.CORRECT
            if accepted
            else DecisionAction.AMBIGUOUS
            if best.score >= threshold and margin < (0.045 if context else 0.05)
            else DecisionAction.PRESERVE
        )
        reason = (
            None
            if accepted
            else "INSUFFICIENT_MARGIN"
            if action == DecisionAction.AMBIGUOUS
            else "ROLE_CHANGE"
            if cross
            else "INSUFFICIENT_CORROBORATED_EVIDENCE"
        )
        return CorrectionDecision(
            original_token=token,
            candidate=best.candidate,
            action=action,
            confidence=(min(0.99, 0.9 + margin / 2) if accepted else max(0, 1 - best.score)),
            score=best.score,
            runner_up_score=second,
            margin=round(margin, 4),
            evidence=best.evidence,
            token_role=role,
            candidate_role=TokenRole.CATEGORY if not best.concept.startswith("_") else None,
            rejection_reason=reason,
            candidates=ranked[:5],
        )

    async def understand(self, query):
        started = time.perf_counter()
        original = query.strip()
        source = _clean(query)
        await self.vocabulary()
        tokens = [m.group(0) for m in _TOKEN.finditer(source)]
        corrections = []
        concepts = []
        decisions = []
        idx = 0

        def replace(match):
            nonlocal idx
            token = match.group(0)
            decision = self._decide(token, idx, tokens)
            idx += 1
            decisions.append(decision)
            if decision.action == DecisionAction.CORRECT and decision.candidate:
                best = decision.candidates[0]
                resolver = (
                    "TRANSLITERATION"
                    if "TRANSLITERATION" in decision.evidence
                    else "KEYBOARD"
                    if "KEYBOARD" in decision.evidence
                    else "MORPHOLOGY"
                    if "MORPHOLOGY" in decision.evidence
                    else "FUZZY"
                )
                corrections.append(
                    QueryCorrection(
                        original=token,
                        replacement=decision.candidate,
                        concept=best.concept,
                    confidence=decision.confidence,
                        resolver=resolver,
                        evidence=decision.evidence,
                    )
                )
                if not best.concept.startswith("_"):
                    concepts.append(best.concept)
                return decision.candidate
            for item in self._exact.get(token.casefold(), ()):
                if not item.concept.startswith("_"):
                    concepts.append(item.concept)
            return token

        normalized = re.sub(r"\s+", " ", _TOKEN.sub(replace, source)).strip()
        confidence = min((x.confidence for x in corrections), default=1)
        resolver = "+".join(dict.fromkeys(x.resolver for x in corrections)) or "EXACT"
        return QueryUnderstanding(
            original_query=original,
            normalized_query=normalized,
            search_query=normalized,
            concepts=list(dict.fromkeys(concepts)),
            corrections=corrections,
            decisions=decisions,
            confidence=confidence,
            resolver=resolver,
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
        )
