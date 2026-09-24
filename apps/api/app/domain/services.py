import hashlib
import re
import unicodedata
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal

from app.domain.errors import CurrencyError
from app.domain.models import ColorGroup, Product, ProductColor


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold().replace("ё", "е")
    for dash in ("–", "—", "‑", "‐"):
        value = value.replace(dash, "-")
    return " ".join(value.split())


class CurrencyService:
    def __init__(self, rub_kzt_rate: Decimal | None):
        if rub_kzt_rate is not None and (not rub_kzt_rate.is_finite() or rub_kzt_rate <= 0):
            raise CurrencyError("Курс RUB_KZT_RATE должен быть положительным числом.")
        self.rate = rub_kzt_rate

    def to_kzt(self, amount: Decimal, currency: str) -> int:
        if not amount.is_finite() or amount < 0:
            raise CurrencyError("Некорректная цена поставщика.")
        code = currency.upper().strip()
        if code == "KZT":
            converted = amount
        elif code == "RUB":
            if self.rate is None:
                raise CurrencyError("Для цен RUB настройте RUB_KZT_RATE в .env.")
            converted = amount * self.rate
        else:
            raise CurrencyError("Валюта поставщика не поддерживается.")
        return int(converted.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


class ColorNormalizer:
    # Ordered specific-to-general; word boundaries prevent `red` matching `infrared`.
    patterns: tuple[tuple[ColorGroup, str], ...] = (
        (ColorGroup.ROYAL_BLUE, r"\b(?:royal[ -]blue|королевск\w* син\w*)\b"),
        (ColorGroup.BLUE_GREY, r"\b(?:blue[ -]gr[ae]y|сине[ -]сер\w*)\b"),
        (ColorGroup.NAVY, r"\b(?:navy|midnight[ -]blue|deep[ -]blue|морск\w* сини\w*)\b"),
        (ColorGroup.DARK_BLUE, r"\b(?:dark[ -]blue|темно[ -]?син\w*|т[ -]синий)\b"),
        (ColorGroup.LIGHT_BLUE, r"\b(?:light[ -]blue|sky[ -]blue|голуб\w*|светло[ -]?син\w*)\b"),
        (ColorGroup.BLUE, r"\b(?:blue|син(?:ий|яя|ее|ие|его|их|юю|ем))\b"),
        (ColorGroup.BLACK, r"\b(?:black|черн\w*)\b"),
        (ColorGroup.WHITE, r"\b(?:white|бел(?:ый|ая|ое|ые|ого|ых))\b"),
        (ColorGroup.GREY, r"\b(?:gr[ae]y|сер(?:ый|ая|ое|ые|ого|ых)|графит\w*)\b"),
        (ColorGroup.GREEN, r"\b(?:green|зелен\w*)\b"),
        (ColorGroup.RED, r"\b(?:red|красн\w*|бордов\w*)\b"),
        (ColorGroup.YELLOW, r"\b(?:yellow|желт\w*)\b"),
        (ColorGroup.ORANGE, r"\b(?:orange|оранжев\w*)\b"),
        (ColorGroup.PURPLE, r"\b(?:purple|violet|фиолетов\w*)\b"),
        (ColorGroup.PINK, r"\b(?:pink|розов\w*)\b"),
        (ColorGroup.BROWN, r"\b(?:brown|коричнев\w*)\b"),
        (ColorGroup.BEIGE, r"\b(?:beige|бежев\w*)\b"),
        (ColorGroup.SILVER, r"\b(?:silver|серебрист\w*)\b"),
        (ColorGroup.GOLD, r"\b(?:gold|золот\w*)\b"),
    )

    def normalize(self, original: str) -> ProductColor:
        text = normalize_text(original).replace("–", "-").replace("—", "-").replace("‑", "-")
        for group, pattern in self.patterns:
            if re.search(pattern, text):
                return ProductColor(original_color=original, normalized_color=group)
        return ProductColor(original_color=original)

    def extract(self, text: str) -> list[ColorGroup]:
        remaining = normalize_text(text)
        found: list[ColorGroup] = []
        for group, pattern in self.patterns:
            if re.search(pattern, remaining):
                found.append(group)
                remaining = re.sub(pattern, " ", remaining)
        return found

    def matches(self, actual: list[ProductColor], requested: list[ColorGroup]) -> bool:
        if not requested:
            return True
        wanted = set(requested)
        if wanted & {ColorGroup.NAVY, ColorGroup.DARK_BLUE}:
            wanted |= {ColorGroup.NAVY, ColorGroup.DARK_BLUE}
        return any(color.normalized_color in wanted for color in actual)


class AvailabilityService:
    def accepts(self, product: Product, quantity: int) -> bool:
        return quantity > 0 and product.stock_quantity is not None and product.stock_quantity >= quantity


class CategoryMatcher:
    """Conservative stage-one relevance check: site searches can return related goods."""

    patterns = {
        "thermomug": r"термо[ -]?(?:круж|стакан|чаш)\w*|дорожн\w*\s+чаш\w*|(?:travel|thermal|vacuum)\s+mug",
        "backpack": r"рюкзак\w*|backpack\w*",
        "notebook": r"ежедневник\w*|блокнот\w*|notebook\w*|diar(?:y|ies)",
        "pen": r"\bруч(?:ка|ки|ек|ку|ками)\b|\bpens?\b",
    }

    def classify(self, name: str) -> list[str]:
        return [
            category
            for category, pattern in self.patterns.items()
            if re.search(pattern, normalize_text(name))
        ]

    def matches(self, name: str, requested: list[str]) -> bool:
        known = set(requested) & self.patterns.keys()
        return not known or bool(known & set(self.classify(name)))


class DeduplicationService:
    def group(self, products: list[Product]) -> list[Product]:
        # Keep every distinct supplier variant. Tag conservative exact-name/color matches.
        unique = {(p.supplier, p.id): p for p in products}
        groups: dict[str, list[Product]] = defaultdict(list)
        for product in unique.values():
            name = re.sub(r"[^\w\s]", "", normalize_text(product.name))
            colors = ",".join(sorted(c.normalized_color.value for c in product.colors))
            groups[f"{name}|{colors}"].append(product)
        for key, group in groups.items():
            if len({p.supplier for p in group}) > 1:
                group_id = hashlib.sha256(key.encode()).hexdigest()[:16]
                for product in group:
                    product.duplicate_group_id = group_id
        return list(unique.values())
