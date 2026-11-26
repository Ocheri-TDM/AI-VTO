"""Attribute roles and typed bounds, independent of product names/taxonomy."""

import re
import unicodedata
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from app.domain.services import ColorNormalizer


class QueryClarification(ValueError):
    """A recognized request whose hard constraints cannot be satisfied as written."""

    PRICE_RANGE = "PRICE_RANGE_CONFLICT"
    MESSAGES = {
        PRICE_RANGE: "Минимальная цена выше максимальной. Какой диапазон использовать?",
    }

    def __init__(self, code):
        self.code = code
        super().__init__(self.MESSAGES[code])


_NUMBER = r"-?\d+(?:\s+\d{3})*(?:[.,]\d+)?"
_QUANTITY_OP = r"не\s+менее|не\s+меньше|не\s+больше|не\s+более|минимум|максимум|больше|более|меньше|менее|от|до|>=|<=|>|<"
_QUANTITY_UNIT = r"(?:шт(?:ук|уки|ука)?\.?|pcs)\b"
_PRICE_UNIT = r"(?:тенге|₸|тг|kzt)"
_PRICE_OP = r"не\s+дороже|не\s+дешевле|дешевле|дороже|до|от|бюджет|цена"
_PRICE_RANGE = re.compile(rf"\b(?:цена\s+)?от\s+({_NUMBER})\s+до\s+({_NUMBER})\s*{_PRICE_UNIT}", re.I)
_PRICE_BOUND = re.compile(
    rf"\b(?:цена\s+)?({_PRICE_OP})\s*({_NUMBER})\s*{_PRICE_UNIT}(?:\s+за\s+штук\w*)?", re.I
)
_IMPLICIT_PRICE = re.compile(
    rf"\b(не\s+дороже|не\s+дешевле|дешевле|дороже)\s+({_NUMBER})(?=\s*(?:$|[,;.!?]))", re.I
)
_PRICE_EXCLUSION = re.compile(
    r"\b(?:убери|исключи|скрой|не\s+показывай|отсей)\s+(?:(?:вс[её]|товар\w*|вариант\w*)\s+)*$", re.I
)
_PRICE_SORT = re.compile(
    r"\b(?:покажи\s+)?сначала\s+(?:сам(?:ые|ых|ую|ый)\s+)?(?:более\s+)?(д[её]ш[её]в\w*|дорог\w*)"
    r"|\b(?:сортируй|отсортируй|сортировка)\s+(?:по\s+цене\s+)?(?:по\s+)?(возрастани\w*|убывани\w*)\b"
    r"|\b(?:по\s+цене\s+)?от\s+(д[её]ш[её]в\w*|дорог\w*)\s+к\s+(?:д[её]ш[её]в\w*|дорог\w*)\b"
    r"|\b(?:price\s+(asc|desc)|cheapest\s+first|most\s+expensive\s+first)\b",
    re.I,
)
_SKU_TOKEN = r"[\w]+(?:[-.][\w]+)+"
_SKU_CUE = re.compile(r"\b(?:артикул\w*|sku)\s*[:№]?\s*([\w]+(?:[-.][\w]+)*)", re.I)
_SKU_HEAD = re.compile(
    rf"^\s*(?:(?:покажи|найди|подбери|show|find)\s+(?:мне\s+)?)?({_SKU_TOKEN})(?![\w.-])"
    rf"(?=\s*(?:$|[,;.!?]|(?:от|до|тираж\w*|количеств\w*|цвет\w*|сначала|минимум)\b))",
    re.I,
)
SHADE_MARKERS = {"BURGUNDY": ("бордов", "бургунд", "burgundy", "maroon", "bordeaux")}
_SHADE = re.compile(r"\b(?:бордов\w*|бургунд\w*|burgundy|maroon|bordeaux)\b", re.I)


