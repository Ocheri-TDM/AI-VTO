import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.services import ColorNormalizer, normalize_text
from app.domain.taxonomy import UniversalCategoryResolver

EscalationReason = Literal[
    "AMBIGUOUS_CONCEPT",
    "UNRESOLVED_RELATION",
    "UNBOUND_CONSTRAINT",
    "SEMANTIC_PREFERENCE",
    "COMPLEX_REFERENCE",
    "CONVERSATIONAL_REFERENCE",
    "UNKNOWN_NEGATION_SCOPE",
    "MULTIPLE_VALID_INTERPRETATIONS",
]


class IntentCompleteness(BaseModel):
    mode_resolved: bool
    categories_resolved: bool
    position_count_resolved: bool
    position_constraints_bound: bool
    global_constraints_resolved: bool
    numbers_resolved: bool
    negations_resolved: bool
    ambiguous_tokens: list[str] = Field(default_factory=list)
    unbound_constraints: list[str] = Field(default_factory=list)
    unresolved_spans: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    requires_llm: bool = False
    llm_reason: EscalationReason | None = None


class QuantityConstraint(BaseModel):
    minimum: int | None = Field(default=None, ge=1)
    required: bool = False

    @model_validator(mode="after")
    def canonicalize_activity(self):
        """The object may exist while the procurement constraint is absent."""
        self.required = self.minimum is not None
        return self

    @property
    def required_quantity(self) -> int | None:
        return self.minimum if self.required else None


class ColorConstraint(BaseModel):
    mode: Literal["BASE_COLORS", "REQUIRED", "DIVERSE"] = "BASE_COLORS"
    values: list[str] = Field(default_factory=list)


class PositionConstraint(BaseModel):
    quantity: QuantityConstraint | None = None
    color: ColorConstraint | None = None
    price_max: float | None = Field(default=None, gt=0)
    price_min: float | None = Field(default=None, ge=0)
    materials: list[str] = Field(default_factory=list)
    excluded_materials: list[str] = Field(default_factory=list)
    excluded_colors: list[str] = Field(default_factory=list)
    preferred_materials: list[str] = Field(default_factory=list)
    items_required: int = Field(default=1, ge=1, le=10)

    @property
    def required_quantity(self) -> int | None:
        return self.quantity.required_quantity if self.quantity is not None else None


class CompositePosition(BaseModel):
    category_text: str = Field(min_length=1, max_length=100)
    constraints: PositionConstraint = Field(default_factory=PositionConstraint)


class HardRequestIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_type: Literal["BUILD_PRODUCT_SETS"] = "BUILD_PRODUCT_SETS"
    original_query: str | None = None
    normalized_query: str | None = None
    query_understanding: dict = Field(default_factory=dict)
    target_sets: int = Field(default=1, ge=1, le=100)
    categories: list[str] = Field(default_factory=list, max_length=20)
    positions: list[CompositePosition] = Field(default_factory=list, max_length=20)
    declared_categories_count: int | None = Field(default=None, ge=1, le=20)
    parsed_categories_count: int = 0
    category_count_mismatch: bool = False
    category_mode: Literal["EXPLICIT", "AUTO_DISCOVER"] = "EXPLICIT"
    candidates_per_position: int = Field(default=3, ge=1, le=5)
    quantity: QuantityConstraint = Field(default_factory=QuantityConstraint)
    color: ColorConstraint = Field(default_factory=ColorConstraint)
    price_max: float | None = Field(default=None, gt=0)
    price_min: float | None = Field(default=None, ge=0)
    materials: list[str] = Field(default_factory=list)
    excluded_materials: list[str] = Field(default_factory=list)
    excluded_colors: list[str] = Field(default_factory=list)
    exclusions: list[str] = Field(default_factory=list)
    semantic_preferences: list[str] = Field(default_factory=list)
    purpose: str | None = None
    audience: str | None = None
    sort_preferences: list[str] = Field(default_factory=list)
    items_per_position: int = Field(default=1, ge=1, le=10)
    require_cross_set_uniqueness: bool = False
    unsupported_constraints: list[str] = Field(default_factory=list)
    ambiguities: list[str] = Field(default_factory=list)
    request_mode: Literal["SIMPLE", "COMPOSITE"] = "SIMPLE"
    intent_parser_version: str = "composite-intent-v2"
    case_assembler_version: str = "case-assembler-v2"
    availability_policy: Literal["LAST_KNOWN_STOCK_ALLOWED", "FRESH_ONLY"] = "LAST_KNOWN_STOCK_ALLOWED"
    availability_policy_version: str = "last-known-stock-v3"

    @model_validator(mode="after")
    def counts(self):
        if self.positions:
            self.categories = [position.category_text for position in self.positions]
        self.categories = list(dict.fromkeys(x.strip() for x in self.categories if x.strip()))
        if not self.positions:
            self.positions = [CompositePosition(category_text=value) for value in self.categories]
        self.parsed_categories_count = len(self.categories)
        self.category_count_mismatch = (
            self.declared_categories_count is not None
            and self.declared_categories_count != self.parsed_categories_count
        )
        if not self.categories:
            self.category_mode = "AUTO_DISCOVER"
        self.request_mode = "COMPOSITE" if len(self.categories) >= 2 else "SIMPLE"
        return self

    @property
    def required_quantity(self) -> int | None:
        return self.quantity.required_quantity

    def effective(self, category: str) -> PositionConstraint:
        position = next((item for item in self.positions if item.category_text == category), None)
        local = position.constraints if position else PositionConstraint()
        return PositionConstraint(
            quantity=local.quantity or self.quantity,
            color=local.color or self.color,
            price_max=local.price_max if local.price_max is not None else self.price_max,
            price_min=local.price_min if local.price_min is not None else self.price_min,
            materials=local.materials or self.materials,
            excluded_materials=local.excluded_materials or self.excluded_materials,
            excluded_colors=local.excluded_colors or self.excluded_colors,
            preferred_materials=local.preferred_materials,
            # ``1`` is the backwards-compatible field default, so it must not
            # mask an explicit global "по N товаров каждой категории" value.
            items_required=max(local.items_required, self.items_per_position),
        )


def _color_constraint(value: str) -> ColorConstraint | None:
    extracted = ColorNormalizer().extract(value)
    if not extracted:
        return None
    # A hard colour is exact. Related shades belong in ranking preferences and
    # never silently satisfy a strict procurement constraint.
    return ColorConstraint(mode="REQUIRED", values=list(dict.fromkeys(item.value for item in extracted)))


def _strip_modifiers(value: str) -> str:
    value = re.sub(
        r"\b(?:син\w*|голуб\w*|васильков\w*|черн\w*|бел\w*|красн\w*|зел[её]н\w*|фиолетов\w*)\b(?:\s+цвет\w*)?",
        "",
        value,
        flags=re.I,
    )
    value = re.sub(
        r"\b(?:от|минимум|не менее|не меньше|>=)\s*\d+\s*(?:шт|штук|ед)?",
        "",
        value,
        flags=re.I,
    )
    value = re.sub(r"\b(?:до|не дороже)\s+\d+(?:[.,]\d+)?\s*(?:₽|руб|тенге|₸|тг)?", "", value, flags=re.I)
    return re.sub(r"\s+", " ", value).strip(" .:;")


