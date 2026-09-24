import json
from urllib.parse import urljoin, urlsplit

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from app.browser.engine import BrowserEngine, BrowserSession
from app.config import Settings
from app.domain.errors import SupplierError
from app.domain.models import ActionType, Product, ProviderResult, SearchIntent
from app.providers.base import ProviderCapabilities, SupplierProvider
from app.providers.oasis.normalization import (
    ALLOWED_HOSTS,
    BASE_URL,
    candidate_urls,
    normalize_product,
    product_url,
)


class OasisProvider(SupplierProvider):
    supplier = "oasis"
    capabilities = ProviderCapabilities(
        discovery='published /categories links', identity='data-product exact variant id',
        pagination='category page-N link', variants='data-product sizes/variants',
        price='product structured data', currency='RUB', stock='free quantity excluding reserve/transit',
        images='product gallery', category_path='observed category route', refresh='exact item URL')

    async def research_listing(self, branch):
        from app.domain.research import ListingPage
        async with self.browser.session(self.supplier, ALLOWED_HOSTS) as session:
            if branch.cursor.listing_url:
                await session.goto(branch.cursor.listing_url, ActionType.PAGINATE)
            else:
                await self._search_page(session, branch.query)
            cards = await session.page.locator('[data-catalog-product]').evaluate_all(
                'xs=>xs.map(x=>JSON.parse(x.getAttribute("data-catalog-product")))')
            if not cards:
                text = (await session.page.locator('body').inner_text()).casefold()
                if not any(x in text for x in ('не найден', 'ничего не', '0 товаров')):
                    raise SupplierError('SUPPLIER_PARSING_ERROR', 'Oasis listing not recognized')
            links = await session.page.locator('a.pagination__btn[href]').evaluate_all(
                'xs=>xs.map(x=>x.href)')
            next_url = next((u for u in links if urlsplit(u).path.endswith(
                f'/page-{branch.cursor.page_number + 2}')), None)
            return ListingPage(urls=list(dict.fromkeys(candidate_urls(cards, []))),
                               next_url=next_url, exhausted=next_url is None)

    def __init__(self, browser: BrowserEngine, settings: Settings):
        self.browser = browser
        self.settings = settings

    async def _read_product(self, session: BrowserSession, url: str) -> Product:
        await session.goto(product_url(url), ActionType.OPEN_PRODUCT)
        raw = await session.page.locator("[data-product]").first.get_attribute("data-product", timeout=5000)
        try:
            data = json.loads(raw or "{}")
            structured = await session.page.locator('script[type="application/ld+json"]').evaluate_all(
                """nodes => nodes.flatMap(node => {
                    try { const value=JSON.parse(node.textContent); return value['@graph'] || [value]; }
                    catch { return []; }
                }).find(value => value['@type'] === 'Product') || {}"""
            )
            product = normalize_product(data, structured, session.page.url)
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise SupplierError("SUPPLIER_PARSE_ERROR", "Изменился формат карточки Oasis.") from exc
        session.record(
            ActionType.READ_PRICE, url, currency=product.original_currency, method="structured_data"
        )
        session.record(ActionType.READ_STOCK, url, quantity=product.stock_quantity, variant_id=data["id"])
        session.record(ActionType.READ_COLOR, url, count=len(product.colors))
        session.record(ActionType.READ_IMAGES, url, count=len(product.images))
        return product

    async def get_product(self, url: str) -> Product:
        canonical = product_url(url)
        try:
            async with self.browser.session(self.supplier, ALLOWED_HOSTS) as session:
                return await self._read_product(session, canonical)
        except PlaywrightTimeoutError as exc:
            raise SupplierError(
                "SUPPLIER_TIMEOUT", "Не удалось дождаться данных Oasis.", retryable=True
            ) from exc
        except PlaywrightError as exc:
            raise SupplierError("SUPPLIER_BROWSER_ERROR", "Ошибка браузера Oasis.", retryable=True) from exc

    async def _search_page(self, session: BrowserSession, query: str) -> None:
        await session.goto(BASE_URL + "/")
        # The observed public UI performs autocomplete and may navigate directly to
        # a category. It must be allowed to choose its actual search URL.
        box = session.page.locator('form[action="/srch"] input[name="q"]').first
        await box.fill(query)
        session.record(ActionType.SEARCH, session.page.url, method="search_form")
        async with session.page.expect_navigation(wait_until="domcontentloaded") as navigation:
            await box.press("Enter")
        response = await navigation.value
        session.pages_scanned += 1
        session.validate_url(session.page.url)
        await session.check_challenge()
        if response and response.status >= 400:
            raise SupplierError(
                "SUPPLIER_HTTP_ERROR",
                f"Oasis вернул HTTP {response.status}.",
                retryable=response.status in (429, 502, 503, 504),
            )

    async def search(self, query: str, filters: SearchIntent) -> ProviderResult:
        result = ProviderResult()
        try:
            async with self.browser.session(self.supplier, ALLOWED_HOSTS) as session:
                await self._search_page(session, query)
                candidates: list[str] = []
                seen_pages: set[str] = set()
                for page_number in range(1, self.settings.max_pages_per_query + 1):
                    seen_pages.add(session.page.url)
                    cards = await session.page.locator("[data-catalog-product]").evaluate_all(
                        """nodes => nodes.flatMap(node => {
                            try { return [JSON.parse(node.getAttribute('data-catalog-product'))]; }
                            catch { return []; }
                        })"""
                    )
                    if not cards:
                        text = (await session.page.locator("body").inner_text()).casefold()
                        if not any(marker in text for marker in ("не найден", "ничего не", "0 товаров")):
                            raise SupplierError(
                                "SUPPLIER_PARSE_ERROR", "Не найдены данные результатов Oasis."
                            )
                        break
                    candidates.extend(candidate_urls(cards, filters.colors))
                    candidates = list(dict.fromkeys(candidates))
                    next_links = await session.page.locator("a.pagination__btn[href]").evaluate_all(
                        "nodes => nodes.map(node => node.href)"
                    )
                    next_url = next(
                        (
                            url
                            for url in next_links
                            if f"/page-{page_number + 1}"
                            == urlsplit(url).path[-len(f"/page-{page_number + 1}") :]
                            and url not in seen_pages
                        ),
                        None,
                    )
                    if len(candidates) >= self.settings.max_products_per_query:
                        result.warnings.append("OASIS_SCAN_LIMIT_REACHED")
                        break
                    if not next_url:
                        break
                    if page_number == self.settings.max_pages_per_query:
                        result.warnings.append("OASIS_PAGE_LIMIT_REACHED")
                        break
                    try:
                        await session.goto(urljoin(BASE_URL, next_url), ActionType.PAGINATE)
                    except SupplierError as exc:
                        if exc.code == "SUPPLIER_CAPTCHA_REQUIRED":
                            raise
                        result.warnings.append(f"OASIS_PAGINATION_{exc.code}")
                        break
                last_error: SupplierError | None = None
                for url in candidates[: self.settings.max_products_per_query]:
                    try:
                        result.products.append(await self._read_product(session, url))
                    except SupplierError as exc:
                        if exc.code == "SUPPLIER_CAPTCHA_REQUIRED":
                            raise
                        last_error = exc
                        session.record(ActionType.ERROR, url, code=exc.code)
                        result.warnings.append(f"OASIS_PRODUCT_{exc.code}")
                    except (PlaywrightError, ValueError, TypeError):
                        last_error = SupplierError(
                            "SUPPLIER_PARSE_ERROR", "Не удалось прочитать карточку Oasis."
                        )
                        session.record(ActionType.ERROR, url, code=last_error.code)
                        result.warnings.append(f"OASIS_PRODUCT_{last_error.code}")
                if candidates and not result.products and last_error:
                    raise last_error
                result.traces = session.traces
                result.pages_scanned = session.pages_scanned
                result.warnings = list(dict.fromkeys(result.warnings))
                return result
        except PlaywrightTimeoutError as exc:
            raise SupplierError("SUPPLIER_TIMEOUT", "Превышено время поиска Oasis.", retryable=True) from exc
        except PlaywrightError as exc:
            raise SupplierError("SUPPLIER_BROWSER_ERROR", "Ошибка браузера Oasis.", retryable=True) from exc

    async def health_check(self) -> bool:
        try:
            async with self.browser.session(self.supplier, ALLOWED_HOSTS) as session:
                await session.goto(BASE_URL + "/")
                return await session.page.locator('form[action="/srch"] input[name="q"]').count() > 0
        except (SupplierError, PlaywrightError):
            return False
