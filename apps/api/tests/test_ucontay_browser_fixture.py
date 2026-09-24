import html
import json
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from app.browser import BrowserEngine
from app.domain.models import SearchIntent
from app.providers.ucontay import UcontayProvider

pytestmark = pytest.mark.browser


async def test_document_survives_hydration_and_page_two_failure(settings):
    observed = json.loads(
        (Path(__file__).parent / "fixtures/ucontay/radmir_observed.json").read_text(encoding="utf-8")
    )
    path = urlsplit(observed["url"]).path
    body = (
        '<html><body><meta name="shop-config" data-config="{&quot;currency_code&quot;:&quot;KZT&quot;}">'
        f'<form data-product-json="{html.escape(json.dumps(observed), quote=True)}"></form>'
        "<script>document.querySelector('[data-product-json]').removeAttribute('data-product-json')</script>"
        "</body></html>"
    )
    visited = []

    async def serve(route):
        url = urlsplit(route.request.url)
        visited.append(url.path)
        if url.path == "/collection/all" and "page=2" in url.query:
            await route.fulfill(status=503, content_type="text/html", body="<body>Temporary outage</body>")
        elif url.path == "/collection/all":
            await route.fulfill(
                status=200,
                content_type="text/html",
                body=(
                    '<body><div class="catalog-list"><div class="product-preview">'
                    f'<div class="product-preview__title"><a href="{path}">Термокружка RADMIR</a></div>'
                    '</div></div><a class="pagination-next" href="/collection/all?page=2">Далее</a></body>'
                ),
            )
        else:
            await route.fulfill(status=200, content_type="text/html", body=body)

    class FixtureBrowser(BrowserEngine):
        @asynccontextmanager
        async def session(self, supplier, allowed_hosts):
            async with super().session(supplier, allowed_hosts) as session:
                await session.page.route("**/*", serve)
                yield session

    browser = FixtureBrowser(settings)
    try:
        provider = UcontayProvider(browser, settings)
        result = await provider.search("термокружка", SearchIntent(raw_query="термокружка"))
        assert result.products and result.pages_scanned == 3
        assert result.warnings and "SUPPLIER_HTTP_ERROR" in result.warnings[0]
        assert any(p.id == "ucontay:710296740" and p.stock_quantity == 0 for p in result.products)
        assert visited.count("/collection/all") == 2
        assert path in visited
    finally:
        await browser.close()
