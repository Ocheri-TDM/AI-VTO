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
    "pencil": ("Карандаши", "карандаш", "pencil"),
    "marker": ("Маркеры", "маркер", "marker"),
    "highlighter": ("Текстовыделители", "текстовыделител", "highlighter"),
    "thermos": ("Термосы", "термос", "thermos"),
    "mug": ("Кружки", "кружк", "mug"),
    "glass": ("Стаканы", "стакан", "glass"),
    "plate": ("Тарелки", "тарелк", "plate"),
    "tshirt": ("Футболки", "футболк", "t-shirt"),
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
    "lanyard": ("Ланъярды", "ланъярд", "lanyard"),
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
        found = [n.id for n in self.nodes if any(re.search(r"\b" + re.escape(a), text) for a in n.aliases)]
        legacy = CategoryResolver().classify(text)
        if "thermos" in found:
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


class ProductCategoryMatcher:
    def __init__(self):
        self.resolver = UniversalCategoryResolver()

    def match(self, product, category: str, query: str = "") -> CategoryMatch:
        resolver = self.resolver
        if re.search(
            r"^(?:подарочн\w*\s+)?(?:набор|комплект|коробк\w*|футляр\w*|чехол|точилк\w*)\b",
            normalize_text(product.name),
        ) and category in ("pencil", "pen", "bottle"):
            return CategoryMatch.REJECTED
        categories = resolve_cached(product.name)
        # Supplier text is first-class evidence for both open and known taxonomy.
        # This keeps products indexed with an older/missing canonical category in
        # recall without weakening hard quantity, price, colour or material rules.
        requested = terms(resolver.concept(query))
        evidence = terms(
            product.name + " "
            + str(product.metadata.get("supplier_category", "")) + " "
            + str(product.metadata.get("breadcrumbs", ""))
            + " " + " ".join(product.metadata.get("product_concepts", []))
        )
        def token_match(left: str, right: str) -> bool:
            return (
                left.startswith(right)
                or right.startswith(left)
                or min(len(left), len(right)) >= 4
                and SequenceMatcher(None, left, right).ratio() >= 0.8
            )

        if requested and all(any(token_match(t, r) for t in evidence) for r in requested):
            return CategoryMatch.EXACT
        alternatives = QueryVocabulary.alternatives(requested)
        if any(
            all(any(token_match(t, r) for t in evidence) for r in alternative)
            for alternative in alternatives
        ):
            return CategoryMatch.STRONG_RELATED
        path = (
            str(product.metadata.get("breadcrumbs", ""))
            + " "
            + str(product.metadata.get("supplier_category", ""))
        )
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

    @classmethod
    def alternatives(cls, requested: list[str]) -> list[list[str]]:
        return [terms(alias) for term in requested for alias in cls.SYNONYMS.get(term, ())]


class EmbeddingProvider(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...
