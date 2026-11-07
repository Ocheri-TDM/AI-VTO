"""Attribute roles and typed bounds, independent of product names/taxonomy."""

import re
import unicodedata
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from app.domain.services import ColorNormalizer


@dataclass(frozen=True)
class ColorSpan:
    start: int
    end: int
    color: str
    text: str
    role: str = "COLOR"


def color_spans(query):
    """Match complete attribute phrases, retaining offsets in the source text.

    Query typography is normalized without changing the supplier shade table.
    Specific phrases consume their whole span before general color words.
    """
    normalized, offsets = [], []
    for n, char in enumerate(query):
        for c in unicodedata.normalize("NFKC", char).casefold().replace("ё", "е"):
            c = "-" if c in "‐‑‒–—−" else " " if c.isspace() else c
            if c == " " and normalized and normalized[-1] == " ":
                offsets[-1] = (offsets[-1][0], n + 1)
                continue
            normalized.append(c)
            offsets.append((n, n + 1))
    text = "".join(normalized)
    found = []
    occupied = []
    for group, pattern in ColorNormalizer.patterns:
        for match in re.finditer(pattern, text):
            if any(match.start() < b and match.end() > a for a, b in occupied):
                continue
            occupied.append(match.span())
            start, end = offsets[match.start()][0], offsets[match.end() - 1][1]
            found.append(ColorSpan(start, end, group.value, query[start:end]))
    return sorted(found, key=lambda span: span.start)


MATERIAL_ALIASES = {
    "METAL": ["металл", "сталь", "алюмин", "metal", "steel"],
    "PLASTIC": ["пластик", "plastic"],
    "WOOD": ["дерев", "wood"],
    "COTTON": ["хлоп", "cotton"],
    "LEATHER": ["кож", "leather"],
}


class AttributeConstraints(BaseModel):
    model_config = ConfigDict(extra="forbid")
    quantity_min: int | None = Field(default=None, ge=1, le=10_000_000)
    quantity_max: int | None = Field(default=None, ge=0, le=10_000_000)
    quantity_min_exclusive: bool = False
    quantity_max_exclusive: bool = False
    price_min: float | None = Field(default=None, ge=0, le=1_000_000_000)
    price_max: float | None = Field(default=None, ge=0, le=1_000_000_000)
    price_min_exclusive: bool = False
    price_max_exclusive: bool = False
    capacity_ml: int | None = Field(default=None, ge=1, le=1_000_000)
    capacity_mah: int | None = Field(default=None, ge=1, le=10_000_000)
    storage_gb: int | None = Field(default=None, ge=1, le=1_000_000)
    connectors: list[str] = Field(default_factory=list)
    sizes: list[str] = Field(default_factory=list)
    subtypes: list[str] = Field(default_factory=list)
    sku: str | None = None
    colors: list[str] = Field(default_factory=list)
    excluded_colors: list[str] = Field(default_factory=list)
    materials: list[str] = Field(default_factory=list)
    excluded_materials: list[str] = Field(default_factory=list)
    preferences: list[str] = Field(default_factory=list)
    features: list[str] = Field(default_factory=list)
    allow_near_colors: bool = False
    availability_only: bool = False


