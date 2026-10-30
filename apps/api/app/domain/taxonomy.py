"""Open taxonomy. Seed nodes aid resolution but never gate text retrieval."""

import hashlib
import re
from difflib import SequenceMatcher
from enum import StrEnum
from functools import lru_cache
from typing import Protocol

from pydantic import BaseModel, Field

from app.domain.intent import CategoryResolver
from app.domain.services import normalize_text


class ProductTaxonomyNode(BaseModel):
    id: str
    canonical_name: str
    display_name_ru: str
    aliases: list[str] = Field(default_factory=list)
    parent_id: str | None = None
    supplier_mappings: dict[str, list[str]] = Field(default_factory=dict)


SEEDS = {
    "powerbank": (
        "Внешние аккумуляторы",
        "powerbank",
        "power bank",
        "повербанк",
        "пауэрбанк",
        "павербанк",
        "внешний аккумулятор",
        "портативный аккумулятор",
    ),
    "flashdrive": (
        "USB-накопители",
        "флешка",
        "флешки",
        "флеш-накопитель",
        "usb-накопитель",
        "usb flash drive",
        "flash drive",
        "usb memory stick",
        "флеш память",
    ),
    "thermomug": (
        "Термокружки",
        "термокружка",
        "термокружки",
        "travel mug",
        "thermal mug",
        "insulated mug",
    ),
    "thermocup": (
        "Термостаканы",
        "термостакан",
        "термостаканы",
        "thermal tumbler",
        "insulated tumbler",
    ),
    "pencil": ("Карандаши", "карандаш", "pencil"),
    "marker": ("Маркеры", "маркер", "marker"),
    "highlighter": ("Текстовыделители", "текстовыделител", "highlighter"),
    "thermos": ("Термосы", "термос", "thermos"),
    "shopperbag": (
        "Сумки-шопперы",
        "шоппер",
        "сумка-шоппер",
        "shopper",
        "shopper bag",
    ),
    "mug": ("Кружки", "кружка", "кружк", "кружек", "mug"),
    "glass": ("Стаканы", "стакан", "glass"),
    "plate": ("Тарелки", "тарелк", "plate"),
    "tshirt": ("Футболки", "футболк", "футбол", "t-shirt"),
    "raincoat": ("Дождевики", "дождевик", "плащ дождевик", "raincoat"),
    "socks": ("Носки", "носок", "носки", "носк", "sock"),
    "hoodie": ("Худи", "худи", "hoodie"),
    "blanket": ("Пледы", "плед", "blanket"),
    "clock": ("Часы", "часы", "clock"),
    "speaker": ("Колонки", "колонк", "speaker"),
    "headphones": ("Наушники", "наушник", "headphone"),
    "flashlight": ("Фонарики", "фонар", "flashlight"),
    "suitcase": ("Чемоданы", "чемодан", "suitcase"),
    "wallet": ("Кошельки", "кошел", "wallet"),
    "cardholder": ("Визитницы", "визитниц", "cardholder"),
    "organizer": ("Органайзеры", "органайзер", "organizer"),
    "lanyard": ("Ланьярды", "ланьярд", "lanyard"),
}


class Expansion(BaseModel):
    query: str
    origin: str = "taxonomy"
    reason: str = "Alias of the requested product concept"
    confidence: float = Field(default=1, ge=0, le=1)


def terms(text: str) -> list[str]:
    words = re.findall(r"[а-яёa-z][а-яёa-z0-9]+", normalize_text(text).replace("-", " "))
    stop = {"для", "или", "это", "мне", "нужен", "нужны", "найди", "покажи", "подбери", "штук"}
    stems = []
    for word in words:
        if word in stop:
            continue
        stem = re.sub(
            r"(?:иями|ями|ами|ого|ему|ому|ыми|ими|ией|ов|ев|ей|ах|ях|ы|и|а|я|у|ю|ом|ем|ой)$",
            "", word,
        ) if len(word) > 4 else word
        stems.append(stem or word)
    return list(dict.fromkeys(stems))


