"""Typed, evidence-backed characteristics; raw supplier values remain untouched."""

import hashlib
import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from app.domain.product_purpose import identity_inputs

ATTRIBUTE_VERSION = "product-attributes-v4"


@dataclass(frozen=True)
class AttributeSpec:
    label: str
    kind: str
    unit: str | None
    categories: frozenset[str]


DRINKWARE = frozenset({"bottle", "mug", "thermomug", "thermocup", "thermos", "cup", "glass"})
APPAREL = frozenset(
    {"tshirt", "polo", "hoodie", "sweatshirt", "jacket", "vest", "raincoat", "socks", "cap", "baseballcap"}
)
PAPER = frozenset({"notebook", "diary", "notepad", "planner"})
ATTRIBUTES = {
    "capacity_ml": AttributeSpec("Объём, мл", "number", "мл", DRINKWARE),
    "apparel_size": AttributeSpec("Размер одежды", "enum", None, APPAREL),
    "paper_format": AttributeSpec("Формат", "enum", None, PAPER),
    "canopy_diameter_cm": AttributeSpec("Диаметр купола, см", "number", "см", frozenset({"umbrella"})),
    "umbrella_mechanism": AttributeSpec("Механизм", "enum", None, frozenset({"umbrella"})),
    "capacity_mah": AttributeSpec("Ёмкость, мА·ч", "number", "мА·ч", frozenset({"powerbank"})),
    "storage_gb": AttributeSpec("Память, ГБ", "number", "ГБ", frozenset({"flashdrive"})),
    "bag_capacity_l": AttributeSpec(
        "Вместимость, л", "number", "л", frozenset({"backpack", "bag", "sportsbag", "travelbag"})
    ),
    "laptop_inches": AttributeSpec(
        "Диагональ ноутбука, ″", "number", "″", frozenset({"backpack", "laptopbag"})
    ),
}


def applicable_attributes(category):
    return [key for key, spec in ATTRIBUTES.items() if category in spec.categories]


def number_key(value):
    try:
        value = Decimal(str(value).replace(",", "."))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("Invalid characteristic number") from exc
    if (
        not value.is_finite()
        or not Decimal("0.000001") <= value <= 10_000_000
        or value.quantize(Decimal("0.000001")) != value
    ):
        raise ValueError("Invalid characteristic number")
    return format(value.normalize(), "f")


def normalize_value(attribute, raw, *, bare=False):
    value = re.sub(r"\s+", " ", str(raw)).strip()
    upper = value.upper().replace("А", "A")
    if attribute == "paper_format":
        return upper if re.fullmatch(r"[ABC][0-8](?:\+)?", upper) else None
    if attribute == "apparel_size":
        # A suffix is part of the real value: never strip V2 or arbitrary text.
        if re.fullmatch(
            r"(?:[2-8]?X{0,4}[SML]|[SM]/[ML]|L/XL|XL/XXL|(?:\d{2,3})(?:[-–/]\d{2,3})?|\d{1,2}[-–]\d{1,2} лет(?: \(\d{2,3}[-–]\d{2,3} см\))?)",
            value,
            re.I,
        ):
            return value.upper() if not re.search(r"лет|см", value, re.I) else value.lower()
        return None
    if attribute == "umbrella_mechanism":
        return {
            "автомат": "AUTOMATIC",
            "полный автомат": "FULL_AUTOMATIC",
            "полуавтомат": "SEMI_AUTOMATIC",
            "механический": "MANUAL",
            "механика": "MANUAL",
            "automatic": "AUTOMATIC",
            "manual": "MANUAL",
            "semi-automatic": "SEMI_AUTOMATIC",
            "full automatic": "FULL_AUTOMATIC",
        }.get(value.casefold())
    units = {
        "capacity_ml": r"мл|ml|л|l|литр(?:а|ов)?",
        "capacity_mah": r"м[аa](?:[·⋅ ]?ч)|mah",
        "storage_gb": r"гб|gb",
        "bag_capacity_l": r"л|l|литр(?:а|ов)?|мл|ml",
        "canopy_diameter_cm": r"см|cm|мм|mm",
        "laptop_inches": r'дюйм(?:а|ов)?|inches|inch|in|["″]',
    }
    match = re.fullmatch(
        r"(\d+(?:[.,]\d+)?)\s*(" + units[attribute] + ")" + ("?" if bare else ""), value, re.I
    )
    if not match:
        return None
    n = Decimal(match[1].replace(",", "."))
    unit = (match[2] or "").casefold()
    if attribute == "capacity_ml" and (unit in ("л", "l") or unit.startswith("литр")):
        n *= 1000
    if attribute == "bag_capacity_l" and unit in ("мл", "ml"):
        n /= 1000
    if attribute == "canopy_diameter_cm" and unit in ("мм", "mm"):
        n /= 10
    try:
        return number_key(n)
    except (ValueError, InvalidOperation):
        return None


def value_label(attribute, key):
    if attribute == "umbrella_mechanism":
        return {
            "AUTOMATIC": "Автомат",
            "FULL_AUTOMATIC": "Полный автомат",
            "SEMI_AUTOMATIC": "Полуавтомат",
            "MANUAL": "Механический",
        }.get(key, key)
    spec = ATTRIBUTES[attribute]
    return key.replace(".", ",") + (" " + spec.unit if spec.unit else "")


def projection_input(payload):
    metadata = payload.get("metadata") or {}
    return {
        "name": payload.get("name"),
        "category": payload.get("category"),
        "brand": payload.get("brand"),
        "capacity": payload.get("capacity"),
        "metadata": {
            key: metadata.get(key)
            for key in [
                "supplier_category",
                "breadcrumbs",
                "attributes",
                "variant_attributes",
                "size",
                *ATTRIBUTES,
            ]
        },
    }