def parse_constraints(query):
    text = unicodedata.normalize("NFKC", query)
    if re.search(r"\d+[.,]\d+\s*шт", text, re.I):
        raise ValueError("Тираж должен быть целым числом.")
    value = AttributeConstraints()
    spans = []
    number = r"-?\d+(?:\s+\d{3})*(?:[.,]\d+)?"

    def consume(match):
        spans.append(match.span())

    for match in re.finditer(r"\b(\d+)\s*(?:ГБ|GB)\b", text, re.I):
        value.storage_gb = int(match[1])
        consume(match)
    for match in re.finditer(r"\bUSB\s*[-–—]?\s*C\b", text, re.I):
        value.connectors.append("USB-C")
        consume(match)
    for match in re.finditer(r"\b(?:без\s+козырьк\w*|without\s+visor)\b", text, re.I):
        value.features.append("WITHOUT_VISOR")
        consume(match)

    for match in re.finditer(
        r"можно\s+близк\w*(?:\s+оттен\w*)?|люб\w*\s+оттен\w*|люб(?:ой|ого)\s+цвет\w*", text, re.I
    ):
        value.allow_near_colors = bool(re.search(r"близк|оттен", match[0], re.I))
        consume(match)

    def bound(kind, op, number):
        number = float(re.sub(r"\s+", "", number).replace(",", "."))
        if number < 0 or kind == "quantity" and number == 0:
            raise ValueError("Количество и цена должны быть положительными.")
        upper = op.casefold() in (
            "до",
            "не дороже",
            "дешевле",
            "менее",
            "меньше",
            "не больше",
            "не более",
            "<",
            "<=",
            "максимум",
            "бюджет",
            "цена",
        )
        exclusive = op.casefold() in ("больше", "более", "дороже", "дешевле", "меньше", "менее", ">", "<")
        field = kind + ("_max" if upper else "_min")
        setattr(value, field, int(number) if kind == "quantity" else number)
        setattr(value, field + "_exclusive", exclusive)

    for match in re.finditer(r"(?<!\w)(-?\d+(?:[.,]\d+)?)\s*(мл|ml|л|литр\w*|мАч|mAh)\b", text, re.I):
        if re.search(r"(?:\b(?:от|до|больше|меньше|более|менее)|[<>]=?)\s*$", text[: match.start()], re.I):
            raise ValueError("Для вместимости укажите точное значение; диапазоны требуют уточнения.")
        n = float(match[1].replace(",", "."))
        unit = match[2].casefold()
        field = "capacity_mah" if unit in ("мач", "mah") else "capacity_ml"
        n *= 1000 if unit == "л" or unit.startswith("литр") else 1
        if n <= 0 or not n.is_integer():
            raise ValueError("Некорректная вместимость.")
        setattr(value, field, int(n))
        consume(match)
    for match in re.finditer(r"\b(?:размер\w*|size)\s*[:=]?\s*(\d*[XSML]{1,4}|A[0-6]|\d{2,3})\b", text, re.I):
        value.sizes.append(match[1].upper())
        consume(match)
    sku = re.fullmatch(r"\s*(?:(?:sku|артикул)\s*[:№]?\s*)?([\w]+(?:[-.][\w]+)+)\s*", text, re.I)
    if sku and any(c.isdigit() for c in sku[1]) and ("." in sku[1] or re.search(r"sku|артикул", text, re.I)):
        value.sku = sku[1]
        consume(sku)
    for match in re.finditer(
        r"(?:(не\s+менее|не\s+меньше|не\s+больше|не\s+более|минимум|максимум|больше|более|меньше|менее|от|до|>=|<=|>|<)\s*)?(-?\d+)\s*(?:шт(?:ук|уки|ука)?\.?|pcs)\b",
        text,
        re.I,
    ):
        bound("quantity", re.sub(r"\s+", " ", match[1] or "от"), match[2])
        consume(match)
    for match in re.finditer(
        r"\b(?:тираж\w*|количеств\w*)\s*(?:минимум|от|не\s+менее)?\s*(-?\d+)", text, re.I
    ):
        bound("quantity", "от", match[1])
        consume(match)
    for match in re.finditer(r"\b(?:штук|шт)\s+(-?\d+)\b", text, re.I):
        bound("quantity", "от", match[1])
        consume(match)
    for match in re.finditer(r"\bот\s+(-?\d+)\s+до\s+(-?\d+)\s*шт", text, re.I):
        bound("quantity", "от", match[1])
        bound("quantity", "до", match[2])
        consume(match)
    for match in re.finditer(
        rf"\b(?:цена\s+)?от\s+({number})\s+до\s+({number})\s*(?:тенге|₸|тг|kzt)", text, re.I
    ):
        bound("price", "от", match[1])
        bound("price", "до", match[2])
        consume(match)
    for match in re.finditer(
        rf"\b(?:цена\s+)?(не\s+дороже|не\s+дешевле|дешевле|дороже|до|от|бюджет|цена)\s*({number})\s*(?:тенге|₸|тг|kzt)(?:\s+за\s+штук\w*)?",
        text,
        re.I,
    ):
        bound("price", re.sub(r"\s+", " ", match[1]), match[2])
        consume(match)
    for match in re.finditer(
        rf"\b(не\s+дороже|не\s+дешевле|дешевле|дороже)\s+({number})(?=\s*(?:$|[,;.!?]))", text, re.I
    ):
        bound("price", re.sub(r"\s+", " ", match[1]), match[2])
        consume(match)
    for match in re.finditer(
        rf"\b(не\s+меньше|не\s+менее|больше|более)\s+({number})(?=\s*(?:$|[,;.!?]))", text, re.I
    ):
        bound("quantity", re.sub(r"\s+", " ", match[1]), match[2])
        consume(match)
    for kind in ("quantity", "price"):
        low, high = getattr(value, kind + "_min"), getattr(value, kind + "_max")
        if (
            low is not None
            and high is not None
            and (
                low > high
                or low == high
                and (getattr(value, kind + "_min_exclusive") or getattr(value, kind + "_max_exclusive"))
            )
        ):
            raise ValueError("Нижняя граница превышает верхнюю.")
    # Non-product scopes terminate at punctuation or another role marker. Keep
    # the positive product head even when a preference shares its sentence.
    roles = r"(?:но\s+не\s+исключай|не\s+исключай|желательно|предпочтительно|покажи\s+сначала|сначала|но\s+не|не\s+показывай|исключи|исключая|кроме|без|никаких|не\s+(?=(?:бел|черн|чёрн|красн|пластик|металл)))"
    markers = list(re.finditer(r"\b" + roles + r"\b", text, re.I))
    hard_text = text
    for n, marker in enumerate(markers):
        end = markers[n + 1].start() if n + 1 < len(markers) else len(text)
        punctuation = re.search(r"[,.;!?]" if marker[0].strip().casefold() == "не" else r"[.;!?]", text[marker.end() : end])
        if punctuation:
            end = marker.end() + punctuation.start()
        phrase = text[marker.end() : end]
        name = marker[0].casefold()
        materials = [
            k
            for k, aliases in MATERIAL_ALIASES.items()
            if any(re.search(r"\b" + a, phrase, re.I) for a in aliases)
        ]
        colors = [c.value for c in ColorNormalizer().extract(phrase)]
        soft = bool(re.search(r"желательно|предпочтительно|сначала", name))
        allowed = "исключай" in name
        if soft:
            value.preferences.extend(
                materials + colors
                or [
                    re.sub(
                        r"\b(?:бренд\w*|марк[аиу]?|модел[ьиь]?|brand|model)\s*", "", phrase, flags=re.I
                    ).strip()
                ]
            )
        elif not allowed:
            value.excluded_materials.extend(materials)
            value.excluded_colors.extend(colors)
        # Remove only attribute spans, not category exclusions, from identity.
        if materials or colors or allowed or soft:
            spans.append((marker.start(), end))
            hard_text = hard_text[: marker.start()] + " " * (end - marker.start()) + hard_text[end:]
    matched_colors = color_spans(hard_text)
    value.colors = list(dict.fromkeys(span.color for span in matched_colors))
    # The residual model parser must never reuse a fragment of an attribute.
    spans.extend((span.start, span.end) for span in matched_colors)
    if len(value.colors) > 1 and re.search(r"\bодновременно\b|\bтолько\b.+\bи\s+только\b", hard_text, re.I):
        raise ValueError(
            "Указаны несовместимые обязательные цвета. Уточните цвет или разрешите альтернативы."
        )
    for match in re.finditer(r"\b(?:(?:только|есть)\s+)?в\s+наличии\b|\bin\s+stock\b", hard_text, re.I):
        value.availability_only = True
        consume(match)
    value.materials = [
        k
        for k, aliases in MATERIAL_ALIASES.items()
        if any(re.search(r"\b" + a, hard_text, re.I) for a in aliases)
    ]
    if set(value.colors) & set(value.excluded_colors):
        raise ValueError("Требуемый цвет одновременно исключён.")
    for match in re.finditer(r"\b(?:с|со|в)\s+(футляр\w*|карабин\w*)", hard_text, re.I):
        value.features.append("футляр" if match[1].casefold().startswith("футляр") else "карабин")
        consume(match)
    if re.search(r"\bвечн\w*|\beternal\b", hard_text, re.I):
        value.features.append("ETERNAL")
    for pattern, subtype in [(r"\bежедневник\w*", "DIARY"), (r"\bблокнот\w*", "NOTEPAD")]:
        if re.search(pattern, hard_text, re.I):
            value.subtypes.append(subtype)
    for match in re.finditer(r"\b2\s*(?:в|in)\s*1\b", hard_text, re.I):
        value.features.append("HYBRID_2_IN_1")
        consume(match)
    masked = list(text)
    for a, b in spans:
        masked[a:b] = " " * (b - a)
    return AttributeConstraints.model_validate(value.model_dump()), "".join(masked)
