"""Closed design grammar; no commercial text or executable content from AI."""

from collections import Counter
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.presentation_composition import TITLE_GEOMETRY
from app.domain.presentation_decoration import DecorationPlan, decoration_plan
from app.domain.presentation_materials import MaterialRequest, plan_materials, validate_material_requests

Style = Literal["editorial", "premium-dark", "modern-graphic"]
Layout = Literal["cover", "title", "hero", "asymmetric", "variants", "lineup", "category", "ending"]
Motif = Literal["none", "rule", "disc", "plane", "frame"]


class DesignPage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    layout: Layout
    product_refs: list[str] = Field(max_length=3)
    heading: str = Field(default="", max_length=110)
    motif: Motif = "none"
    materials: list[MaterialRequest] = Field(default_factory=list, max_length=8)
    title_family: Literal["centered", "split", "band", "arch", "open"] | None = None
    logo_ref: str | None = None
    variant: Literal[0, 1, 2] = 0


class DesignPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    style: Style
    style_version: Literal[2, 3] = 2
    design_seed: int = Field(ge=0, le=2_147_483_647)
    palette_order: list[int] = Field(min_length=1, max_length=6)
    pages: list[DesignPage] = Field(min_length=1, max_length=202)
    material_schema_version: Literal[1] | None = None
    decoration: DecorationPlan | None = None
    schema_version: Literal[1, 2] = 1
    cover_policy: Literal[1] | None = None
    renderer_version: Literal[1] | None = None


def same_model(items):
    return (
        len(items) > 1
        and len({i.get("product_id", i["offer_id"]) for i in items}) == 1
        and len({i["display_name"] for i in items}) == 1
        and all(not i.get("caption") for i in items)
    )


def long_copy(item):
    return len(item["display_name"]) > 76 or len(item.get("caption", "")) > 40


def validate_plan(plan, items, settings, materials=None):
    plan = DesignPlan.model_validate(plan)
    title_only = settings.get("cover_policy") == 1
    if (
        plan.schema_version != (2 if title_only else 1)
        or plan.cover_policy != settings.get("cover_policy")
        or plan.renderer_version != settings.get("renderer_version")
    ):
        raise ValueError("PLAN_POLICY_VERSION_MISMATCH")
    if plan.style_version != settings.get("style_version", 2):
        raise ValueError("STYLE_VERSION_MISMATCH")
    if plan.style_version == 3 and plan.style != "premium-dark":
        raise ValueError("UNSUPPORTED_STYLE_VERSION")
    by_id = {i["offer_id"]: i for i in items}
    if plan.design_seed != settings.get("design_seed", 0):
        raise ValueError("DESIGN_SEED_MISMATCH")
    if sorted(plan.palette_order) != list(range(len(settings["palette"]))):
        raise ValueError("INVALID_PALETTE_ROLES")
    if settings.get("palette_plan") and plan.palette_order != list(range(len(settings["palette"]))):
        raise ValueError("PRIMARY_PALETTE_ROLE_IS_LOCKED")
    expected_decoration = decoration_plan(settings)
    if (plan.decoration is None) != (expected_decoration is None):
        raise ValueError("DECORATION_VERSION_MISMATCH")
    if plan.decoration and (
        plan.decoration.seed != expected_decoration.seed
        or plan.decoration.version != 1
        or plan.decoration.enabled != expected_decoration.enabled
        or plan.decoration.intensity != expected_decoration.intensity
    ):
        raise ValueError("DECORATION_SETTINGS_MISMATCH")
    allowed_headings = {"", "Выбранные товары", "Товарная подборка"} | {i["category_label"] for i in items}
    placements = []
    for n, page in enumerate(plan.pages):
        if page.heading not in allowed_headings or any(id not in by_id for id in page.product_refs):
            raise ValueError("UNKNOWN_DESIGN_REFERENCE")
        count = len(page.product_refs)
        if page.layout == "title":
            if (
                not title_only
                or n != 0
                or count
                or page.title_family not in TITLE_GEOMETRY
                or page.logo_ref != settings.get("resolved_logo", {}).get("asset_id")
                or not page.logo_ref
            ):
                raise ValueError("INVALID_TITLE_PAGE")
        elif page.layout == "cover":
            if title_only:
                raise ValueError("PRODUCT_COVER_FORBIDDEN")
            if n != 0 or not settings["cover"] or not count:
                raise ValueError("INVALID_COVER")
        elif page.layout == "ending":
            if n != len(plan.pages) - 1 or not settings.get("ending_note") or count:
                raise ValueError("INVALID_ENDING")
        else:
            required = {
                "hero": (1,),
                "asymmetric": (2,),
                "variants": (2, 3),
                "lineup": (3,),
                "category": (2, 3),
            }
            if count not in required[page.layout]:
                raise ValueError("INVALID_LAYOUT_DENSITY")
            group = [by_id[id] for id in page.product_refs]
            neutral = {"Выбранные товары", "Товарная подборка"}
            labels = {i["category_label"] for i in group}
            if (
                len(labels) > 1
                and page.heading not in neutral
                or page.heading
                and page.heading not in neutral
                and page.heading not in labels
            ):
                raise ValueError("HEADING_DOES_NOT_DESCRIBE_PAGE")
            if page.layout == "variants" and not same_model(group):
                raise ValueError("VARIANTS_REQUIRE_SAME_MODEL")
            if (
                count == 3
                and page.layout != "variants"
                and any(len(i["display_name"]) > 60 or len(i.get("caption", "")) > 20 for i in group)
            ):
                raise ValueError("THREE_ITEMS_REQUIRE_SHORT_COPY")
            if page.layout != "hero" and any(long_copy(i) for i in group):
                raise ValueError("LONG_COPY_REQUIRES_HERO")
            placements.extend(page.product_refs)
        if page.layout != "title" and (page.title_family is not None or page.logo_ref is not None):
            raise ValueError("TITLE_FIELDS_ON_PRODUCT_PAGE")
    if Counter(placements) != Counter(by_id.keys()) or len(placements) != len(by_id):
        raise ValueError("DESIGN_SELECTION_MISMATCH")
    if title_only and (plan.pages[0].layout != "title" or sum(p.layout == "title" for p in plan.pages) != 1):
        raise ValueError("MISSING_TITLE")
    if sum(p.layout == "cover" for p in plan.pages) != (0 if title_only else int(settings["cover"])):
        raise ValueError("MISSING_COVER")
    if sum(p.layout == "ending" for p in plan.pages) != int(bool(settings.get("ending_note"))):
        raise ValueError("MISSING_ENDING")
    if any(p.materials for p in plan.pages) and (
        plan.material_schema_version != 1 or plan.style != "premium-dark" or plan.style_version != 3
    ):
        raise ValueError("UNSUPPORTED_MATERIAL_PLAN")
    validate_material_requests(plan.pages, materials or [])
    return plan


