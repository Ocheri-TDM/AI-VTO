"""Document settings and edits; commercial fields are server-owned snapshots."""

import re
from decimal import ROUND_HALF_UP, Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.services import ColorNormalizer, normalize_text

ACTIVE = ("QUEUED", "PREPARING_ASSETS", "RENDERING", "VALIDATING")
MODEL_NAME = "u2net"
MODEL_MD5 = "60024c5c889badc19c04ad937298a77b"
PIPELINE = "u2net-60024c5c-cutout-v1"


class Theme(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(default="Товарная подборка", min_length=1, max_length=160)
    client: str = Field(default="", max_length=100)
    subtitle: str = Field(default="", max_length=220)
    palette: list[str] = Field(default_factory=lambda: ["#163E52", "#C9AB78"], min_length=1, max_length=4)
    cover: bool = True
    show_brand: bool = True
    use_logo: bool = False
    logo: str | None = None
    ending_note: str = Field(default="", max_length=500)

    @field_validator("palette")
    @classmethod
    def colors(cls, values):
        import re

        if not all(re.fullmatch(r"#[0-9a-fA-F]{6}", v) for v in values):
            raise ValueError("Используйте цвета #RRGGBB")
        return [v.upper() for v in values]


class ItemEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    offer_id: str
    display_name: str = Field(min_length=1, max_length=220)
    caption: str = Field(default="", max_length=220)
    image_url: str | None = None


class DocumentEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)
    settings: Theme
    items: list[ItemEdit] = Field(min_length=1, max_length=200)
    category_order: list[str] = Field(default_factory=list, max_length=200)
    original_fallback_ids: list[str] = Field(default_factory=list, max_length=200)


class DocumentCreate(BaseModel):
    research_id: str
    selection_version: int = Field(ge=0)


class Generate(BaseModel):
    version: int = Field(ge=1)


class Approve(BaseModel):
    asset_hashes: list[str] = Field(max_length=200)


def price_label(value):
    if value is None:
        return "Цена по запросу"
    amount = Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return f"{int(amount):,}".replace(",", " ") + " ₸ / шт."


def short_display_name(source):
    name = re.split(r"\s[—–]\s", source)[0].strip()
    head, comma, tail = name.rpartition(",")
    # Parent titles can retain an old color while the actual variant suffix
    # differs. A standalone color suffix is not part of the product model.
    if comma and any(re.fullmatch(pattern, normalize_text(tail)) for _, pattern in ColorNormalizer.patterns):
        name = head.strip()
    if len(name) > 110:
        name = name[:110].rsplit(" ", 1)[0].rstrip(" ,") + "…"
    return name
