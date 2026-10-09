import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.models import ColorGroup
from app.domain.services import ColorNormalizer
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
    exclusions: list[str] = Field(default_factory=list)
    semantic_preferences: list[str] = Field(default_factory=list)
    ambiguities: list[str] = Field(default_factory=list)
    request_mode: Literal["SIMPLE", "COMPOSITE"] = "SIMPLE"
    intent_parser_version: str = "composite-intent-v2"
    case_assembler_version: str = "case-assembler-v2"
    availability_policy_version: str = "strict-procurement-v2"

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
            excluded_materials=local.excluded_materials,
        )


def _color_constraint(value: str) -> ColorConstraint | None:
    extracted = ColorNormalizer().extract(value)
    if not extracted:
        return None
    blue_family = {
        ColorGroup.BLUE,
        ColorGroup.NAVY,
        ColorGroup.DARK_BLUE,
        ColorGroup.LIGHT_BLUE,
        ColorGroup.ROYAL_BLUE,
        ColorGroup.BLUE_GREY,
    }
    requested = blue_family if any(color in blue_family for color in extracted) else set(extracted)
    return ColorConstraint(mode="REQUIRED", values=sorted(item.value for item in requested))


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


def deterministic_hard_request(text: str) -> HardRequestIntent:
    clean = " ".join(text.split())
    target = re.search(r"(?:собер\w*|найд\w*|сдела\w*)\D{0,20}(\d+)\D{0,20}(?:набор|комплект)", clean, re.I)
    declared = re.search(r"из\s+(\d+)\s+категор", clean, re.I)
    quantity = re.findall(
        r"(?:от|минимум|не менее|не меньше|>=)\s*(\d+)\s*(?:шт|штук|ед)?",
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
        r"(?:из|:)\s+(.+?)(?:[.!?]|\s+(?:от|минимум|не менее|не меньше|>=)\s*\d+)",
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
            r"\s+(?:от|минимум|не менее|не меньше|>=)\s*\d+|\s+(?:син\w*|черн\w*|бел\w*|красн\w*)\s+цвет",
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
    global_color = _color_constraint(prefix) or _color_constraint(
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
    return HardRequestIntent(
        target_sets=int(target.group(1)) if target else 1,
        categories=[p.category_text for p in positions] or categories,
        positions=positions,
        declared_categories_count=int(declared.group(1)) if declared else None,
        quantity=QuantityConstraint(minimum=int(quantity[-1]) if quantity else None),
        color=color,
        price_max=float(price.group(1).replace(",", ".")) if price else None,
    )


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
    unresolved = [category for category in intent.categories if not resolver.resolve(category)]
    position_colors = [
        position.constraints.color
        for position in intent.positions
        if position.constraints.color and position.constraints.color.mode == "REQUIRED"
    ]
    color_mentions = len(ColorNormalizer().extract(text))
    has_bound_colors = not color_mentions or bool(position_colors) or intent.color.mode == "REQUIRED"

    target_evidence = re.search(r"\b\d+\s+(?:набор|комплект)\w*", text, re.I)
    quantity_evidence = re.search(
        r"(?:от|минимум|не\s+менее|не\s+меньше|>=)\s*\d+", text, re.I
    )
    numbers_resolved = (
        (not target_evidence or intent.target_sets != 1)
        and (not quantity_evidence or intent.quantity.minimum is not None)
    )
    negations_resolved = not _NEGATION.search(text) or bool(intent.exclusions)

    reason: EscalationReason | None = None
    unresolved_spans: list[str] = []
    unbound_constraints: list[str] = []
    if _CONVERSATIONAL_REFERENCE.search(text):
        reason = "CONVERSATIONAL_REFERENCE"
        unresolved_spans.append("conversation_reference")
    elif _SEMANTIC_PREFERENCE.search(text):
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

    complete = (
        bool(intent.categories)
        and not unresolved
        and len(intent.positions) == len(intent.categories)
        and has_bound_colors
        and numbers_resolved
        and negations_resolved
        and reason is None
    )
    return IntentCompleteness(
        mode_resolved=bool(intent.categories),
        categories_resolved=not unresolved and bool(intent.categories),
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
