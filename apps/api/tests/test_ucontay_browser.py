"""Opt-in live site checks: pytest -m live."""

import pytest

from app.browser.engine import BrowserEngine
from app.config import Settings
from app.domain.models import SearchIntent
from app.providers.ucontay import UcontayProvider

pytestmark = [
    pytest.mark.browser,
    pytest.mark.live,
]


@pytest.mark.asyncio
async def test_live_ucontay_exact_variant():
    settings = Settings()
    browser = BrowserEngine(settings)
    try:
        provider = UcontayProvider(browser, settings)
        product = await provider.get_product(
            "https://ucontay.kz/product/termokruzhka-radmir-soft-touch?variant_id=710296740"
        )
        assert product.id == "ucontay:710296740"
        assert product.original_currency == "KZT"
        assert product.colors
        assert product.images
        # Live inventory changes: verify the actual numeric contract, never a fixed quantity.
        assert product.stock_quantity is None or product.stock_quantity >= 0
    finally:
        await browser.close()


@pytest.mark.asyncio
async def test_live_ucontay_search_opens_product_documents():
    settings = Settings(max_pages_per_query=1, max_products_per_query=2)
    browser = BrowserEngine(settings)
    try:
        provider = UcontayProvider(browser, settings)
        result = await provider.search("термокружка", SearchIntent(raw_query="термокружка"))
        assert result.products
        assert result.pages_scanned >= 2
        assert all(product.supplier == "ucontay" for product in result.products)
        assert any(trace.action == "READ_STOCK" for trace in result.traces)
    finally:
        await browser.close()
