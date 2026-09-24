"""Extensible dictionaries and deterministic interpretation of structured intent."""

import re
from typing import Protocol

from pydantic import BaseModel, Field

from .models import ColorGroup, ProductColor
from .services import CategoryMatcher, ColorNormalizer, normalize_text

CATEGORY_TERMS = {
    "thermomug": "термокружка",
    "backpack": "рюкзак",
    "notebook": "ежедневник",
    "pen": "ручка",
    "bottle": "бутылка",
    "powerbank": "внешний аккумулятор",
    "charger": "зарядное устройство",
    "cable": "кабель",
    "umbrella": "зонт",
    "bag": "сумка",
    "lunchbox": "ланчбокс",
    "accessory": "брелок",
}


class CategoryResolver(CategoryMatcher):
    patterns = {
        **CategoryMatcher.patterns,
        "thermomug": CategoryMatcher.patterns["thermomug"] + r"|термос\w*|кружк\w* с крышк\w*",
        "notebook": CategoryMatcher.patterns["notebook"] + r"|записн\w* книжк\w*",
        "bottle": r"бутыл\w*|\bbottle\w*",
        "powerbank": r"пауэрбанк\w*|power\s?bank\w*|внешн\w* аккумулятор\w*",
        "charger": r"зарядн\w* (?:устройств|станци)\w*|\bзарядк\w*|\bcharger\w*",
        "cable": r"кабел\w*|\bcable\w*",
        "umbrella": r"\bзонт\w*|umbrella\w*",
        "bag": r"\bсумк\w*|\bшоппер\w*|\bbag\w*",
        "lunchbox": r"ланч[ -]?бокс\w*|контейнер\w* для ед\w*|lunch\s?box",
        "accessory": r"брелок\w*|брелк\w*|keyring\w*|keychain\w*",
    }

    def classify(self, name: str) -> list[str]:
        categories = super().classify(name)
        text = normalize_text(name)
        # Search engines return packaging/accessories mentioning the target item.
        if re.search(r"\b(?:коробк\w*|чех\w*|держател\w*|крышк\w*|упаковк\w*|пакет\w*|мешоч\w*)\b.*бутыл", text):
            categories = [c for c in categories if c != "bottle"]
        if re.search(r'^(?:подарочн\w*\s+)?(?:набор|комплект)\b', text):
            categories = [c for c in categories if c != 'bottle']
        return categories

    def resolve(self, text: str, semantic: list[str] | None = None) -> list[str]:
        explicit = self.classify(text)
        if explicit:
            return explicit
        valid = [x.lower() for x in semantic or [] if x.lower() in self.patterns]
        if valid:
            return list(dict.fromkeys(valid))
        text = normalize_text(text)
        if re.search(r"\bit\b|айти|технолог|конференц", text):
            return ["powerbank", "charger", "bottle", "notebook"]
        if re.search(r"руководител|премиал|делов", text):
            return ["pen", "notebook", "thermomug"]
        return ["pen", "accessory"] if "небольш" in text else ["backpack", "thermomug", "pen", "notebook"]

    def query(self, category: str) -> str:
        if category not in CATEGORY_TERMS:
            raise ValueError("Unsupported category")
        return CATEGORY_TERMS[category]


class ColorIntent(BaseModel):
    primary: list[ColorGroup] = Field(default_factory=list)
    acceptable: list[ColorGroup] = Field(default_factory=list)
    excluded: list[ColorGroup] = Field(default_factory=list)
    similarity_tolerance: float = Field(default=0.25, ge=0, le=1)


class ColorIntentResolver:
    def resolve(
        self, colors: list[ColorGroup], excluded: list[ColorGroup] | None = None, *, strict: bool = False
    ) -> ColorIntent:
        acceptable = set(colors)
        if not strict:
            if ColorGroup.BLUE in acceptable:
                acceptable.update([ColorGroup.NAVY, ColorGroup.DARK_BLUE, ColorGroup.LIGHT_BLUE])
            if acceptable & {ColorGroup.NAVY, ColorGroup.DARK_BLUE}:
                acceptable.update([ColorGroup.NAVY, ColorGroup.DARK_BLUE])
        acceptable.difference_update(excluded or [])
        return ColorIntent(
            primary=colors,
            acceptable=sorted(acceptable),
            excluded=excluded or [],
            similarity_tolerance=0 if strict else 0.25,
        )

    def from_text(self, text: str) -> ColorIntent:
        colors = ColorNormalizer().extract(text)
        if re.search(r"темн\w* син\w*", normalize_text(text)):
            colors = [ColorGroup.DARK_BLUE]
        return self.resolve(colors)


class ImageColorAnalyzer(Protocol):
    async def analyze_object(self, image_url: str) -> list[ProductColor]:
        """Optional object-segmented analysis. Background must not be treated as object color."""
