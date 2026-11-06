"""Durable research state; observation freshness is independent of its lifetime."""

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import NAMESPACE_URL, uuid4, uuid5

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator

from app.domain.models import ColorGroup, Product, SearchIntent, utcnow
from app.domain.query_constraints import AttributeConstraints


class CoverageStatus(StrEnum):
    LIMITED = "LIMITED"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"
    CAPTCHA = "CAPTCHA"
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"


class ResearchJobStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    PARTIAL = "PARTIAL"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ProductBranch(BaseModel):
    constraints: AttributeConstraints = Field(default_factory=AttributeConstraints)
    id: str = Field(default_factory=lambda: str(uuid4()))
    source_text: str = ""
    order: int = 0
    resolved: bool | None = None
    category: str
    label: str
    colors: list[str] = Field(default_factory=list)
    quantity: int | None = None
    materials: list[str] = Field(default_factory=list)
    min_price: float | None = None
    max_price: float | None = None
    origins: dict[str, str] = Field(default_factory=dict)
    scope: Literal["STANDALONE", "SET", "PACKAGING", "ACCESSORY", "COMPONENT", "ASSOCIATED"] = "STANDALONE"
    subject: str = ""
    required_concepts: list[str] = Field(default_factory=list)
    include_sets: bool = False
    brands: list[str] = Field(default_factory=list)
    excluded_brands: list[str] = Field(default_factory=list)
    models: list[str] = Field(default_factory=list)


class ResearchIntent(SearchIntent):
    """Category and quantity are hard; preferences never fabricate attributes."""

    color_mode: Literal["family", "strict"] = "family"
    primary_colors: list[ColorGroup] = Field(default_factory=list)
    discovery: dict[str, list[str]] = Field(default_factory=dict)
    soft_terms: list[str] = Field(default_factory=list)
    branches: list[ProductBranch] = Field(default_factory=list)
    brands: list[str] = Field(default_factory=list)
    excluded_brands: list[str] = Field(default_factory=list)
    models: list[str] = Field(default_factory=list)
    identity_version: str | None = None
    identity_issues: list[str] = Field(default_factory=list)


class ResearchBudget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_category_branches: int = Field(default=12, ge=1, le=12)
    max_query_expansions: int = Field(default=4, ge=1, le=8)
    max_supplier_routes: int = Field(default=72, ge=6, le=576)
    max_seconds: int = Field(default=600, ge=10, le=14400)


class TraversalCursor(BaseModel):
    """Committed after each product; pending URLs survive timeouts/restarts."""

    listing_url: str | None = None
    page_number: int = 0
    pending_urls: list[str] = Field(default_factory=list)
    failed_urls: list[str] = Field(default_factory=list)
    visited_urls: list[str] = Field(default_factory=list)
    next_url: str | None = None
    listing_loaded: bool = False
    exhausted: bool = False


