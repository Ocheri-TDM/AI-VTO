from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.models import ColorGroup, ProductColor, SearchIntent, SearchSnapshot, SupplierStatus


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=2000)
    refresh: bool = False

    @field_validator("query")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Введите запрос для поиска.")
        return value.strip()


class FilterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_price_kzt: int | None = Field(default=None, ge=0)
    colors: list[ColorGroup] | None = Field(default=None, max_length=20)


class ProductCard(BaseModel):
    id: str
    name: str
    category: str | None
    description: str | None
    price_kzt: int | None
    colors: list[ProductColor]
    primary_image: str | None
    images: list[str]
    duplicate_group_id: str | None


class SearchResponse(BaseModel):
    id: str
    status: Literal["queued", "running", "completed", "partial", "failed"]
    intent: SearchIntent
    products: list[ProductCard]
    suppliers: list[SupplierStatus]
    created_at: datetime
    expires_at: datetime
    cache_hit: bool

    @classmethod
    def from_snapshot(cls, snapshot: SearchSnapshot) -> "SearchResponse":
        if snapshot.state:
            from app.domain.search_state import SearchState, current_view

            view = current_view(
                snapshot.products, snapshot.intent, SearchState.model_validate(snapshot.state)
            )
            snapshot = snapshot.model_copy(update={"products": [x.product for x in view]})
        return cls.model_validate(snapshot.model_dump())