def _legacy_deterministic_hard_request(text: str) -> HardRequestIntent:
    clean = " ".join(text.split())
    target = re.search(r"(?:собер\w*|найд\w*|сдела\w*)\D{0,20}(\d+)\D{0,20}(?:набор|комплект)", clean, re.I)
    if target is None:
        target = re.search(r"\b(\d+)\s*(?:набор|комплект)\w*\b", clean, re.I)
    declared = re.search(r"из\s+(\d+)\s+категор", clean, re.I)
    quantity = re.findall(
        r"(?:от|минимум|не менее|не меньше|>=|тираж(?:ом)?(?:\s+от)?|количество(?:м)?(?:\s+от)?)"
        r"\s*(\d+)\s*(?:шт|штук|ед)?",
        clean,
        re.I,
    )
    price = re.search(r"(?:до|не дороже)\s+(\d+(?:[.,]\d+)?)\s*(?:₽|руб|тенге|₸|тг)", clean, re.I)
    categories: list[str] = []
    colon = clean.find(":")
    if colon >= 0:
        tail = clean[colon + 1 :]
        tail = re.split(r"[.!?]\s*(?:от|минимум|не менее|до)\b", tail, maxsplit=1, flags=re.I)[0]
        for value in re.split(r",|\s+и\s+", tail):
            value = re.sub(r"[.;:]$", "", value.strip())
            if value and len(value) <= 80:
                categories.append(value)
    elif match := re.search(
        r"(?:из|:)\s+(.+?)(?:[.!?]|\s+(?:от|минимум|не менее|не меньше|>=)\s*\d+|$)",
        clean,
        re.I,
    ):
        categories = [x.strip(" .") for x in re.split(r",|\s+и\s+", match.group(1)) if x.strip(" .")]
    elif "," in clean:
        category_text = re.sub(
            r"^(?:собер\w*|найд\w*|подбер\w*|сдела\w*)\s*(?:\d+\s+(?:набор|комплект)\w*\s*)?",
            "",
            clean,
            flags=re.I,
        )
        category_text = re.split(
            r"[.!?]\s*(?=(?:цвет|тираж|количество)\b|\d+\s*(?:набор|комплект)\w*\b)"
            r"|\s+(?:от|минимум|не менее|не меньше|>=)\s*\d+"
            r"|\s+(?:син\w*|черн\w*|бел\w*|красн\w*)\s+цвет",
            category_text,
            maxsplit=1,
            flags=re.I,
        )[0]
        category_text = re.sub(r"^(?:набор\w*\s+)?(?:из\s+)?", "", category_text, flags=re.I)
        categories = [x.strip(" .:") for x in re.split(r",|\s+и\s+", category_text) if x.strip(" .:")]
    positions = []
    for value in categories:
        category = _strip_modifiers(value)
        if category:
            positions.append(
                CompositePosition(
                    category_text=category,
                    constraints=PositionConstraint(color=_color_constraint(value)),
                )
            )
    # A color attached to an individual comma-separated position is local. A
    # color in the request prefix or after the category list is global.
    prefix = clean[: clean.find(":")] if ":" in clean else ""
    suffix_match = re.search(
        r"(?:от|минимум|не менее|не меньше|>=)\s*\d+\s*(?:шт|штук|ед)?\s+(.+)$",
        clean,
        re.I,
    )
    explicit_global_color = re.search(
        r"(?:^|[.!?])\s*цвет\s+([^.!?]+)", clean, re.I
    )
    global_color = _color_constraint(
        explicit_global_color.group(1) if explicit_global_color else ""
    ) or _color_constraint(prefix) or _color_constraint(
        suffix_match.group(1) if suffix_match else ""
    )
    if global_color is None and positions and not any(p.constraints.color for p in positions):
        global_color = _color_constraint(clean)
    any_base_color = re.search(
        r"(?:разн\w+|рандомн\w+|случайн\w+|люб\w+)\s+цвет", clean, re.I
    )
    color = global_color or ColorConstraint(
        mode="DIVERSE" if any_base_color else "BASE_COLORS"
    )
    fresh_only = bool(re.search(
        r"(?:только\s+актуальн\w*\s+налич|подтвержд[её]нн\w*\s+остат|остатк\w*\s+на\s+сегодня)",
        clean,
        re.I,
    ))
    return HardRequestIntent(
        target_sets=int(target.group(1)) if target else 1,
        categories=[p.category_text for p in positions] or categories,
        positions=positions,
        declared_categories_count=int(declared.group(1)) if declared else None,
        quantity=QuantityConstraint(minimum=int(quantity[-1]) if quantity else None),
        color=color,
        price_max=float(price.group(1).replace(",", ".")) if price else None,
        availability_policy="FRESH_ONLY" if fresh_only else "LAST_KNOWN_STOCK_ALLOWED",
    )


