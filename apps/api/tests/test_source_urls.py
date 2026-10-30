import pytest
from conftest import make_product

from app.api.research import product_details_view, product_view
from app.domain.source_urls import validated_product_url


def test_supplier_product_url_accepts_configured_host_and_removes_fragment():
    assert validated_product_url("vto", "https://www.vto.kz/id/123#stock") == (
        "https://www.vto.kz/id/123"
    )


def test_supplier_product_url_rejects_active_and_foreign_urls():
    assert validated_product_url("vto", "javascript:alert(1)") is None
    assert validated_product_url("vto", "https://evil.example/vto.kz/product") is None
    assert validated_product_url("vto", "http://localhost/product") is None
    assert validated_product_url("unknown", "https://vto.kz/id/123") is None


def test_canonical_details_preserve_all_real_supplier_urls():
    common = dict(
        name="Bottle Modelx", category="bottle", brand="Brand", material="steel",
        dimensions="20 cm", capacity="500 ml",
    )
    gifts = make_product(
        **common, id="gifts:1", supplier="vto", source_url="https://vto.kz/id/1"
    )
    happy = make_product(
        **common, id="happygifts:1", supplier="vto",
        source_url="https://vto.kz/catalog/1",
    )
    research = type("Research", (), {"products": [gifts, happy]})()
    result = product_details_view(research, gifts)
    assert result["offers"] == [
        {"supplier": "vto", "product_url": "https://vto.kz/id/1"},
        {"supplier": "vto", "product_url": "https://vto.kz/catalog/1"},
    ]


def test_missing_product_url_does_not_create_public_link():
    product = make_product(source_url="https://evil.example/product")
    research = type("Research", (), {"products": [product]})()
    result = product_details_view(research, product)
    assert "source_url" not in result and "offers" not in result


def test_list_card_exposes_category_relationship_separately_from_color_match():
    product = make_product()
    product.metadata["category_match"] = "STRONG_RELATED"
    product.metadata["color_match"] = "exact"
    result = product_view(product)
    assert result["category_match"] == "STRONG_RELATED"
    assert result["match"] == "exact"


@pytest.mark.parametrize(("supplier", "url"), [("vto", "https://vto.kz/product/melango/?variation_id=1823107")])
def test_list_card_preserves_validated_source_for_every_supplier(supplier, url):
    assert product_view(make_product(supplier=supplier, source_url=url))["source_url"] == url
