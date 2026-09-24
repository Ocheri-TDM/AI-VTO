import copy
import html
import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.domain.models import ColorGroup
from app.domain.services import AvailabilityService, ColorNormalizer
from app.providers.ucontay.extraction import DocumentData, normalize_product, quantity

FIXTURE = Path(__file__).parent / "fixtures/ucontay/radmir_observed.json"


@pytest.fixture
def observed():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_observed_variants_do_not_borrow_stock_or_images(observed):
    products = normalize_product(observed, currency="KZT", category="thermomug")
    by_id = {p.id: p for p in products}
    navy = by_id["ucontay:710296740"]
    pink = by_id["ucontay:710296567"]
    assert navy.stock_quantity == 0
    assert not AvailabilityService().accepts(navy, 300)
    assert pink.stock_quantity == 1713  # 1738 warehouse units minus 25 reserved.
    assert pink.stock_quantity != pink.metadata["warehouse_fields"]["13312"]
    assert navy.primary_image != pink.primary_image
    assert navy.source_url.endswith("?variant_id=710296740")
    assert navy.original_price == Decimal("5153.0")
    assert navy.price_kzt is None
    assert ColorNormalizer().normalize(navy.colors[0].original_color).normalized_color == ColorGroup.DARK_BLUE


@pytest.mark.parametrize("value", [None, True, "in stock", "NaN", "Infinity", -1, "12.5", ""])
def test_unknown_or_invalid_quantity_never_becomes_available(value, observed):
    observed["variants"][0]["quantity"] = value
    product = normalize_product(observed, currency="KZT")[0]
    assert product.stock_quantity is None
    assert not product.available
    assert not AvailabilityService().accepts(product, 1)


@pytest.mark.parametrize(("value", "expected"), [(0, 0), (300, 300), ("300.0", 300)])
def test_integer_supplier_quantities(value, expected):
    assert quantity(value) == expected


def test_structured_document_decodes_html_entities_and_ignores_bad_json(observed):
    payload = html.escape(json.dumps(observed, ensure_ascii=False), quote=True)
    document = DocumentData(
        '<meta name="shop-config" data-config="{&quot;currency_code&quot;:&quot;KZT&quot;}">'
        '<div data-product-json="broken"></div>'
        f'<form data-product-json="{payload}"></form>'
        '<script type="application/ld+json">{"@type":"Product","description":"test"}</script>'
    )
    assert document.products == [observed]
    assert document.shop["currency_code"] == "KZT"
    assert document.structured[0]["description"] == "test"


def test_sizes_are_not_colors_and_transit_is_never_added(observed):
    data = copy.deepcopy(observed)
    data["option_names"].append({"id": 999, "title": "Размер"})
    data["variants"][0]["option_values"].append({"option_name_id": 999, "title": "XL"})
    data["variants"][0]["variant_field_values"].append({"variant_field_id": 13314, "value": "8000"})
    product = normalize_product(data, currency="KZT")[0]
    assert len(product.colors) == 1
    assert product.stock_quantity == 1096


def test_missing_currency_or_bad_price_is_not_invented(observed):
    observed["variants"][0]["price"] = "по запросу"
    product = normalize_product(observed, currency=None)[0]
    assert product.original_currency is None
    assert product.original_price is None


def test_missing_variant_image_does_not_borrow_another_color(observed):
    observed["variants"][0]["image_id"] = None
    observed["variants"][0]["image_ids"] = []
    product = normalize_product(observed, currency="KZT")[0]
    assert product.primary_image is None
    assert product.images == []
    assert product.metadata["family_images"]