def attribute_projection(payload):
    source = projection_input(payload)
    fingerprint = hashlib.sha256(
        json.dumps(source, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()
    metadata = source["metadata"]
    identity = identity_inputs(
        source["name"] or "",
        metadata.get("supplier_category"),
        metadata.get("breadcrumbs"),
        metadata.get("attributes"),
        brand=source["brand"],
    )
    categories = set(identity.primary)
    allowed = {a for a, spec in ATTRIBUTES.items() if categories & spec.categories}
    candidates, issues, blocked = {}, [], {}

    def add(attribute, raw, field, priority, *, bare=False, strict_scope=False, label_unit=None):
        if attribute not in allowed or raw in (None, ""):
            return
        parsed = str(raw)
        if label_unit and re.fullmatch(r"\d+(?:[.,]\d+)?", parsed):
            parsed += " " + label_unit
        key = normalize_value(attribute, parsed, bare=bare)
        if key is None:
            issues.append(
                {
                    "attribute": attribute,
                    "field": field,
                    "raw": str(raw)[:300],
                    "reason": "AMBIGUOUS_OR_UNTYPED",
                }
            )
            if strict_scope and priority >= 25:
                blocked[attribute] = max(priority, blocked.get(attribute, 0))
            return
        candidates.setdefault(attribute, []).append(
            (
                priority,
                key,
                {
                    "field": field,
                    "raw": str(raw),
                    "scope": "VARIANT" if priority >= 20 else "PUBLISHED_PRODUCT_ATTRIBUTE",
                },
            )
        )

    sources = [
        (metadata.get("attributes") or {}, "metadata.attributes", 10),
        (metadata.get("variant_attributes") or {}, "metadata.variant_attributes", 30),
    ]
    for attributes, field, priority in sources:
        for name, raw in attributes.items():
            label = str(name).casefold().replace("ё", "е")
            if re.search(r"упаков|транспорт|shipping|package|вес|weight|комплект|набор|количество", label):
                continue
            if re.search(r"объем|объём|вместим|volume|capacity", label):
                unit = re.search(r"(?:\b|_)(мл|ml|л|l)(?:\b|_)", label)
                for attribute in ("capacity_ml", "bag_capacity_l"):
                    add(
                        attribute,
                        raw,
                        field + "." + name,
                        priority,
                        strict_scope=True,
                        label_unit=unit[1] if unit else None,
                    )
            if re.search(r"емкост|ёмкост|battery", label):
                add("capacity_mah", raw, field + "." + name, priority)
            if re.search(r"памят|storage|memory", label):
                add("storage_gb", raw, field + "." + name, priority)
            if re.search(r"диаметр|diameter", label):
                unit = re.search(r"\b(см|cm|мм|mm)\b", label)
                add(
                    "canopy_diameter_cm",
                    raw,
                    field + "." + name,
                    priority,
                    strict_scope=True,
                    label_unit=unit[1] if unit else None,
                )
            if re.search(r"механизм|mechanism", label):
                add("umbrella_mechanism", raw, field + "." + name, priority)
            if re.search(r"ноутбук|laptop|диагональ", label):
                add("laptop_inches", raw, field + "." + name, priority)
            if re.search(r"размер|size|формат|format", label):
                for attribute in ("apparel_size", "paper_format", "capacity_ml", "bag_capacity_l"):
                    add(
                        attribute,
                        raw,
                        field + "." + name,
                        priority,
                        strict_scope=bool(re.search(r"формат|format", label)),
                    )
    for attribute in allowed:
        add(attribute, metadata.get(attribute), "metadata." + attribute, 40, bare=True, strict_scope=True)
    for attribute in ("apparel_size", "paper_format", "capacity_ml", "bag_capacity_l"):
        add(attribute, metadata.get("size"), "metadata.size", 35)
    for attribute in ("capacity_ml", "capacity_mah", "storage_gb", "bag_capacity_l"):
        add(attribute, source.get("capacity"), "capacity", 15, bare=True)
    # Explicit unit-bearing title evidence is permitted only for an identified
    # main product. Price, SKU, quantity and arbitrary bare model numbers are not.
    title = source["name"] or ""
    for attribute, pattern in [
        ("capacity_ml", r"\b\d+(?:[.,]\d+)?\s*(?:мл|ml|литр(?:а|ов)?|л|l)\b"),
        ("capacity_mah", r"\b\d+\s*(?:мАч|mAh)\b"),
        ("storage_gb", r"\b\d+\s*(?:ГБ|GB)\b"),
    ]:
        for match in re.finditer(pattern, title, re.I):
            if not re.search(
                r"упаков|транспорт|shipping|package", title[max(0, match.start() - 60) : match.end()], re.I
            ):
                add(attribute, match[0], "name.explicit_unit", 5)
    result = []
    for attribute, rows in candidates.items():
        priority = max(r[0] for r in rows)
        if blocked.get(attribute, 0) >= priority:
            continue
        chosen = [r for r in rows if r[0] == priority]
        keys = {r[1] for r in chosen}
        if len(keys) != 1:
            issues.append(
                {
                    "attribute": attribute,
                    "reason": "CONFLICTING_PUBLISHED_VALUES",
                    "evidence": [r[2] for r in chosen],
                }
            )
            continue
        key = next(iter(keys))
        result.append(
            {
                "attribute_id": attribute,
                "value_key": key,
                "label": value_label(attribute, key),
                "numeric_value": Decimal(key) if ATTRIBUTES[attribute].kind == "number" else None,
                "evidence": [r[2] for r in rows if r[1] == key],
            }
        )
    return fingerprint, result, issues