class ResearchCoverage(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    supplier: str
    category: str
    query: str
    query_origin: str = "taxonomy"
    query_reason: str = "Category alias"
    query_confidence: float = Field(default=1, ge=0, le=1)
    route: str | None = None
    pages_scanned: int = 0
    products_seen: int = 0
    listings_seen: int = 0
    products_validated: int = 0
    products_rejected: int = 0
    parsing_errors: int = 0
    total_results_reported: int | None = None
    total_unit: Literal["families", "offers", "unknown"] = "unknown"
    pagination_exhausted: bool = False
    status: CoverageStatus = CoverageStatus.LIMITED
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_code: str | None = None
    cursor: TraversalCursor = Field(default_factory=TraversalCursor)

    @model_validator(mode="after")
    def honest_completion(self):
        if self.status == CoverageStatus.COMPLETE and (
            not self.pagination_exhausted
            or self.cursor.pending_urls
            or self.cursor.failed_urls
            or self.parsing_errors
        ):
            raise ValueError("COMPLETE requires exhausted pagination and no unprocessed products/errors")
        return self


class ListingPage(BaseModel):
    urls: list[str] = Field(default_factory=list)
    next_url: str | None = None
    total: int | None = None
    total_unit: Literal["families", "offers", "unknown"] = "unknown"
    exhausted: bool = False


class ResearchFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")
    categories: list[str] = Field(default_factory=list)
    colors: list[ColorGroup] = Field(default_factory=list)
    min_price: int | None = Field(default=None, ge=0, le=1_000_000_000, strict=True)
    max_price: int | None = Field(default=None, ge=0, le=1_000_000_000, strict=True)
    attributes: dict[str, list[str]] = Field(default_factory=dict)
    excluded_terms: list[str] = Field(default_factory=list)
    required_terms: list[str] = Field(default_factory=list)
    selected_only: bool = False
    brands: list[str] = Field(default_factory=list)
    excluded_brands: list[str] = Field(default_factory=list)
    models: list[str] = Field(default_factory=list)
    branch_brands: dict[str, list[str]] = Field(default_factory=dict)
    branch_models: dict[str, list[str]] = Field(default_factory=dict)
    materials: list[str] = Field(default_factory=list)
    sizes: list[str] = Field(default_factory=list)
    minimum_quantity: int | None = Field(default=None, ge=1, le=10_000_000, strict=True)
    excluded_materials: list[str] = Field(default_factory=list)
    origins: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def bounded_filters(self):
        if self.min_price is not None and self.max_price is not None and self.min_price > self.max_price:
            raise ValueError("Нижняя граница цены превышает верхнюю.")
        for key, value in self.model_dump().items():
            if isinstance(value, (list, dict)) and len(value) > 200:
                raise ValueError("Слишком много значений фильтра: " + key)
        return self


class EffectiveFilterEdit(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    field: Literal["colors", "brands", "materials", "sizes", "minimum_quantity", "min_price", "max_price"]
    action: Literal["set", "clear", "inherit"]
    branch_id: str | None = Field(default=None, max_length=36)
    values: list[str] | None = Field(default=None, max_length=200)
    number: int | float | None = None

    @model_validator(mode="after")
    def valid_value(self):
        import math

        numeric = self.field in ("minimum_quantity", "min_price", "max_price")
        if self.action != "set":
            if self.values is not None or self.number is not None:
                raise ValueError("Снятие/наследование не принимает значение.")
        elif numeric:
            n = self.number
            if n is None or isinstance(n, bool) or not math.isfinite(n) or self.values is not None:
                raise ValueError("Укажите числовое значение.")
            if self.field == "minimum_quantity":
                if not 1 <= n <= 10_000_000 or int(n) != n:
                    raise ValueError("Тираж должен быть целым положительным числом.")
            elif not 0 <= n <= 1_000_000_000:
                raise ValueError("Некорректная цена.")
        elif not self.values or self.number is not None or any(len(v) > 300 for v in self.values):
            raise ValueError("Выберите значение.")
        if self.field == "colors" and self.values:
            for value in self.values:
                ColorGroup(value)
        return self


class ResearchView(BaseModel):
    filters: ResearchFilters = Field(default_factory=ResearchFilters)
    effective_overrides: list[EffectiveFilterEdit] = Field(default_factory=list, max_length=100)
    sorting: Literal["relevance", "price_asc", "price_desc"] = "relevance"
    preferences: list[str] = Field(default_factory=list)
    selected_ids: list[str] = Field(default_factory=list)
    hidden_ids: list[str] = Field(default_factory=list)
    hidden_branch_ids: list[str] = Field(default_factory=list)
    category_version: int = 0
    similar_to: list[str] = Field(default_factory=list)
    version: int = 0


class ResearchSession(BaseModel):
    _prepared_response: dict | None = PrivateAttr(default=None)
    id: str = Field(default_factory=lambda: str(uuid4()))
    chat_id: str
    intent: ResearchIntent
    budget: ResearchBudget = Field(default_factory=ResearchBudget)
    products: list[Product] = Field(default_factory=list)
    coverage: list[ResearchCoverage] = Field(default_factory=list)
    view: ResearchView = Field(default_factory=ResearchView)
    history: list[dict[str, Any]] = Field(default_factory=list)
    messages: list[dict[str, str]] = Field(default_factory=list)
    job_status: ResearchJobStatus = ResearchJobStatus.QUEUED
    revision: int = 0
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    source_mode: str = "live"
    indexed_offer_ids: list[str] = Field(default_factory=list)
    pool_offer_ids: list[str] = Field(default_factory=list)
    pending_offer_ids: list[str] = Field(default_factory=list)
    background_job_ids: list[str] = Field(default_factory=list)
    timings: dict[str, Any] = Field(default_factory=dict)
    unified: bool = False
    purpose_version: str | None = None
    branch_offer_ids: list[list[str]] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def legacy_branch_ids(cls, value):
        if isinstance(value, dict) and isinstance(value.get("intent"), dict):
            value = dict(value)
            value["intent"] = dict(value["intent"])
            value["intent"]["branches"] = [
                dict(
                    b,
                    order=b.get("order", i),
                    id=b.get("id")
                    or str(uuid5(NAMESPACE_URL, f"research:{value.get('id')}:{i}:{b.get('category')}")),
                )
                if isinstance(b, dict)
                else b
                for i, b in enumerate(value["intent"].get("branches", []))
            ]
        return value


class BranchAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["hide", "show", "show_all", "remove", "restore"]
    branch_id: str | None = None
    removal_id: str | None = None

    @model_validator(mode="after")
    def target(self):
        if self.action in ("hide", "show", "remove") and not self.branch_id:
            raise ValueError("branch_id required")
        if self.action == "restore" and not self.removal_id:
            raise ValueError("removal_id required")
        return self


class RefinementAction(StrEnum):
    LOCAL_FILTER = "LOCAL_FILTER"
    LOCAL_RERANK = "LOCAL_RERANK"
    LOCAL_SEMANTIC_REFINE = "LOCAL_SEMANTIC_REFINE"
    SIMILARITY_SEARCH = "SIMILARITY_SEARCH"
    TARGETED_RESEARCH = "TARGETED_RESEARCH"
    EXPAND_RESEARCH = "EXPAND_RESEARCH"
    REFRESH_RESEARCH = "REFRESH_RESEARCH"
    RESET_REFINEMENT = "RESET_REFINEMENT"
    UNDO = "UNDO"
    SELECT = "SELECT"
    SHOW = "SHOW"


class ResearchDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: RefinementAction
    filters: ResearchFilters | None = None
    preference: str | None = Field(default=None, max_length=100)
    query: str | None = Field(default=None, max_length=200)
    product_ids: list[str] = Field(default_factory=list, max_length=100)
    count: int | None = Field(default=None, ge=1, le=100)
    per_category: bool = False
    sorting: Literal["relevance", "price_asc", "price_desc"] | None = None

    @model_validator(mode="after")
    def action_scope(self):
        if self.action != RefinementAction.SELECT:
            self.per_category = False
        if self.action not in (RefinementAction.LOCAL_FILTER, RefinementAction.LOCAL_SEMANTIC_REFINE):
            self.filters = None
        if self.action not in (RefinementAction.SELECT, RefinementAction.SIMILARITY_SEARCH):
            self.product_ids = []
            self.count = None
        if self.action != RefinementAction.TARGETED_RESEARCH:
            self.query = None
        return self