def deterministic_plan(items, settings, category_order, geometry=None, materials=None):
    style = settings.get("style", "editorial")
    if style == "classic":
        style = "editorial"
    seed = settings.get("design_seed", 0)
    pages = []
    motif = {"editorial": "rule", "premium-dark": "disc", "modern-graphic": "plane"}[style]
    if seed % 2:
        motif = "frame"
    title_only = settings.get("cover_policy") == 1
    if title_only:
        pages.append(
            DesignPage(
                layout="title",
                product_refs=[],
                motif="none",
                title_family=list(TITLE_GEOMETRY)[seed % len(TITLE_GEOMETRY)],
                logo_ref=settings["resolved_logo"]["asset_id"],
            )
        )
    elif settings["cover"]:
        pages.append(DesignPage(layout="cover", product_refs=[i["offer_id"] for i in items[:3]], motif=motif))
    shapes = {g["offer_id"]: g for g in (geometry or [])}
    for category in category_order or list(dict.fromkeys(i["category_key"] for i in items)):
        group = [i for i in items if i["category_key"] == category]
        while group:
            count = 1
            layout = "hero"
            first_shape = shapes.get(group[0]["offer_id"], {})
            if not long_copy(group[0]) and first_shape.get("aspect", 1) < 3:
                if len(group) >= 3 and all(not long_copy(i) for i in group[:3]):
                    if same_model(group[:3]) and seed % 2 == 0:
                        count, layout = 3, "variants"
                    elif (
                        style == "modern-graphic"
                        and seed % 2 == 0
                        and all(
                            len(i["display_name"]) <= 60 and len(i.get("caption", "")) <= 20
                            for i in group[:3]
                        )
                    ):
                        count, layout = 3, "lineup"
                if count == 1 and len(group) >= 2 and not long_copy(group[1]):
                    count = 2
                    layout = "asymmetric" if (style == "editorial") != bool(seed % 2) else "category"
            pages.append(
                DesignPage(
                    layout=layout,
                    product_refs=[i["offer_id"] for i in group[:count]],
                    heading=group[0]["category_label"],
                    motif=motif,
                    variant=seed % 3 if title_only else 0,
                )
            )
            group = group[count:]
    if settings.get("ending_note"):
        pages.append(DesignPage(layout="ending", product_refs=[], motif=motif))
    if materials and style == "premium-dark" and settings.get("style_version") == 3:
        plan_materials(pages, materials)
    return validate_plan(
        DesignPlan(
            style=style,
            style_version=settings.get("style_version", 2),
            design_seed=seed,
            palette_order=list(range(len(settings["palette"]))),
            pages=pages,
            material_schema_version=1 if materials else None,
            decoration=decoration_plan(settings),
            schema_version=2 if title_only else 1,
            cover_policy=settings.get("cover_policy"),
            renderer_version=settings.get("renderer_version"),
        ),
        items,
        settings,
        materials,
    )
