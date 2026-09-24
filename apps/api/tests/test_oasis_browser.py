import html
import json
from pathlib import Path

import pytest

from app.browser import BrowserEngine
from app.domain.models import SearchIntent
from app.providers.oasis import OasisProvider

pytestmark = pytest.mark.browser


async def test_browser_reads_observed_fixture(settings):
    fixture = json.loads(
        (Path(__file__).parent / "fixtures/oasis/sense_navy_observed.json").read_text(encoding="utf-8")
    )
    content = (
        '<html><body><div data-product="'
        + html.escape(json.dumps(fixture["product"]))
        + '"></div><script type="application/ld+json">'
        + json.dumps(fixture["structured"])
        + "</script></body></html>"
    )
    browser = BrowserEngine(settings)
    try:
        async with browser.session("oasis", {"www.oasiscatalog.com"}) as session:
            await session.page.route(
                "**/*", lambda route: route.fulfill(status=200, content_type="text/html", body=content)
            )
            product = await OasisProvider(browser, settings)._read_product(
                session, "https://www.oasiscatalog.com/item/1-000091961"
            )
            assert product.stock_quantity == 1891
            assert {t.action.value for t in session.traces} >= {
                "OPEN_PRODUCT",
                "READ_PRICE",
                "READ_STOCK",
                "READ_IMAGES",
            }
    finally:
        await browser.close()


@pytest.mark.live
async def test_live_oasis_search(settings):
    settings.max_pages_per_query = 1
    settings.max_products_per_query = 2
    browser = BrowserEngine(settings)
    try:
        result = await OasisProvider(browser, settings).search(
            "термокружка", SearchIntent(raw_query="термокружка")
        )
        assert result.products
        assert all(p.original_currency == "RUB" for p in result.products)
    finally:
        await browser.close()
