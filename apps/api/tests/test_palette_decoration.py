"""Color-role truth, geometric safety, legacy isolation and lightweight browsing."""

import copy
import itertools
import json
from pathlib import Path

import pytest
from sqlalchemy import func, select
from test_premium_dark import assets_for
from test_presentation_design import content
from test_presentation_materials import materials_app  # noqa: F401
from test_presentations import edit_body

from app.application.presentation_material_layout import intersects
from app.application.presentation_render import render_document
from app.database.models import PresentationRevision
from app.domain.presentation_decoration import DecorationSettings
from app.domain.presentation_design import deterministic_plan, validate_plan
from app.domain.presentation_palette import (
    HEX,
    NEAR_DISTANCE,
    catalog,
    distance,
    hue_distance,
    lch,
    library,
    manual_plan,
    oklch,
    resolve_choice,
    signature,
    validate_roles,
)


def enhanced():
    value = content()
    palette = catalog("red")["items"][0]
    value["settings"].update(
        style="premium-dark",
        style_version=3,
        palette=palette["colors"],
        palette_plan=palette,
        auto_decoration=DecorationSettings().model_dump(),
    )
    return value


def test_entire_published_library_has_distinct_rendered_roles_gamut_and_contrast():
    pools, stats = library()
    assert sum(map(len, pools.values())) >= 300
    assert len(pools["red"]) >= 40
    assert all(pools.values()) and stats["near_duplicates_removed"] > 0
    values = list(itertools.chain.from_iterable(pools.values()))
    assert len({tuple(sorted(v["colors"])) for v in values}) == len(values)
    signatures = [signature(v) for v in values]
    assert all(distance(a, b) >= NEAR_DISTANCE for a, b in itertools.combinations(signatures, 2))
    for value in values:
        assert min(validate_roles(value).values()) >= 4.5
        assert all(HEX.fullmatch(c) for c in value["roles"].values())
    for value in pools["red"]:
        _, c, hue = lch(value["roles"]["dark_background"])
        assert c > 0.01 and hue_distance(hue, lch(value["primary"])[2]) < 9


def test_published_v1_roles_match_the_shared_frozen_fixture():
    fixture = json.loads(
        (Path(__file__).parent / "fixtures/presentations/palette-explorer/library-v1.json").read_text("utf-8")
    )
    assert fixture["version"] == 1
    assert fixture["palettes"] == [p for values in library()[0].values() for p in values]


@pytest.mark.parametrize("color", ["#FF0000", "#00FF00", "#0000FF", "#808080", "#FDFDFD", "#101010"])
def test_oklch_roundtrip_and_neutral_hue_stability(color):
    restored = oklch(*lch(color))
    assert all(abs(int(restored[n : n + 2], 16) - int(color[n : n + 2], 16)) <= 1 for n in (1, 3, 5))
    assert hue_distance(359, 1) == 2


def test_custom_exact_anchor_manual_provenance_and_authoritative_roles():
    value = catalog("custom", primary="#eF1A52")["items"][0]
    assert value["primary"] == "#EF1A52" and value["locked"]
    forged = copy.deepcopy(value)
    forged["roles"]["dark_background"] = "#0000FF"
    assert resolve_choice(forged, forged["colors"])["roles"] == value["roles"]
    colors = value["colors"].copy()
    colors[0] = "#16A873"
    edited = resolve_choice(value, colors)
    assert edited["manual"] and edited["name"] == "Своя палитра"
    assert edited["origin_preset_id"] == value["preset_id"] and edited["primary"] == "#16A873"
    assert manual_plan(["#808080"])["primary"] == "#808080"


@pytest.mark.parametrize(
    "slot,role",
    [
        (1, "dark_background"),
        (2, "light_background"),
        (3, "dark_accent"),
        (4, "dark_peak"),
        (5, "light_text"),
    ],
)
def test_manual_role_edit_is_used_instead_of_becoming_an_ignored_swatch(slot, role):
    old = catalog("red")["items"][0]
    base = manual_plan(old["colors"], old)
    colors = old["colors"].copy()
    colors[slot] = "#308A65"
    edited = manual_plan(colors, old, edited_slots=[slot])
    assert edited["roles"][role] != base["roles"][role]
    assert min(validate_roles(edited).values()) >= 4.5