_ASSEMBLY = re.compile(r"\b(?:собер\w*|скомплект\w*|набор\w*|комплект\w*|сет(?:ов|ы|а)?\b)", re.I)
_NEGATIVE_PREFIX = re.compile(r"(?:^|\b)(?:без|кроме|исключ(?:и|ая|ить)|никак\w*|не\s+(?:показывай|нужн\w*|те\b))", re.I)
_SOFT_PREFIX = re.compile(r"\b(?:желательно|предпочтительно|лучше\s+бы|по\s+возможности)\b", re.I)
_SORT_PREFIX = re.compile(r"\b(?:покажи|выведи|поставь)\s+(?:сначала|первыми)\b", re.I)
_MATERIALS = {
    "METAL": r"металл\w*|metal(?:lic)?",
    "WOOD": r"дерев\w*|wood(?:en)?",
    "PLASTIC": r"пластик\w*|plastic",
    "COTTON": r"хлоп\w*|cotton",
    "LEATHER": r"кож\w*|leather",
}


def _materials(value: str) -> list[str]:
    return [name for name, pattern in _MATERIALS.items() if re.search(rf"\b(?:{pattern})\b", value, re.I)]


def _concepts(value: str) -> list[str]:
    """Resolve product concepts only from a positive product-bearing span."""
    value = re.sub(r"\bкаронд(?:аш|аши|ашей|ашов)\w*", "карандаш", value, flags=re.I)
    return UniversalCategoryResolver().resolve(value)


def _purpose_positions(value: str) -> list[str]:
    """Resolve functional slots to catalog concepts without making the prose a category."""
    positions: list[str] = []
    mappings = (
        (r"(?:для\s+запис\w*|писать|writing)", "pen"),
        (r"(?:для\s+напит\w*|пить|drinkware)", "thermomug"),
        (r"(?:для\s+ноутбук\w*|laptop)", "backpack"),
    )
    for pattern, concept in mappings:
        if re.search(pattern, value, re.I):
            positions.append(concept)
    return positions


def _quantity(value: str) -> int | None:
    match = re.search(
        r"(?:(?:от|минимум|не\s+менее|не\s+меньше|>=|тираж(?:ом)?(?:\s+минимум)?(?:\s+от)?)\s*(\d+)\s*(?:шт|штук|ед)?|(?:шт|штук|ед)\s*(\d+))",
        value,
        re.I,
    )
    return int(match.group(1) or match.group(2)) if match else None


def _required_items(value: str) -> int:
    """Read a count that introduces a product clause, not stock or set count."""
    match = re.search(
        r"(?:^|:\s*|\bпо\s+)(один|одна|одну|два|две|три|четыре|пять|\d+)\s+",
        value.strip(),
        re.I,
    )
    if not match:
        return 1
    words = {
        "один": 1, "одна": 1, "одну": 1, "два": 2, "две": 2,
        "три": 3, "четыре": 4, "пять": 5,
    }
    token = match.group(1).casefold()
    return int(token) if token.isdigit() else words[token]


