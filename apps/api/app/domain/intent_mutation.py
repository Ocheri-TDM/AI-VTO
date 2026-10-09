"""Typed, deterministic mutations of an existing Composite intent."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from app.domain.hard_request import (
    ColorConstraint,
    CompositePosition,
    HardRequestIntent,
    PositionConstraint,
    QuantityConstraint,
    _color_constraint,
)
from app.domain.services import normalize_text
from app.domain.taxonomy import UniversalCategoryResolver, terms


class PatchOperation(StrEnum):
    SET = "SET"
    CLEAR = "CLEAR"
    ADD_POSITION = "ADD_POSITION"
    REMOVE_POSITION = "REMOVE_POSITION"
    REPLACE_POSITION = "REPLACE_POSITION"
    KEEP_ONLY = "KEEP_ONLY"
    SEMANTIC_REFINE = "SEMANTIC_REFINE"
    UNDO = "UNDO"


class PatchScope(StrEnum):
    GLOBAL = "GLOBAL"
    POSITION = "POSITION"


class PatchField(StrEnum):
    COLOR = "COLOR"
    MATERIAL = "MATERIAL"
    PRICE = "PRICE"
    QUANTITY = "QUANTITY"
    CATEGORY = "CATEGORY"
    TARGET_CASES = "TARGET_CASES"
    SEMANTIC_PREFERENCE = "SEMANTIC_PREFERENCE"


class IntentPatch(BaseModel):
    operation: PatchOperation
    scope: PatchScope = PatchScope.GLOBAL
    field: PatchField
    position: str | None = None
    replacement: str | None = None
    value: Any = None
    source_text: str
    confidence: float = Field(default=0.98, ge=0, le=1)
    resolver: str = "DETERMINISTIC_MUTATION_V1"


class MutationResult(BaseModel):
    patches: list[IntentPatch] = Field(default_factory=list)
    normalized_message: str
    llm_used: bool = False


class IntentMutationService:
    def __init__(self):
        self.resolver = UniversalCategoryResolver()

    def _concepts(self, text: str) -> list[str]:
        value = normalize_text(text)
        found = self.resolver.resolve(value)
        patterns = {
            "tshirt": r"\b(?:футбол|фудбол)\w*",
            "pen": r"\b(?:ручк|ручьк|рючк)\w*",
            "bottle": r"\bбутыл\w*",
            "mug": r"\bкруж\w*",
            "thermomug": r"\bтермо[- ]?круж\w*",
            "umbrella": r"\bзонт\w*",
            "backpack": r"\bрюкзак\w*",
            "raincoat": r"\bдождевик\w*",
            "socks": r"\bноск\w*",
        }
        if value in patterns and value not in found:
            found.append(value)
        for concept, pattern in patterns.items():
            if re.search(pattern, value) and concept not in found:
                found.append(concept)
        if "thermomug" in found and "mug" in found:
            found.remove("mug")
        return found

    @staticmethod
    def _color(text: str) -> ColorConstraint | None:
        color = _color_constraint(text)
        if color:
            return color
        value = normalize_text(text)
        colors = {
            "RED": r"\bкрасн\w*", "BLACK": r"\bч[еёо]рн\w*",
            "WHITE": r"\bбел\w*", "GREEN": r"\bзел[её]н\w*",
            "YELLOW": r"\bж[её]лт\w*", "BLUE": r"\bсин\w*",
        }
        for name, pattern in colors.items():
            if re.search(pattern, value):
                if name == "BLUE":
                    return ColorConstraint(mode="REQUIRED", values=[
                        "BLUE", "BLUE_GREY", "DARK_BLUE", "LIGHT_BLUE", "NAVY", "ROYAL_BLUE"
                    ])
                return ColorConstraint(mode="REQUIRED", values=[name])
        return None

    def _position_map(self, intent: HardRequestIntent) -> dict[str, CompositePosition]:
        result: dict[str, CompositePosition] = {}
        for position in intent.positions:
            for concept in self._concepts(position.category_text):
                result.setdefault(concept, position)
            # Open-vocabulary positions may not belong to the seeded taxonomy.
            # Stable lexical keys let a follow-up name that exact persisted
            # position without turning the constraint into a global mutation.
            for stem in terms(position.category_text):
                result.setdefault(f"literal:{stem}", position)
        return result

    def plan(self, intent: HardRequestIntent, source: str, normalized: str) -> MutationResult:
        text = normalize_text(normalized)
        source_text = normalize_text(source)
        command_text = f"{text} {source_text}"
        patches: list[IntentPatch] = []
        positions = self._position_map(intent)

        def add(operation, field, *, scope=PatchScope.GLOBAL, position=None,
                replacement=None, value=None, confidence=.98):
            patches.append(IntentPatch(operation=operation, scope=scope, field=field,
                position=position, replacement=replacement, value=value,
                source_text=source, confidence=confidence))

        if re.search(r"\b(?:отмени|верни предыдущее|шаг назад)\b", text):
            add(PatchOperation.UNDO, PatchField.CATEGORY)
            return MutationResult(patches=patches, normalized_message=normalized)

        # Set counts are deliberately parsed separately from procurement quantity.
        target = re.search(r"(?:сделай|зделай|хочу|пусть будет|не\s+\d+\s+набор\w*\s*,?\s*а)?\s*(\d+)\s+(?:набор|комплект)\w*", command_text)
        if target:
            add(PatchOperation.SET, PatchField.TARGET_CASES, value=int(target.group(1)))

        if re.search(
            r"(?:количество|тираж)(?:\s+т[оа]вар\w*)?(?:\s+больше)?\s+не\s+важ"
            r"|без\s+ограничени\w*\s+по\s+(?:количеству|тиражу)"
            r"|убери\s+ограничени\w*\s+по\s+(?:количеству|тиражу)",
            command_text,
        ):
            add(PatchOperation.CLEAR, PatchField.QUANTITY)
        else:
            quantity = re.search(r"(?:[оа]т|минимум|не\s+менее|не\s+меньше|>=|лучше\s+[оа]т)\s*(\d+)\s*(?:шт|штук|ед)?", command_text)
            if quantity:
                add(PatchOperation.SET, PatchField.QUANTITY, value=int(quantity.group(1)))

        if re.search(r"(?:цена|бюджет)\s+не\s+важ|без\s+(?:ограничения\s+по\s+)?(?:цене|бюджету)|убери\s+(?:цену|бюджет)", text):
            add(PatchOperation.CLEAR, PatchField.PRICE)
        else:
            price_range = re.search(r"(?:цена|бюджет)?\s*от\s*(\d+)\s*(?:₽|руб|тг|тенге)?\s+до\s*(\d+)", text)
            price_max = re.search(r"(?:до|не\s+дороже)\s*(\d+)\s*(?:₽|руб|тг|тенге)", text)
            price_min = re.search(r"(?:дороже|цена\s+от)\s*(\d+)\s*(?:₽|руб|тг|тенге)", text)
            if price_range:
                add(PatchOperation.SET, PatchField.PRICE, value={"min": float(price_range.group(1)), "max": float(price_range.group(2))})
            elif price_max:
                add(PatchOperation.SET, PatchField.PRICE, value={"max": float(price_max.group(1))})
            elif price_min:
                add(PatchOperation.SET, PatchField.PRICE, value={"min": float(price_min.group(1))})

        mentioned = self._concepts(text)
        message_stems = set(terms(text))
        mentioned.extend(
            key for key in positions
            if key.startswith("literal:") and key.removeprefix("literal:") in message_stems
        )
        mentioned = list(dict.fromkeys(mentioned))
        current_mentioned = []
        seen_positions: set[int] = set()
        for concept in mentioned:
            position = positions.get(concept)
            if position is not None and id(position) not in seen_positions:
                current_mentioned.append(concept)
                seen_positions.add(id(position))

        keep = re.search(r"\b[оа]ставь\s+(?:только\s+)?(.+)", command_text)
        if keep and current_mentioned and not self._color(keep.group(1)):
            add(PatchOperation.KEEP_ONLY, PatchField.CATEGORY,
                value=current_mentioned)

        remove = re.search(r"\b(?:уб[еёи]р\w*|исключи|без)\s+(.+)", command_text)
        if remove and current_mentioned and not re.search(r"цвет|материал|количеств|тираж|цен|бюджет", text):
            for concept in current_mentioned:
                add(PatchOperation.REMOVE_POSITION, PatchField.CATEGORY,
                    scope=PatchScope.POSITION, position=concept)

        replace = re.search(r"\b(?:замени|поменяй)\s+(.+?)\s+на\s+(.+)$|\bвместо\s+(.+?)\s+(?:добавь|поставь|возьми)\s+(.+)$", text)
        if replace and "цвет" not in text:
            left, right = (replace.group(1), replace.group(2)) if replace.group(1) else (replace.group(3), replace.group(4))
            old = next((c for c in self._concepts(left) if c in positions), None)
            new = next((c for c in self._concepts(right) if c != old), None)
            if old and new:
                add(PatchOperation.REPLACE_POSITION, PatchField.CATEGORY,
                    scope=PatchScope.POSITION, position=old, replacement=new,
                    value={"color": (self._color(right).model_dump() if self._color(right) else None)})

        if re.search(r"\b(?:д[ао]бав\w*|плюс|ещ[её]|также)\b", command_text):
            for concept in mentioned:
                if concept not in positions:
                    color = self._color(text)
                    add(PatchOperation.ADD_POSITION, PatchField.CATEGORY,
                        scope=PatchScope.POSITION, position=concept,
                        value={"color": color.model_dump() if color else None})

        clear_color = re.search(
            r"(?:цвет(?:\s+\w+){0,3}\s+не\s+важ|любой\s+цвет|"
            r"убери\s+(?:ограничение\s+по\s+)?цвет)",
            text,
        )
        if clear_color:
            if current_mentioned:
                for concept in current_mentioned:
                    add(PatchOperation.CLEAR, PatchField.COLOR, scope=PatchScope.POSITION, position=concept)
            else:
                add(PatchOperation.CLEAR, PatchField.COLOR)
        else:
            clause_colors = []
            for clause in re.split(r"[,;]|\s+а\s+", text):
                clause_color = self._color(clause)
                clause_concepts = [c for c in self._concepts(clause) if c in positions]
                if clause_color and clause_concepts:
                    for concept in clause_concepts:
                        clause_colors.append((concept, clause_color))
            color = self._color(text)
            if color and not any(p.field == PatchField.CATEGORY and p.operation in {PatchOperation.ADD_POSITION, PatchOperation.REPLACE_POSITION} for p in patches):
                if clause_colors:
                    for concept, scoped_color in clause_colors:
                        add(PatchOperation.SET, PatchField.COLOR, scope=PatchScope.POSITION,
                            position=concept, value=scoped_color.model_dump())
                elif current_mentioned:
                    for concept in current_mentioned:
                        add(PatchOperation.SET, PatchField.COLOR, scope=PatchScope.POSITION,
                            position=concept, value=color.model_dump())
                else:
                    add(PatchOperation.SET, PatchField.COLOR, value=color.model_dump())

        material_match = re.search(r"(?:из|материал)\s+(дерев\w*|металл\w*|пластик\w*|хлоп\w*|кож\w*)", text)
        material_clear = re.search(r"(?:материал(?:\s+\w+){0,3}\s+не\s+важ|любой\s+материал|убери\s+(?:ограничение\s+по\s+)?материал)", text)
        if material_clear:
            for concept in current_mentioned or [None]:
                add(PatchOperation.CLEAR, PatchField.MATERIAL,
                    scope=PatchScope.POSITION if concept else PatchScope.GLOBAL, position=concept)
        elif material_match:
            excluded = bool(re.search(r"(?:кроме|не\s+из|исключи)", text))
            value = {"value": material_match.group(1), "excluded": excluded}
            for concept in current_mentioned or [None]:
                add(PatchOperation.SET, PatchField.MATERIAL,
                    scope=PatchScope.POSITION if concept else PatchScope.GLOBAL,
                    position=concept, value=value)

        if re.search(r"\b(?:премиальн|статусн|минималистичн|выгляд\w*\s+дороже|более\s+современн)\w*", text):
            add(PatchOperation.SEMANTIC_REFINE, PatchField.SEMANTIC_PREFERENCE,
                value=text, confidence=.85)
        return MutationResult(patches=patches, normalized_message=normalized)

    def apply(self, intent: HardRequestIntent, result: MutationResult) -> HardRequestIntent:
        plan = HardRequestIntent.model_validate(intent.model_dump())

        def position_for(concept: str | None):
            if concept is None:
                return None
            return self._position_map(plan).get(concept)

        for patch in result.patches:
            target = position_for(patch.position)
            if patch.operation == PatchOperation.SET and patch.field == PatchField.TARGET_CASES:
                plan.target_sets = int(patch.value)
            elif patch.field == PatchField.QUANTITY:
                quantity = QuantityConstraint(minimum=int(patch.value)) if patch.operation == PatchOperation.SET else QuantityConstraint()
                if patch.scope == PatchScope.POSITION and target:
                    target.constraints.quantity = quantity if quantity.required else None
                else:
                    plan.quantity = quantity
            elif patch.field == PatchField.PRICE:
                owner = target.constraints if patch.scope == PatchScope.POSITION and target else plan
                if patch.operation == PatchOperation.CLEAR:
                    owner.price_min = owner.price_max = None
                else:
                    owner.price_min = patch.value.get("min")
                    owner.price_max = patch.value.get("max")
            elif patch.field == PatchField.COLOR:
                color = None if patch.operation == PatchOperation.CLEAR else ColorConstraint.model_validate(patch.value)
                if patch.scope == PatchScope.POSITION and target:
                    target.constraints.color = color
                else:
                    plan.color = color or {"mode": "BASE_COLORS", "values": []}
                    for item in plan.positions:
                        item.constraints.color = color
            elif patch.field == PatchField.MATERIAL:
                owner = target.constraints if patch.scope == PatchScope.POSITION and target else plan
                if patch.operation == PatchOperation.CLEAR:
                    owner.materials = []
                    if hasattr(owner, "excluded_materials"):
                        owner.excluded_materials = []
                elif patch.value.get("excluded"):
                    if hasattr(owner, "excluded_materials"):
                        owner.excluded_materials = [patch.value["value"]]
                    else:
                        plan.exclusions = list(dict.fromkeys(plan.exclusions + [patch.value["value"]]))
                else:
                    owner.materials = [patch.value["value"]]
            elif patch.operation == PatchOperation.ADD_POSITION:
                if position_for(patch.position) is None:
                    constraints = PositionConstraint(color=(patch.value or {}).get("color"))
                    plan.positions.append(CompositePosition(category_text=patch.position, constraints=constraints))
            elif patch.operation == PatchOperation.REMOVE_POSITION:
                plan.positions = [p for p in plan.positions if p is not target]
            elif patch.operation == PatchOperation.KEEP_ONLY:
                wanted = set(patch.value)
                plan.positions = [p for p in plan.positions if any(c in wanted for c in self._concepts(p.category_text))]
            elif patch.operation == PatchOperation.REPLACE_POSITION and target:
                target.category_text = patch.replacement
                target.constraints = PositionConstraint(color=(patch.value or {}).get("color"))
            elif patch.operation == PatchOperation.SEMANTIC_REFINE:
                plan.semantic_preferences = list(dict.fromkeys(plan.semantic_preferences + [str(patch.value)]))
        return HardRequestIntent.model_validate(plan.model_dump())
