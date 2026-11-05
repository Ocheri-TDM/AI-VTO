import asyncio
import copy
import json
from contextlib import closing

import pypdfium2 as pdfium
import pytest
from test_presentations import image_bytes

from app.application.presentation_design import prepare_design
from app.application.presentation_render import render_document
from app.domain.presentation import Theme
from app.domain.presentation_design import DesignPlan, deterministic_plan, validate_plan


def content():
    items = [
        {
            "offer_id": f"vto:{n}",
            "product_id": "melango",
            "display_name": "Рюкзак-мешок Melango",
            "caption": "",
            "brand": "Molti",
            "unit_price_kzt": str(1000 + n),
            "category_key": "backpack",
            "category_label": "Рюкзаки",
            "colors": [{"original_color": color, "normalized_color": norm}],
        }
        for n, color, norm in [(1, "синий", "BLUE"), (2, "серый", "GREY"), (3, "чёрный", "BLACK")]
    ]
    return {
        "document_id": "control",
        "items": items,
        "settings": Theme(title="").model_dump(),
        "category_order": ["backpack"],
        "original_fallback_ids": [],
    }


@pytest.mark.parametrize("style", ["editorial", "premium-dark", "modern-graphic"])
def test_three_styles_preserve_three_variant_facts_and_fit_one_comparison_page(tmp_path, style):
    from app.application.presentation_assets import store_upload

    value = content()
    value["settings"]["style"] = style
    value["items"][0].update(brand=None, unit_price_kzt=None)
    assets = {
        i["offer_id"]: {"key": store_upload(tmp_path, "control", image_bytes(color), prepared=True)}
        for i, color in zip(value["items"], ["blue", "grey", "black"], strict=True)
    }
    result = render_document(tmp_path, style, value, assets)
    assert result["placements"] == ["vto:1", "vto:2", "vto:3"]
    assert result["page_count"] == 2
    assert result["design_plan"]["style"] == style
    assert result["design_plan"]["pages"][1]["layout"] == "variants"
    assert result["sha256"] and len(result["pages"]) == 2
    with pdfium.PdfDocument(str(tmp_path / result["pdf"])) as pdf:
        with closing(pdf[1]) as page, closing(page.get_textpage()) as tp:
            extracted = tp.get_text_range()
            assert "Цена по запросу" in extracted and "₸" in extracted
            assert "None" not in extracted and "UNKNOWN" not in extracted


def test_alternative_changes_layout_and_saved_plan_replays_deterministically():
    value = content()
    first = deterministic_plan(value["items"], value["settings"], value["category_order"])
    next_settings = value["settings"] | {"design_seed": 1}
    other = deterministic_plan(value["items"], next_settings, value["category_order"])
    assert [p.layout for p in first.pages] != [p.layout for p in other.pages]
    assert first == validate_plan(first.model_dump(), value["items"], value["settings"])


@pytest.mark.parametrize(
    "bad", ["lost", "unknown", "duplicate", "density", "code", "price", "heading", "palette"]
)
def test_untrusted_design_plan_cannot_change_commercial_selection_or_inject_content(bad):
    value = content()
    plan = deterministic_plan(value["items"], value["settings"], value["category_order"]).model_dump()
    page = plan["pages"][1]
    if bad == "lost":
        page["product_refs"].pop()
    elif bad == "unknown":
        page["product_refs"][0] = "vto:foreign"
    elif bad == "duplicate":
        page["product_refs"][1] = page["product_refs"][0]
    elif bad == "density":
        page["layout"] = "hero"
    elif bad == "code":
        plan["python"] = "import os"
    elif bad == "price":
        page["unit_price_kzt"] = "0"
    elif bad == "heading":
        page["heading"] = "Экологичные премиальные подарки"
    else:
        plan["palette_order"] = [0, 0]
    with pytest.raises(ValueError):
        validate_plan(plan, value["items"], value["settings"])


async def test_ai_one_compact_schema_request_and_truthful_fallbacks():
    value = content()
    value["settings"]["auto_design"] = True
    original = copy.deepcopy(value)
    calls = []

    class Model:
        base_url = "http://127.0.0.1:11434"

        async def complete(self, messages, schema):
            calls.append(messages)
            assert issubclass(schema, DesignPlan)
            return deterministic_plan(value["items"], value["settings"], value["category_order"])

    result = await prepare_design(Model(), value, [], timeout=1)
    assert result["ai_used"] and not result["fallback_reason"] and len(calls) == 1
    request = json.loads(calls[0][1]["content"])
    assert not any("price" in key or "brand" in key for item in request["products"] for key in item)
    assert value == original

    class Invalid(Model):
        async def complete(self, messages, schema):
            return {"style": "editorial", "pages": []}

    invalid = await prepare_design(Invalid(), value, [], timeout=1)
    assert not invalid["ai_used"] and invalid["fallback_reason"] == "INVALID_DESIGN_PLAN"

    class Slow(Model):
        async def complete(self, messages, schema):
            await asyncio.sleep(1)

    timed = await prepare_design(Slow(), value, [], timeout=0.01)
    assert not timed["ai_used"] and timed["fallback_reason"] == "AI_TIMEOUT"
    offline = await prepare_design(None, value, [])
    assert not offline["ai_used"] and offline["fallback_reason"] == "LOCAL_MODEL_UNAVAILABLE"

    class External(Model):
        base_url = "https://external-service.example"

        async def complete(self, messages, schema):
            raise AssertionError("Client data must never leave the local endpoint")

    denied = await prepare_design(External(), value, [])
    assert not denied["ai_used"]