def _semantic_deterministic_hard_request(text: str) -> HardRequestIntent:
    """Build a role-aware procurement intent before retrieval.

    Punctuation is only a clause boundary. A clause becomes a position solely
    when catalog concept evidence or a supported functional role is present.
    """
    clean = " ".join(text.split())
    normalized = normalize_text(clean)
    is_assembly = bool(_ASSEMBLY.search(normalized))

    target_match = re.search(
        r"(?:собер\w*\s+)?(?:ровно\s+)?(\d+)\s*(?:\w+\s+){0,2}?(?:набор|комплект|сет)\w*",
        normalized,
        re.I,
    )
    target_sets = int(target_match.group(1)) if target_match else 1

    # Sentence and comma boundaries retain local constraint scope. Conjunctions
    # are inspected inside the clause through taxonomy evidence instead of being
    # treated as unconditional product separators.
    coarse_clauses = [part.strip(" .:;") for part in re.split(r"[,.!?;]+", normalized) if part.strip(" .:;")]
    clauses: list[str] = []
    for clause in coarse_clauses:
        parts = re.split(r"\s+и\s+", clause)
        if len(parts) > 1 and sum(bool(_concepts(part)) for part in parts) >= 2:
            clauses.extend(parts)
        else:
            clauses.append(clause)
    positive: list[str] = []
    exclusions: list[str] = []
    semantic_preferences: list[str] = []
    sort_preferences: list[str] = []
    excluded_materials: list[str] = []
    excluded_colors: list[str] = []
    purpose_parts: list[str] = []
    audience = None

    for clause in clauses:
        negative = bool(
            _NEGATIVE_PREFIX.search(clause)
            or re.search(r"\bно\s+не\b(?!\s+исключ)", clause)
        )
        soft = bool(_SOFT_PREFIX.search(clause))
        sort = bool(_SORT_PREFIX.search(clause))
        concepts = _concepts(clause)
        colors = [item.value for item in ColorNormalizer().extract(clause)]
        materials = _materials(clause)
        if negative:
            exclusions.extend(concepts)
            excluded_materials.extend(materials)
            excluded_colors.extend(colors)
            continue
        if soft:
            semantic_preferences.extend(materials or [clause])
            continue
        if sort:
            sort_preferences.extend(colors or [clause])
            continue
        functional = _purpose_positions(clause)
        if functional:
            purpose_parts.append(clause)
            if is_assembly:
                positive.extend(functional)
        positive.extend(concepts)

    # Concepts mentioned after a negative marker inside a longer sentence must
    # be excluded even when the sentence also contains the requested product.
    for negative_span in re.findall(
        r"(?:без|кроме|исключая|но\s+не(?!\s+исключ)|не\s+показывай)\s+([^,.!?;]+)", normalized, re.I
    ):
        exclusions.extend(_concepts(negative_span))
        excluded_materials.extend(_materials(negative_span))
        excluded_colors.extend(item.value for item in ColorNormalizer().extract(negative_span))
        if re.search(r"одежд\w*|apparel|clothing", negative_span, re.I):
            exclusions.append("tshirt")

    positive = [item for item in dict.fromkeys(positive) if item not in set(exclusions)]
    if not is_assembly and len(positive) > 1:
        # For one requested subject, taxonomy can also see objects used only to
        # describe purpose (door/bag handles). Preserve the first positive head.
        positive = positive[:1]

    if re.search(r"руководител\w*|executive", normalized, re.I):
        audience = "executives"
    elif re.search(r"сотрудник\w*.*(?:дорог|командиров)|travel(?:ing)?\s+employees", normalized, re.I):
        audience = "traveling_employees"

    positions: list[CompositePosition] = []
    # Bind local colour and quantity by the nearest clause containing a concept.
    for concept in positive:
        owner = next((clause for clause in clauses if concept in _concepts(clause)), "")
        local_color = _color_constraint(owner)
        local_quantity = _quantity(owner)
        local_materials = _materials(owner)
        positions.append(CompositePosition(
            category_text=concept,
            constraints=PositionConstraint(
                color=local_color,
                quantity=QuantityConstraint(minimum=local_quantity) if local_quantity else None,
                materials=local_materials if not _SOFT_PREFIX.search(owner) else [],
                items_required=_required_items(owner),
            ),
        ))

    global_color_match = re.search(
        r"(?:вс[её]\s+в|общ(?:ий|его)\s+цвет|цвет)\s+([^,.!?;]+)", normalized, re.I
    )
    global_color = _color_constraint(global_color_match.group(1)) if global_color_match else None
    if global_color is None and len(positive) <= 1:
        hard_color_clauses = [
            clause for clause in clauses
            if not _NEGATIVE_PREFIX.search(clause)
            and not _SOFT_PREFIX.search(clause)
            and not _SORT_PREFIX.search(clause)
        ]
        global_color = _color_constraint(" ".join(hard_color_clauses))
    global_quantity = None
    explicit_global_quantity = re.search(
        r"(?:тираж(?:ом)?|для\s+кажд\w*|общ\w*\s+количеств\w*)[^\d]{0,20}(?:минимум|от|>=)?\s*(\d+)",
        normalized,
        re.I,
    )
    if explicit_global_quantity:
        global_quantity = int(explicit_global_quantity.group(1))
    elif len(positions) <= 1:
        global_quantity = _quantity(normalized)
    else:
        non_position_quantities = [
            _quantity(clause) for clause in clauses if not _concepts(clause) and _quantity(clause)
        ]
        if non_position_quantities:
            global_quantity = non_position_quantities[-1]

    price = re.search(r"(?:до|не\s+дороже|бюджет)\s*(\d+)\s*(?:₸|тенге|тг|kzt)?", normalized, re.I)
    items_match = re.search(r"по\s+(\d+)\s+разн\w*\s+товар\w*\s+кажд\w*\s+категор", normalized, re.I)
    items_per_position = int(items_match.group(1)) if items_match else 1
    unique = bool(re.search(r"не\s+повторяй\w*.*между\s+наборами|разн\w*\s+набор", normalized, re.I))

    hard_materials = []
    if re.search(r"(?:только|обязательно)[^.!?]{0,20}(?:металл|дерев|пластик|хлоп|кож)|из\s+(?:металл|дерев|пластик|хлоп|кож)", normalized, re.I):
        hard_materials = _materials(normalized)
    soft_materials = []
    for span in re.findall(r"(?:желательно|предпочтительно)\s+([^.!?]+)", normalized, re.I):
        soft_materials.extend(_materials(span))

    unsupported = []
    if items_per_position > 1:
        for position in positions:
            position.constraints.items_required = items_per_position

    mode = "COMPOSITE" if is_assembly and len(positions) >= 2 else "SIMPLE"
    if not positive and not purpose_parts:
        purpose_parts = [normalized]
    result = HardRequestIntent(
        original_query=clean,
        normalized_query=normalized,
        target_sets=target_sets,
        categories=[position.category_text for position in positions],
        positions=positions,
        quantity=QuantityConstraint(minimum=global_quantity),
        color=global_color or ColorConstraint(),
        price_max=float(price.group(1)) if price else None,
        materials=list(dict.fromkeys(hard_materials)),
        excluded_materials=list(dict.fromkeys(excluded_materials)),
        excluded_colors=list(dict.fromkeys(excluded_colors)),
        exclusions=list(dict.fromkeys(exclusions)),
        semantic_preferences=list(dict.fromkeys(semantic_preferences + soft_materials)),
        purpose="; ".join(dict.fromkeys(purpose_parts)) or None,
        audience=audience,
        sort_preferences=list(dict.fromkeys(sort_preferences)),
        items_per_position=items_per_position,
        require_cross_set_uniqueness=unique,
        unsupported_constraints=unsupported,
        request_mode=mode,
    )
    # The model validator derives the legacy field from position count. Purpose
    # discovery and one-concept prose remain SIMPLE; explicit assemblies remain
    # COMPOSITE.
    result.request_mode = mode
    result.availability_policy = (
        "FRESH_ONLY"
        if re.search(r"только\s+актуальн\w*\s+налич|подтвержд[её]нн\w*\s+остат|остатк\w*\s+на\s+сегодня", normalized, re.I)
        else "LAST_KNOWN_STOCK_ALLOWED"
    )
    return result


