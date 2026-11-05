"""Document-owned visual material settings, separate from commercial snapshots."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

MaterialRole = Literal["COVER_PHOTO", "BACKGROUND", "DECORATION"]
Scope = Literal["COVER", "PRODUCTS", "DOCUMENT"]
Position = Literal["AUTO", "TOP", "BOTTOM", "LEFT", "RIGHT"]


class MaterialEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    enabled: bool = True
    scope: Scope = "DOCUMENT"
    expression: Literal["SUBTLE", "NORMAL", "BOLD"] = "NORMAL"
    position: Position = "AUTO"
    crop: bool = False
    focal_x: float = Field(default=0.5, ge=0, le=1)
    focal_y: float = Field(default=0.5, ge=0, le=1)
    opacity: float = Field(default=1, ge=0, le=1)
    harmonize: bool = False
    required: bool = False
    description: str = Field(default="", max_length=160)


class MaterialRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asset_ref: str
    preset: Literal["AUTO", "TOP", "BOTTOM", "LEFT", "RIGHT"] = "AUTO"


def on_page(material, layout):
    return (
        material["scope"] == "DOCUMENT"
        or material["scope"] == "COVER"
        and layout in ("cover", "title")
        or material["scope"] == "PRODUCTS"
        and layout not in ("cover", "title", "ending")
    )


def eligible(material):
    return material.get("enabled") and material.get("status") == "READY"


def plan_materials(pages, materials):
    """Allowed scope is permission, not mandatory repetition on every leaf."""
    for index, page in enumerate(pages):
        available = [m for m in materials if eligible(m) and on_page(m, page.layout)]
        selected = [m for m in available if m["role"] == "BACKGROUND"]
        if page.layout != "ending":
            selected += [m for m in available if m["role"] == "COVER_PHOTO"]
        decorations = [m for m in available if m["role"] == "DECORATION"]
        if decorations:
            offset = index * 2 % len(decorations)
            selected += (decorations[offset:] + decorations[:offset])[:2]
        page.materials = [MaterialRequest(asset_ref=m["id"], preset=m["position"]) for m in selected]


def validate_material_requests(pages, materials):
    by_id = {m["id"]: m for m in materials if eligible(m)}
    for page in pages:
        refs = set()
        for request in page.materials:
            material = by_id.get(request.asset_ref)
            if (
                not material
                or request.asset_ref in refs
                or not on_page(material, page.layout)
                or material["role"] == "COVER_PHOTO"
                and page.layout == "ending"
                or material["position"] != "AUTO"
                and request.preset not in ("AUTO", material["position"])
            ):
                raise ValueError("INVALID_MATERIAL_REFERENCE_OR_SCOPE")
            refs.add(request.asset_ref)
