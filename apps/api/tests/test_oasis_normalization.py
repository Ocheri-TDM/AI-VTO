import copy
import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.domain.errors import SupplierError
from app.domain.models import ColorGroup
from app.providers.oasis.normalization import (
    candidate_urls,
    normalize_product,
    numeric_stock,
    product_url,
    variant_stock,
)

URL = "https://www.oasiscatalog.com/item/1-000091961"


@pytest.fixture
def observed():
    return json.loads(
        (Path(__file__).parent / "fixtures/oasis/sense_navy_observed.json").read_text(encoding="utf-8")
    )


def test_exact_variant_price_stock_and_images(observed):
    product = normalize_product(observed["product"], observed["structured"], URL)
    assert product.id == "oasis:1-000091961"
    assert product.stock_quantity == 1891  # fixture evidence, never a runtime fallback
    assert product.original_price == Decimal(str(observed["structured"]["offers"]["price"]))
    assert product.original_currency == "RUB" and product.price_kzt is None
    assert product.primary_image.startswith("https://")
    assert "син" in product.colors[0].original_color.casefold()


def test_reserve_transit_and_other_variants_never_change_free_stock(observed):
    data = copy.deepcopy(observed["product"])
    warehouses = data["settings"]["productWarehouses"]
    warehouses["main"]["summary"]["stock"] = 999999
    warehouses["main"]["sizes"][0]["reserve"] = 888888
    warehouses["main"]["sizes"].append({"id": "other", "stock": 777777})
    warehouses["transit"] = {"summary": {"stock": 999999}}
    assert variant_stock(data) == 1891
    warehouses["main"]["sizes"] = [{"id": "other", "stock": 999999}]
    assert variant_stock(data) is None


@pytest.mark.parametrize("value", [None, True, "in stock", "300+", "10.5", "NaN"])
def test_unknown_stock_is_not_inventory_evidence(value):
    assert numeric_stock(value) is None


def test_color_candidates_reference_only_exact_matching_variants():
    cards = [
        {
            "colorsProduct": [
                {"url": "/item/red", "colorName": "красный"},
                {"url": "/item/navy", "colorName": "темно-синий"},
                {"url": "/item/blue", "colorName": "синий"},
            ]
        }
    ]
    assert candidate_urls(cards, [ColorGroup.NAVY]) == ["https://www.oasiscatalog.com/item/navy"]


def test_product_identity_and_host_are_validated(observed):
    with pytest.raises(SupplierError):
        normalize_product(
            observed["product"], observed["structured"], "https://www.oasiscatalog.com/item/other"
        )
    for url in (
        "https://evil.example/item/1",
        "http://www.oasiscatalog.com/item/1",
        "https://www.oasiscatalog.com:8080/item/1",
    ):
        with pytest.raises(SupplierError):
            product_url(url)