def deterministic_hard_request(text: str) -> HardRequestIntent:
    """Use the role-aware parser when prose needs semantic scope handling.

    The established explicit-list parser remains the compatibility path for
    terse catalog requests, where preserving supplier-facing category labels is
    valuable and no non-product roles are present.
    """
    normalized = normalize_text(text)
    semantic_cues = re.search(
        r"что[- ]?(?:то|нибудь)|для\s+(?:запис|напит|ноутбук|руководител|сотрудник)|"
        r"(?:без|кроме|исключ|никак\w*|но\s+не)|(?:желательно|предпочтительно)|"
        r"покажи\s+сначала|по\s+\d+\s+разн\w*\s+товар|не\s+повторяй|каронд|"
        r"(?:один|одна|одну|два|две|три|четыре|пять)\s+\w+",
        normalized,
        re.I,
    )
    if semantic_cues:
        return _semantic_deterministic_hard_request(text)
    return _legacy_deterministic_hard_request(text)


_SEMANTIC_PREFERENCE = re.compile(
    r"\b(?:статусн\w*|премиальн\w*|минималистичн\w*|"
    r"корпоративн\w*|выгляд\w*\s+дороже|в\s+духе|похож\w*\s+на)\b",
    re.I,
)
_CONVERSATIONAL_REFERENCE = re.compile(
    r"\b(?:перв\w*|втор\w*|трет\w*|предыдущ\w*|эт\w*\s+вариант\w*|"
    r"замени|оставь|убери)\b",
    re.I,
)
_NEGATION = re.compile(r"\b(?:без|кроме|исключи|не\s+(?!менее\b|меньше\b|дороже\b))", re.I)