class UniversalCategoryResolver:
    def __init__(self, nodes: list[ProductTaxonomyNode] | None = None):
        self.nodes = nodes or [
            ProductTaxonomyNode(id=k, canonical_name=k, display_name_ru=v[0], aliases=list(v[1:]))
            for k, v in SEEDS.items()
        ]

    def resolve(self, phrase: str) -> list[str]:
        text = normalize_text(phrase)
        found = [
            n.id
            for n in self.nodes
            if text == normalize_text(n.id)
            or any(re.search(r"\b" + re.escape(a), text) for a in n.aliases)
        ]
        legacy = CategoryResolver().classify(text)
        if "shopperbag" in found or "shopperbag" in legacy:
            found = [c for c in found if c != "bag"]
            legacy = [c for c in legacy if c != "bag"]
        if "thermocup" in found:
            found = [c for c in found if c not in ("thermos", "mug", "glass")]
            legacy = [c for c in legacy if c != "thermomug"]
        elif "thermos" in found:
            legacy = [c for c in legacy if c != "thermomug"]
        if "thermomug" in legacy:
            found = [c for c in found if c not in ("mug", "glass")]
        return list(dict.fromkeys(found + legacy))

    def concept(self, phrase: str) -> str:
        value = re.sub(r"\d[\d\s]*(?:шт\.?|штук|человек)?", " ", normalize_text(phrase))
        value = re.sub(r"\b(?:нужны|нужен|найди|подбери|покажи|пожалуйста)\b", "", value).strip(" .,")
        value = re.sub(
            r"\b(?:только\s+(?:в\s+)?наличии|можно\s+подождать|до\s+тенге|"
            r"темно[- ]?син\w*|син\w*|черн\w*|бел\w*|красн\w*|"
            r"металлическ\w*|деревянн\w*|пластиков\w*|хлопков\w*)\b",
            " ", value,
        )
        value = " ".join(value.split()).strip(" .,")
        return value or normalize_text(phrase)

    def dynamic_id(self, phrase: str) -> str:
        return "dynamic:" + hashlib.sha256(self.concept(phrase).encode()).hexdigest()[:20]


class CategoryMatch(StrEnum):
    EXACT = "EXACT"
    STRONG_RELATED = "STRONG_RELATED"
    RELATED = "RELATED"
    EXPLORATORY = "EXPLORATORY"
    REJECTED = "REJECTED"


@lru_cache(maxsize=50000)
def resolve_cached(value: str) -> tuple[str, ...]:
    return tuple(UniversalCategoryResolver().resolve(value))


@lru_cache(maxsize=1024)
def requested_terms_cached(query: str) -> tuple[tuple[str, ...], tuple[tuple[str, ...], ...]]:
    """Cache immutable query evidence shared by every candidate in a pool."""
    requested = tuple(terms(UniversalCategoryResolver().concept(query)))
    alternatives = tuple(tuple(value) for value in QueryVocabulary.alternatives(list(requested)))
    return requested, alternatives


@lru_cache(maxsize=50000)
def product_evidence_terms_cached(
    name: str, supplier_category: str, breadcrumbs: str, concepts: tuple[str, ...]
) -> tuple[str, ...]:
    """Normalize stable catalog evidence once across requests and positions."""
    return tuple(terms(f"{name} {supplier_category} {breadcrumbs} {' '.join(concepts)}"))