def price_sorting(query):
    match = _PRICE_SORT.search(query)
    if not match:
        return None
    value = next((g for g in match.groups() if g), match[0]).casefold().replace("ё", "е")
    return "price_desc" if value.startswith(("дорог", "убыван", "desc", "most expensive")) else "price_asc"


def mask_price_sorting(query):
    return _PRICE_SORT.sub(lambda match: " " * len(match[0]), query)


def sku_span(query):
    explicit = _SKU_CUE.search(query)
    if explicit:
        return explicit if any(c.isdigit() for c in explicit[1]) else None
    implicit = _SKU_HEAD.search(query)
    return implicit if implicit and "." in implicit[1] and any(c.isdigit() for c in implicit[1]) else None


def price_matches(text):
    """Yield nonoverlapping typed bounds; shared by parsing and clarification."""
    found = []
    for pattern in (_PRICE_RANGE, _PRICE_BOUND, _IMPLICIT_PRICE):
        for match in pattern.finditer(text):
            if any(match.start() < end and match.end() > start for start, end in found):
                continue
            found.append(match.span())
            yield match, pattern is _PRICE_RANGE


def without_price_bounds(query):
    text = unicodedata.normalize("NFKC", query)
    matches = sorted((m.span() for m, _ in price_matches(text)), reverse=True)
    for start, end in matches:
        text = text[:start] + " " + text[end:]
    return re.sub(r"\s+", " ", text).strip(" ,.;")


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
    paper_format: str | None = None
    subtypes: list[str] = Field(default_factory=list)
    sku: str | None = None
    colors: list[str] = Field(default_factory=list)
    excluded_colors: list[str] = Field(default_factory=list)
    excluded_color_shades: list[str] = Field(default_factory=list)
    materials: list[str] = Field(default_factory=list)
    excluded_materials: list[str] = Field(default_factory=list)
    preferences: list[str] = Field(default_factory=list)
    features: list[str] = Field(default_factory=list)
    allow_near_colors: bool = False
    availability_only: bool = False


def validate_bounds(value):
    for kind in ("quantity", "price"):
        low, high = getattr(value, kind + "_min"), getattr(value, kind + "_max")
        if low is not None and high is not None and (
            low > high or low == high and (
                getattr(value, kind + "_min_exclusive") or getattr(value, kind + "_max_exclusive")
            )
        ):
            if kind == "price":
                raise QueryClarification(QueryClarification.PRICE_RANGE)
            raise ValueError("Нижняя граница превышает верхнюю.")