@pytest.mark.parametrize("style", ["premium-dark", "editorial", "modern-graphic"])
def test_actual_composited_text_contrast_and_deterministic_geometry(tmp_path, style):
    value = enhanced()
    value["settings"].update(style=style, style_version=3 if style == "premium-dark" else 2)
    assets = assets_for(tmp_path, value["items"])
    value["design_plan"] = deterministic_plan(
        value["items"], value["settings"], value["category_order"]
    ).model_dump()
    original = copy.deepcopy(value)
    first = render_document(tmp_path, "first-" + style, value, assets)
    repeat = render_document(tmp_path, "repeat-" + style, value, assets)
    assert first["sha256"] == repeat["sha256"] and value == original
    assert first["contrast_audit"] and all(c["after"] >= c["required"] for c in first["contrast_audit"])
    assert first["placements"] == [i["offer_id"] for i in value["items"]]
    assert any(p["elements"] for p in first["decoration_plan"]["pages"])
    off = copy.deepcopy(value)
    off.pop("design_plan")
    off["settings"]["auto_decoration"]["enabled"] = False
    clean = render_document(tmp_path, "off-" + style, off, assets)
    assert clean["sha256"] != first["sha256"]
    assert all(not p["elements"] for p in clean["decoration_plan"]["pages"])


def test_legacy_missing_fields_replay_without_enabling_new_features(tmp_path):
    value = content()
    value["settings"].update(style="premium-dark", style_version=3)
    assets = assets_for(tmp_path, value["items"])
    absent = copy.deepcopy(value)
    for key in ("palette_plan", "auto_decoration"):
        absent["settings"].pop(key, None)
    a = render_document(tmp_path, "legacy", absent, assets)
    b = render_document(tmp_path, "null", value, assets)
    assert a["sha256"] == b["sha256"] and "decoration_plan" not in a


def test_palette_order_and_disabled_decor_settings_cannot_be_changed_by_ai():
    value = enhanced()
    plan = deterministic_plan(value["items"], value["settings"], value["category_order"]).model_dump()
    plan["palette_order"].reverse()
    with pytest.raises(ValueError, match="LOCKED"):
        validate_plan(plan, value["items"], value["settings"])
    plan["palette_order"].reverse()
    plan["decoration"]["enabled"] = False
    with pytest.raises(ValueError, match="DECORATION_SETTINGS"):
        validate_plan(plan, value["items"], value["settings"])


def test_alternate_has_real_different_primitives_and_preserves_facts(tmp_path):
    value = enhanced()
    assets = assets_for(tmp_path, value["items"])
    first = render_document(tmp_path, "seed-0", value, assets)
    other = copy.deepcopy(value)
    other["settings"]["design_seed"] = 1
    second = render_document(tmp_path, "seed-1", other, assets)
    assert first["decoration_plan"]["family"] != second["decoration_plan"]["family"]
    assert first["sha256"] != second["sha256"] and first["placements"] == second["placements"]
    assert (
        value["items"] == other["items"]
        and value["settings"]["palette_plan"] == other["settings"]["palette_plan"]
    )


async def test_authenticated_palette_browsing_does_not_mutate_document_or_enqueue(materials_app):  # noqa: F811
    app, client, identifier = materials_app
    doc = (await client.get(f"/api/presentations/{identifier}")).json()
    async with app.state.sessions() as db:
        before = await db.scalar(select(func.count()).select_from(PresentationRevision))
    r = await client.get("/api/presentations/palettes?direction=red&contrast_accent=false")
    assert r.status_code == 200 and r.json()["published_total"] >= 300
    assert all(p["direction"] == "red" and not p["contrast_accent"] for p in r.json()["items"])
    value = r.json()["items"][0]
    damaged = copy.deepcopy(value)
    damaged["roles"]["dark_background"] = "#00FFFF"
    saved = (
        await client.patch(
            f"/api/presentations/{doc['id']}",
            json=edit_body(doc, palette=value["colors"], palette_plan=damaged),
        )
    ).json()
    assert saved["draft"]["settings"]["palette_plan"]["roles"] == value["roles"]
    for page in (1, 2):
        assert (
            await client.get(f"/api/presentations/palettes?direction=blue&page={page}")
        ).status_code == 200
    after = (await client.get(f"/api/presentations/{doc['id']}")).json()
    assert after["version"] == saved["version"]
    async with app.state.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(PresentationRevision)) == before


def test_decor_uses_measured_regions_and_custom_material_priority():
    from app.application.presentation_decoration import DecorationLayers
    from app.domain.presentation_decoration import DecorationPlan
    from app.domain.presentation_design import DesignPage

    layer = DecorationLayers(DecorationPlan(), {})
    protected = [{"kind": "text", "rect": (20, 80, 80, 350)}, {"kind": "photo", "rect": (700, 100, 210, 350)}]
    page = DesignPage(layout="hero", product_refs=["x"])
    layer.resolve(page, 0, protected, [])
    assert layer.pages[0]["elements"]
    assert all(not intersects(e["rect"], p["rect"]) for e in layer.pages[0]["elements"] for p in protected)
    layer.resolve(page, 1, protected, [{"role": "DECORATION"}])
    assert not layer.pages[1]["elements"] and layer.pages[1]["reason"] == "CUSTOM_MATERIAL_PRIORITY"
