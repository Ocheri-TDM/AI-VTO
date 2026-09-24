from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def utcnow() -> datetime:
    return datetime.now(UTC)


class ColorGroup(StrEnum):
    NAVY = "NAVY"
    DARK_BLUE = "DARK_BLUE"
    BLUE = "BLUE"
    LIGHT_BLUE = "LIGHT_BLUE"
    ROYAL_BLUE = "ROYAL_BLUE"
    BLUE_GREY = "BLUE_GREY"
    BLACK = "BLACK"
    WHITE = "WHITE"
    GREY = "GREY"
    GREEN = "GREEN"
    RED = "RED"
    YELLOW = "YELLOW"
    ORANGE = "ORANGE"
    PURPLE = "PURPLE"
    PINK = "PINK"
    BROWN = "BROWN"
    BEIGE = "BEIGE"
    SILVER = "SILVER"
    GOLD = "GOLD"
    UNKNOWN = "UNKNOWN"


class ProductColor(BaseModel):
    original_color: str
    normalized_color: ColorGroup = ColorGroup.UNKNOWN


class AvailabilityRecord(BaseModel):
    location_code: str | None = None
    location_label: str | None = None
    state: Literal[
        "ON_HAND", "INCOMING", "FACTORY", "WAITING_SHIPMENT", "RECEIVING",
        "PREORDER", "ORDER_ON_DEMAND", "PRODUCTION", "REMOTE_STOCK", "UNKNOWN",
    ] = "UNKNOWN"
    total_quantity: int | None = Field(default=None, ge=0)
    free_quantity: int | None = Field(default=None, ge=0)
    reserved_quantity: int | None = Field(default=None, ge=0)
    expected_at: datetime | None = None
    lead_time_min_days: int | None = Field(default=None, ge=0)
    lead_time_max_days: int | None = Field(default=None, ge=0)
    observed_at: datetime = Field(default_factory=utcnow)
    parser_version: str
    source_label: str
    source_evidence: dict[str, Any] = Field(default_factory=dict)
    confidence: Literal["HIGH", "MEDIUM", "LOW"] = "HIGH"


class Product(BaseModel):
    """A timestamped supplier observation, never a master catalog record.

    Each priced/stocked color variant is a separate Product. Quantity must
    refer to that variant's available warehouse units, excluding transit.
    """

    id: str
    supplier: str
    source_url: str
    name: str
    category: str | None = None
    description: str | None = None
    original_price: Decimal | None = Field(default=None, ge=0)
    original_currency: str | None = None
    price_kzt: int | None = Field(default=None, ge=0)
    colors: list[ProductColor] = Field(default_factory=list)
    primary_image: str | None = None
    images: list[str] = Field(default_factory=list)
    stock_quantity: int | None = Field(default=None, ge=0)
    incoming_quantity: int | None = Field(default=None, ge=0)
    reserved_quantity: int | None = Field(default=None, ge=0)
    total_quantity: int | None = Field(default=None, ge=0)
    incoming_date: datetime | None = None
    availability_status: str | None = None
    incoming_status: str | None = None
    source_availability_payload: dict[str, Any] = Field(default_factory=dict)
    availability_parser_version: str = "availability-v1"
    availability_records: list[AvailabilityRecord] = Field(default_factory=list)
    available: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)
    fetched_at: datetime = Field(default_factory=utcnow)
    duplicate_group_id: str | None = None
    material: str | None = None
    brand: str | None = None
    dimensions: str | None = None
    capacity: str | None = None
    features: list[str] = Field(default_factory=list)
    evidence: dict[str, dict[str, Any]] = Field(default_factory=dict)


class Budget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    min: int | None = Field(default=None, ge=0)
    max: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def ordered(self):
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("Budget min exceeds max")
        return self


class SoftPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid")
    premium: bool = False
    minimal: bool = False
    technology: bool = False
    eco: bool = False
    business: bool = False
    creative: bool = False
    sport: bool = False


class SearchIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    raw_query: str = Field(min_length=1, max_length=2000)
    quantity: int | None = Field(default=None, ge=1, le=10_000_000)
    categories: list[str] = Field(default_factory=list, max_length=12)
    colors: list[ColorGroup] = Field(default_factory=list, max_length=20)
    budget: Budget | None = None
    branding: str | None = None
    style: str | None = None
    material: str | None = None
    client: str | None = None
    event: str | None = None
    premium_level: str | None = None
    keywords: list[str] = Field(default_factory=list, max_length=20)
    excluded_categories: list[str] = Field(default_factory=list, max_length=12)
    excluded_colors: list[ColorGroup] = Field(default_factory=list, max_length=20)
    sorting: Literal["relevance", "price_asc", "price_desc", "darker"] = "relevance"
    preferences: SoftPreferences = Field(default_factory=SoftPreferences)
    attributes: dict[str, str] = Field(default_factory=dict)
    availability_mode: Literal["ALLOW_INCOMING", "AVAILABLE_NOW_ONLY", "WAIT_ALLOWED"] = "ALLOW_INCOMING"

    @field_validator("budget", mode="before")
    @classmethod
    def legacy_budget(cls, value):
        return {"max": value} if isinstance(value, int) else value

    @field_validator("categories", "excluded_categories", mode="before")
    @classmethod
    def canonical_categories(cls, value):
        return list(dict.fromkeys(str(x).lower() for x in value))


class ActionType(StrEnum):
    OPEN_PAGE = "OPEN_PAGE"
    SEARCH = "SEARCH"
    OPEN_PRODUCT = "OPEN_PRODUCT"
    READ_PRICE = "READ_PRICE"
    READ_STOCK = "READ_STOCK"
    READ_COLOR = "READ_COLOR"
    READ_IMAGES = "READ_IMAGES"
    PAGINATE = "PAGINATE"
    ERROR = "ERROR"


class BrowserAction(BaseModel):
    action: ActionType
    supplier: str
    url: str | None = None
    timestamp: datetime = Field(default_factory=utcnow)
    details: dict[str, Any] = Field(default_factory=dict)


class ProviderResult(BaseModel):
    products: list[Product] = Field(default_factory=list)
    traces: list[BrowserAction] = Field(default_factory=list)
    pages_scanned: int = 0
    warnings: list[str] = Field(default_factory=list)


class SupplierStatus(BaseModel):
    supplier: str
    status: Literal["pending", "running", "completed", "failed"] = "pending"
    error_code: str | None = None
    message: str | None = None
    products_discovered: int = 0
    products_accepted: int = 0
    products_rejected: int = 0
    pages_scanned: int = 0
    duration_ms: int = 0
    warnings: list[str] = Field(default_factory=list)


class SearchSnapshot(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    status: Literal["queued", "running", "completed", "partial", "failed"] = "queued"
    intent: SearchIntent
    products: list[Product] = Field(default_factory=list)
    suppliers: list[SupplierStatus] = Field(default_factory=list)
    traces: list[BrowserAction] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime
    cache_hit: bool = False
    state: dict[str, Any] = Field(default_factory=dict)
    coverage: dict[str, Any] = Field(default_factory=dict)
