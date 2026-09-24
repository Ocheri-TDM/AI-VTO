from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .intent import CategoryResolver, ColorIntentResolver
from .models import Budget, ColorGroup, Product, SearchIntent, SoftPreferences, utcnow
from .services import normalize_text


class CoverageEntry(BaseModel):
    supplier: str
    category: str
    colors: list[ColorGroup] = Field(default_factory=list)
    quantity: int = 1
    searched_at: datetime = Field(default_factory=utcnow)
    status: Literal["completed", "limited", "failed"] = "completed"
    pages_scanned: int = 0


class SearchCoverage(BaseModel):
    entries: list[CoverageEntry] = Field(default_factory=list)

    def missing(
        self, intent: SearchIntent, suppliers: list[str], now: datetime | None = None
    ) -> dict[str, list[str]]:
        now = now or utcnow()
        result = {}
        for supplier in suppliers:
            missing = []
            for category in intent.categories:
                # A limited or failed attempt is still an attempt: do not auto-retry within TTL.
                covered = any(
                    e.supplier == supplier
                    and e.category == category
                    and e.searched_at + timedelta(hours=1) > now
                    and e.quantity <= (intent.quantity or 1)
                    and (not e.colors or set(intent.colors).issubset(e.colors) and bool(intent.colors))
                    for e in self.entries
                )
                if not covered:
                    missing.append(category)
            if missing:
                result[supplier] = missing
        return result


class ViewFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")
    categories: list[str] | None = None
    excluded_categories: list[str] = Field(default_factory=list)
    colors: list[ColorGroup] | None = None
    excluded_colors: list[ColorGroup] = Field(default_factory=list)
    budget: Budget | None = None
    quantity: int | None = Field(default=None, ge=1)
    hidden_ids: list[str] = Field(default_factory=list, max_length=2000)
    selection: Literal["all", "selected", "unselected"] = "all"


class SearchState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filters: ViewFilters = Field(default_factory=ViewFilters)
    sorting: Literal["relevance", "price_asc", "price_desc", "darker"] = "relevance"
    preferences: SoftPreferences = Field(default_factory=SoftPreferences)
    selected_ids: list[str] = Field(default_factory=list, max_length=2000)
    version: int = 0
    intent_version: int = 1


class RankedProduct(BaseModel):
    product: Product
    score: float = Field(ge=0, le=1)
    match_reasons: list[str]


class ProductRankingService:
    tags = {
        "premium": ["кожа", "parker", "waterman", "преми", "металл"],
        "minimal": ["минимал", "лаконич", "однотон"],
        "technology": ["usb", "заряд", "аккумулятор", "беспровод"],
        "eco": ["бамбук", "переработ", "эколог", "хлопок"],
        "business": ["делов", "документ", "ежедневник", "ручка"],
        "creative": ["дизайн", "необыч", "ярк"],
        "sport": ["спорт", "бег", "фитнес"],
    }

    def rank(
        self, products: list[Product], intent: SearchIntent, preferences: SoftPreferences
    ) -> list[RankedProduct]:
        result = []
        for p in products:
            score, reasons = 0.1, []
            if p.category in intent.categories:
                score += 0.2
                reasons.append("соответствует категории")
            if p.stock_quantity is not None and p.stock_quantity >= (intent.quantity or 1):
                score += 0.2
                reasons.append("доступно требуемое количество")
            colors = {c.normalized_color for c in p.colors}
            if not intent.colors or colors.intersection(
                ColorIntentResolver().resolve(intent.colors).acceptable
            ):
                score += 0.15
                reasons.append("подходящий оттенок")
            if p.price_kzt is not None and (
                not intent.budget
                or (intent.budget.max is None or p.price_kzt <= intent.budget.max)
                and (intent.budget.min is None or p.price_kzt >= intent.budget.min)
            ):
                score += 0.1
                reasons.append("соответствует бюджету" if intent.budget else "цена подтверждена")
            text = normalize_text(p.name + " " + (p.description or ""))
            words = [
                normalize_text(x)
                for x in intent.keywords + ([intent.material] if intent.material else [])
                if x.strip()
            ]
            if words and any(x in text for x in words):
                score += 0.1
                reasons.append("совпадают ключевые слова")
            matches = [
                key
                for key, enabled in preferences.model_dump().items()
                if enabled and any(t in text for t in self.tags[key])
            ]
            if matches:
                score += 0.15
                reasons.append("есть текстовые признаки стиля: " + ", ".join(matches))
            result.append(RankedProduct(product=p, score=round(min(score, 1), 3), match_reasons=reasons))
        return sorted(result, key=lambda x: (-x.score, x.product.price_kzt or 0, x.product.id))


def current_view(products: list[Product], intent: SearchIntent, state: SearchState) -> list[RankedProduct]:
    f = state.filters
    acceptable = ColorIntentResolver().resolve(f.colors or [], f.excluded_colors).acceptable
    visible = []
    for p in products:
        if f.selection == "selected" and p.id not in state.selected_ids:
            continue
        if f.selection == "unselected" and p.id in state.selected_ids:
            continue
        if p.category and not CategoryResolver().matches(p.name, [p.category]):
            continue
        colors = {c.normalized_color for c in p.colors}
        if p.id in f.hidden_ids or p.category in f.excluded_categories:
            continue
        if f.categories is not None and p.category not in f.categories:
            continue
        if colors.intersection(f.excluded_colors) or f.colors and not colors.intersection(acceptable):
            continue
        required_quantity = f.quantity or intent.quantity
        if required_quantity is not None and (
            p.stock_quantity is None or p.stock_quantity < required_quantity
        ):
            continue
        if f.budget and p.price_kzt is None:
            continue
        if f.budget and (
            (f.budget.max is not None and p.price_kzt > f.budget.max)
            or (f.budget.min is not None and p.price_kzt < f.budget.min)
        ):
            continue
        visible.append(p)
    ranked = ProductRankingService().rank(visible, intent, state.preferences)
    if state.sorting in ("price_asc", "price_desc"):
        ranked.sort(key=lambda x: (x.product.price_kzt, x.product.id), reverse=state.sorting == "price_desc")
    if state.sorting == "darker":
        ranked.sort(
            key=lambda x: (
                not any(
                    c.normalized_color in (ColorGroup.NAVY, ColorGroup.DARK_BLUE, ColorGroup.BLACK)
                    for c in x.product.colors
                )
            )
        )
    return ranked
