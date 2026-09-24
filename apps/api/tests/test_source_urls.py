from conftest import make_product

from app.api.research import product_details_view
from app.domain.source_urls import validated_product_url


def test_supplier_product_url_accepts_configured_host_and_removes_fragment():
    assert validated_product_url("gifts", "https://www.gifts.ru/id/123#stock") == (
        "https://www.gifts.ru/id/123"
    )


def test_supplier_product_url_rejects_active_and_foreign_urls():
    assert validated_product_url("gifts", "javascript:alert(1)") is None
    assert validated_product_url("gifts", "https://evil.example/gifts.ru/product") is None
    assert validated_product_url("gifts", "http://localhost/product") is None
    assert validated_product_url("unknown", "https://gifts.ru/id/123") is None


def test_canonical_details_preserve_all_real_supplier_urls():
    common = dict(
        name="Bottle Modelx", category="bottle", brand="Brand", material="steel",
        dimensions="20 cm", capacity="500 ml",
    )
    gifts = make_product(
        **common, id="gifts:1", supplier="gifts", source_url="https://gifts.ru/id/1"
    )
    happy = make_product(
        **common, id="happygifts:1", supplier="happygifts",
        source_url="https://happygifts.ru/catalog/1",
    )
    research = type("Research", (), {"products": [gifts, happy]})()
    result = product_details_view(research, gifts)
    assert result["offers"] == [
        {"supplier": "gifts", "product_url": "https://gifts.ru/id/1"},
        {"supplier": "happygifts", "product_url": "https://happygifts.ru/catalog/1"},
    ]


def test_missing_product_url_does_not_create_public_link():
    product = make_product(source_url="https://evil.example/product")
    research = type("Research", (), {"products": [product]})()
    result = product_details_view(research, product)
    assert "source_url" not in result and "offers" not in result