def evaluate_intent_completeness(
    intent: HardRequestIntent,
    text: str,
    understanding_confidence: float = 1.0,
) -> IntentCompleteness:
    """Decide whether deterministic evidence is sufficient before using Qwen."""
    resolver = UniversalCategoryResolver()
    known_ids = {node.id for node in resolver.nodes}
    unresolved = [
        category for category in intent.categories
        if category not in known_ids and not resolver.resolve(category)
    ]
    position_colors = [
        position.constraints.color
        for position in intent.positions
        if position.constraints.color and position.constraints.color.mode == "REQUIRED"
    ]
    color_mentions = len(ColorNormalizer().extract(text))
    has_bound_colors = not color_mentions or bool(position_colors) or intent.color.mode == "REQUIRED"

    target_evidence = re.search(r"\b(\d+)\s+(?:набор|комплект)\w*", text, re.I)
    quantity_evidence = re.search(
        r"(?:от|минимум|не\s+менее|не\s+меньше|>=)\s*\d+", text, re.I
    )
    numbers_resolved = (
        (not target_evidence or intent.target_sets == int(target_evidence.group(1)))
        and (not quantity_evidence or intent.quantity.minimum is not None)
    )
    negations_resolved = not _NEGATION.search(text) or bool(
        intent.exclusions
        or intent.excluded_materials
        or intent.excluded_colors
        or intent.require_cross_set_uniqueness
    )

    reason: EscalationReason | None = None
    unresolved_spans: list[str] = []
    unbound_constraints: list[str] = []
    if _CONVERSATIONAL_REFERENCE.search(text):
        reason = "CONVERSATIONAL_REFERENCE"
        unresolved_spans.append("conversation_reference")
    elif _SEMANTIC_PREFERENCE.search(text) and not (
        intent.semantic_preferences or intent.sort_preferences
    ):
        reason = "SEMANTIC_PREFERENCE"
        unresolved_spans.append("semantic_preference")
    elif unresolved:
        reason = "AMBIGUOUS_CONCEPT"
    elif not has_bound_colors:
        reason = "UNBOUND_CONSTRAINT"
        unbound_constraints.append("color")
    elif not numbers_resolved:
        reason = "UNBOUND_CONSTRAINT"
        unbound_constraints.append("number")
    elif not negations_resolved:
        reason = "UNKNOWN_NEGATION_SCOPE"
        unbound_constraints.append("negation")
    elif understanding_confidence < 0.70:
        reason = "MULTIPLE_VALID_INTERPRETATIONS"

    # Purpose discovery is a resolved search mode even when the user did not
    # name a concrete catalog category. Requiring a category here sent clear
    # requests through two long Qwen attempts before deterministic fallback.
    purpose_resolved = bool(intent.purpose or intent.audience)
    complete = (
        (bool(intent.categories) or purpose_resolved)
        and not unresolved
        and len(intent.positions) == len(intent.categories)
        and has_bound_colors
        and numbers_resolved
        and negations_resolved
        and reason is None
    )
    return IntentCompleteness(
        mode_resolved=bool(intent.categories) or purpose_resolved,
        categories_resolved=not unresolved and (bool(intent.categories) or purpose_resolved),
        position_count_resolved=len(intent.positions) == len(intent.categories),
        position_constraints_bound=has_bound_colors,
        global_constraints_resolved=True,
        numbers_resolved=numbers_resolved,
        negations_resolved=negations_resolved,
        ambiguous_tokens=unresolved,
        unbound_constraints=unbound_constraints,
        unresolved_spans=unresolved_spans,
        confidence=min(understanding_confidence, 1.0 if complete else 0.69),
        requires_llm=not complete,
        llm_reason=reason or ("UNRESOLVED_RELATION" if not complete else None),
    )
