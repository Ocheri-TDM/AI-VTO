import pytest

from app.providers.gifts.normalization import normalize_product, search_items


def observation(**changes):
    value = {
        "name": "Рюкзак New Element, темно-синий",
        "price": "187",
        "currency": "RUB",
        "quantities": ["840"],
        "sku": "13921.70",
        "images": ["//files.gifts.ru/reviewer/webp/26/13921.70_23_2000x2000.webp"],
    }
    value.update(changes)
    return value


def test_exact_variant_free_stock_and_original_currency():
    p = normalize_product(observation(), "https://gifts.ru/id/206709")
    assert p.stock_quantity == 840 and p.original_currency == "RUB"
    assert p.price_kzt is None
    assert p.colors[0].normalized_color == "DARK_BLUE"
    assert p.primary_image.startswith("https://files.gifts.ru/")


@pytest.mark.parametrize("quantities", [[], ["в наличии"], ["300", "200"], [None]])
def test_unknown_or_multiple_variant_stock_not_summed(quantities):
    assert (
        normalize_product(observation(quantities=quantities), "https://gifts.ru/id/206709").stock_quantity
        is None
    )


def test_structured_search_literal_not_javascript_execution():
    assert search_items('<script>a.ArticlesData={"1":[{"items":[{"id":1}]}]};evil()</script>') == [{"id": 1}]