def parse_constraints(query, *, check_bounds=True):
    text = unicodedata.normalize("NFKC", query)
    value = AttributeConstraints()
    spans = []

    def consume(match):
        spans.append(match.span())

    # Protect the identifier before interpreting numbers embedded in it.
    sku = sku_span(text)
    if sku:
        value.sku = sku[1]
        consume(sku)
        text = text[:sku.start()] + " " * (sku.end()-sku.start()) + text[sku.end():]
    for match in reversed(list(_PRICE_SORT.finditer(text))):
        consume(match)
        text = text[:match.start()] + " " * (match.end()-match.start()) + text[match.end():]

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
        if kind == "quantity" and not number.is_integer():
            raise ValueError("Тираж должен быть целым числом.")
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

    for match in re.finditer(
        rf"(?<![\w.,])(?:\b(?:по|объ[её]мом|объ[её]м|вместимостью|[её]мкостью)\s*[:=]?\s+)?"
        rf"({_NUMBER})\s*(мл|ml|л|l|литр\w*|мАч|mAh)\b", text, re.I
    ):
        if re.search(r"(?:\b(?:от|до|больше|меньше|более|менее)|[<>]=?)\s*$", text[: match.start()], re.I):
            raise ValueError("Для вместимости укажите точное значение; диапазоны требуют уточнения.")
        n = float(re.sub(r"\s+", "", match[1]).replace(",", "."))
        unit = match[2].casefold()
        field = "capacity_mah" if unit in ("мач", "mah") else "capacity_ml"
        n *= 1000 if unit in ("л", "l") or unit.startswith("литр") else 1
        if n <= 0 or not n.is_integer():
            raise ValueError("Некорректная вместимость.")
        setattr(value, field, int(n))
        consume(match)
    # Paper formats belong to paper, not to the model vocabulary. Explicit
    # model designations (including "модель A5 Max") remain literal identities.
    from app.domain.taxonomy import UniversalCategoryResolver
    paper = "notebook" in UniversalCategoryResolver().resolve(text)
    for match in re.finditer(r"(?<!\w)(?:(?:формат\w*|format|размер\w*)\s*[:=]?\s*)?([AАBВCС][0-8]\+?)(?![\w+])", text, re.I):
        if re.search(r"\b(?:модель|model)\s*[:=]?\s*$", text[:match.start()], re.I):
            continue
        if paper or re.search(r"формат|format", match[0], re.I):
            value.paper_format = match[1].upper().translate(str.maketrans("АВС", "ABC"))
            consume(match)
    for match in re.finditer(r"\b(?:размер\w*|size)\s*[:=]?\s*(\d*[XSML]{1,4}|A[0-6]|\d{2,3})\b", text, re.I):
        if any(match.start() < end and match.end() > start for start, end in spans):
            continue
        value.sizes.append(match[1].upper())
        consume(match)
    for match in re.finditer(rf"\bот\s+({_NUMBER})\s+до\s+({_NUMBER})\s*{_QUANTITY_UNIT}", text, re.I):
        bound("quantity", "от", match[1])
        bound("quantity", "до", match[2])
        consume(match)
    for match in re.finditer(
        rf"(?<![\w.,])(?:({_QUANTITY_OP})\s*)?({_NUMBER})\s*{_QUANTITY_UNIT}",
        text,
        re.I,
    ):
        if any(match.start() < end and match.end() > start for start, end in spans):
            continue
        bound("quantity", re.sub(r"\s+", " ", match[1] or "от"), match[2])
        consume(match)
    for match in re.finditer(
        rf"\b(?:тираж\w*|количеств\w*)\s*(?:({_QUANTITY_OP})\s*)?({_NUMBER})(?:\s*{_QUANTITY_UNIT})?", text, re.I
    ):
        bound("quantity", re.sub(r"\s+", " ", match[1] or "от"), match[2])
        consume(match)
    for match in re.finditer(rf"\b(?:штук|шт)\s+({_NUMBER})(?!\w)", text, re.I):
        bound("quantity", "от", match[1])
        consume(match)
    for match, is_range in price_matches(text):
        if is_range:
            bound("price", "от", match[1])
            bound("price", "до", match[2])
        else:
            op = re.sub(r"\s+", " ", match[1]).casefold()
            # The comparative describes the products being excluded, not the
            # accepted products: "убери всё дороже X" means price <= X.
            if _PRICE_EXCLUSION.search(text[:match.start()]):
                op = {"дороже": "не дороже", "дешевле": "не дешевле",
                      "не дороже": "дороже", "не дешевле": "дешевле"}.get(op, op)
            bound("price", op, match[2])
        consume(match)
    for match in re.finditer(
        rf"\b(не\s+меньше|не\s+менее|больше|более)\s+({_NUMBER})(?=\s*(?:$|[,;.!?]))", text, re.I
    ):
        bound("quantity", re.sub(r"\s+", " ", match[1]), match[2])
        consume(match)
    if check_bounds:
        validate_bounds(value)
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
        soft = bool(re.search(r"желательно|предпочтительно|сначала", name))
        allowed = "исключай" in name
        shades = bool(_SHADE.search(phrase)) and not soft and not allowed
        colors = [c.value for c in ColorNormalizer().extract(_SHADE.sub(" ", phrase) if shades else phrase)]
        if shades:
            value.excluded_color_shades.append("BURGUNDY")
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
        if materials or colors or shades or allowed or soft:
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