class ProductCategoryMatcher:
    def __init__(self):
        self.resolver = UniversalCategoryResolver()

    def match(self, product, category: str, query: str = "") -> CategoryMatch:
        if category == 'pen' and re.search(
            r'(?:круж|стакан|сумк|двер|рюкзак).{0,90}ручк|ручк.{0,35}(?:круж|двер|сумк)',
            product.name, re.I,
        ) and not re.search(r'шариков|пишущ|роллер|ballpoint|writing pen|вечн\w*\s+карандаш', product.name, re.I):
            return CategoryMatch.REJECTED
        packaging = re.search(
            r"^(?:подарочн\w*\s+)?(?:набор|комплект|коробк\w*|футляр\w*|чехол|точилк\w*)\b",
            normalize_text(product.name),
        )
        if packaging and category in ("pencil", "pen", "bottle"):
            return CategoryMatch.REJECTED
        # Supplier text is first-class evidence for both open and known taxonomy.
        # This keeps products indexed with an older/missing canonical category in
        # recall without weakening hard quantity, price, colour or material rules.
        requested, alternatives = requested_terms_cached(query)
        evidence = product_evidence_terms_cached(
            product.name,
            str(product.metadata.get("supplier_category", "")),
            str(product.metadata.get("breadcrumbs", "")),
            tuple(product.metadata.get("product_concepts", [])),
        )
        def token_match(left: str, right: str) -> bool:
            return (
                left.startswith(right)
                or right.startswith(left)
                or min(len(left), len(right)) >= 4
                and SequenceMatcher(None, left, right).ratio() >= 0.8
            )

        def phrase_matches(phrase: str) -> bool:
            wanted = terms(phrase)
            if not wanted:
                return False
            for start, value in enumerate(evidence):
                if not token_match(value, wanted[0]):
                    continue
                position = start
                for target in wanted[1:]:
                    following = next(
                        (
                            candidate
                            for candidate in range(position + 1, min(position + 3, len(evidence)))
                            if token_match(evidence[candidate], target)
                        ),
                        None,
                    )
                    if following is None:
                        break
                    position = following
                else:
                    return True
            return False

        exact_forms = QueryVocabulary.equivalents(category)
        strong_forms = QueryVocabulary.strong_related(category)
        related_forms = QueryVocabulary.related(category)
        if category == "flashdrive" and (
            packaging
            or re.search(
                r"\b(?:showcase|образц\w*|зажигалк\w*|чип\w*\s+памят\w*\s+для)\b",
                normalize_text(product.name),
            )
        ):
            return CategoryMatch.RELATED
        if exact_forms and any(phrase_matches(form) for form in exact_forms):
            return CategoryMatch.EXACT
        if strong_forms and any(phrase_matches(form) for form in strong_forms):
            return CategoryMatch.STRONG_RELATED
        if related_forms and any(phrase_matches(form) for form in related_forms):
            return CategoryMatch.RELATED
        if product.category == category and category not in QueryVocabulary.EQUIVALENTS:
            return CategoryMatch.EXACT

        if (
            not exact_forms
            and requested
            and all(any(token_match(t, r) for t in evidence) for r in requested)
        ):
            return CategoryMatch.EXACT
        if not exact_forms and any(
            all(any(token_match(t, r) for t in evidence) for r in alternative)
            for alternative in alternatives
        ):
            return CategoryMatch.STRONG_RELATED
        path = (
            str(product.metadata.get("breadcrumbs", ""))
            + " "
            + str(product.metadata.get("supplier_category", ""))
        )
        # Name taxonomy resolution is only needed by the fallback. Performing
        # it eagerly dominated cold full-pool Composite filtering even when
        # direct catalog evidence had already accepted the product.
        categories = resolve_cached(product.name)
        if category in resolve_cached(path) and not categories:
            return CategoryMatch.EXACT
        return CategoryMatch.REJECTED


class QueryVocabulary:
    """Seed synonyms aid ranking; catalog evidence remains the open membership source."""

    SYNONYMS = {
        "powerbank": ("пауэрбанк", "павербанк", "внешний аккумулятор"),
        "пауэрбанк": ("powerbank", "павербанк", "внешний аккумулятор"),
        "шоппер": ("сумка для покупок",),
        "кардхолдер": ("картхолдер", "футляр для карт"),
        "картхолдер": ("кардхолдер", "футляр для карт"),
        "флешк": ("usb flash", "usb-накопитель"),
        "худи": ("толстовка с капюшоном",),
    }

    EQUIVALENTS = {
        "powerbank": SEEDS["powerbank"][1:],
        "flashdrive": SEEDS["flashdrive"][1:],
        "thermomug": SEEDS["thermomug"][1:],
        "thermocup": SEEDS["thermocup"][1:],
        "thermos": SEEDS["thermos"][1:],
        "shopperbag": SEEDS["shopperbag"][1:],
    }
    STRONG_RELATED = {
        "thermomug": SEEDS["thermocup"][1:],
        "thermocup": SEEDS["thermomug"][1:],
    }
    RELATED = {
        "thermomug": SEEDS["thermos"][1:],
        "thermocup": SEEDS["thermos"][1:],
        "thermos": (*SEEDS["thermomug"][1:], *SEEDS["thermocup"][1:]),
        "shopperbag": ("tote bag", "сумка для покупок"),
    }

    @classmethod
    def equivalents(cls, concept: str) -> tuple[str, ...]:
        return tuple(cls.EQUIVALENTS.get(concept, ()))

    @classmethod
    def strong_related(cls, concept: str) -> tuple[str, ...]:
        return tuple(cls.STRONG_RELATED.get(concept, ()))

    @classmethod
    def related(cls, concept: str) -> tuple[str, ...]:
        return tuple(cls.RELATED.get(concept, ()))

    @classmethod
    def alternatives(cls, requested: list[str]) -> list[list[str]]:
        return [terms(alias) for term in requested for alias in cls.SYNONYMS.get(term, ())]


class EmbeddingProvider(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...
