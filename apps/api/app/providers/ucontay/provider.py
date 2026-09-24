"""Ucontay adapter. All supplier document reads are performed by Chromium."""

from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

from app.browser.engine import BrowserEngine, BrowserSession
from app.config import Settings
from app.domain.errors import SupplierError
from app.domain.models import ActionType, Product, ProviderResult, SearchIntent
from app.domain.services import ColorNormalizer
from app.providers.base import ProviderCapabilities, SupplierProvider
from app.providers.ucontay.extraction import BASE_URL, DocumentData, normalize_product


class UcontayProvider(SupplierProvider):
    supplier = "ucontay"
    allowed_hosts = {"ucontay.kz", "www.ucontay.kz"}
    capabilities = ProviderCapabilities(
        discovery='published /collection links', identity='InSales variant id',
        pagination='collection next URL', variants='family document variant JSON',
        price='variant JSON price', currency='KZT', stock='variant available quantity',
        images='variant images', category_path='observed collection route', refresh='family document batch')

    def __init__(self, browser: BrowserEngine, settings: Settings):
        self.browser = browser
        self.settings = settings

    def observation_group_key(self, url: str) -> str:
        # A product document includes every variant, regardless of selected variant query.
        parsed = urlsplit(url)
        return f'{parsed.scheme}://{parsed.netloc}{parsed.path}'

    async def research_listing(self, branch):
        from app.domain.research import ListingPage
        # Research does not inherit Stage 1's warehouse characteristic filters.
        url = branch.cursor.listing_url or f'{BASE_URL}/collection/all?{urlencode({"q": branch.query})}'
        async with self.browser.session(self.supplier, self.allowed_hosts) as session:
            await session.goto(url, ActionType.SEARCH)
            urls = await session.page.locator('.catalog-list .product-preview__title a').evaluate_all(
                'xs=>xs.map(x=>x.href)')
            if not urls:
                text = (await session.page.locator('body').inner_text()).casefold()
                if not any(x in text for x in ('не найден', 'нет товаров', 'ничего не')):
                    raise SupplierError('SUPPLIER_PARSING_ERROR', 'Ucontay listing not recognized')
            link = session.page.locator('a.pagination-next[href]').first
            next_url = urljoin(BASE_URL, await link.get_attribute('href')) if await link.count() else None
            return ListingPage(urls=list(dict.fromkeys(urls)), next_url=next_url,
                               exhausted=next_url is None)

    async def research_products(self, url):
        async with self.browser.session(self.supplier, self.allowed_hosts) as session:
            return await self._products(session, url)

    async def _document(self, session: BrowserSession, url: str, action: ActionType) -> DocumentData:
        # The storefront consumes/removes data-product-json during hydration.
        # Capture the response from a normal main-frame browser navigation first.
        async with session.page.expect_response(
            lambda response: (
                response.request.resource_type == "document"
                and response.request.frame == session.page.main_frame
                and response.status == 200
            )
        ) as pending:
            await session.goto(url, action)
        response = await pending.value
        return DocumentData(await response.text())

    async def _products(
        self,
        session: BrowserSession,
        url: str,
        category: str | None = None,
    ) -> list[Product]:
        document = await self._document(session, url, ActionType.OPEN_PRODUCT)
        path = urlsplit(url).path
        data = next(
            (item for item in document.products if urlsplit(str(item.get("url", ""))).path == path),
            None,
        )
        if data is None:
            raise SupplierError(
                "SUPPLIER_PARSING_ERROR", "Ucontay: структурированные данные карточки не найдены."
            )
        description = None
        for item in document.structured:
            if item.get("@type") == "Product":
                description = item.get("description")
            if category is None and item.get("@type") == "BreadcrumbList":
                breadcrumbs = item.get("itemListElement", [])
                if len(breadcrumbs) > 2:
                    category = breadcrumbs[-2].get("name")
        currency = document.shop.get("currency_code") or document.shop.get("currency_iso_code")
        products = normalize_product(data, currency=currency, category=category, description=description)
        if not products:
            raise SupplierError("SUPPLIER_PARSING_ERROR", "Ucontay: варианты карточки не найдены.")
        session.record(ActionType.READ_PRICE, url, count=len(products), currency=currency)
        session.record(ActionType.READ_STOCK, url, count=len(products), field="variants.quantity")
        session.record(ActionType.READ_COLOR, url, count=len(products))
        session.record(ActionType.READ_IMAGES, url, count=sum(len(item.images) for item in products))
        return products

    async def search(self, query: str, filters: SearchIntent) -> ProviderResult:
        params = [
            ("q", query),
            ("characteristics[]", "113475444"),
            ("characteristics[]", "108951931"),
            ("characteristics[]", "273463770"),
        ]
        url = f"{BASE_URL}/collection/all?{urlencode(params)}"
        products: list[Product] = []
        warnings: list[str] = []
        candidates: dict[str, str] = {}
        category = filters.categories[0] if filters.categories else None
        async with self.browser.session(self.supplier, self.allowed_hosts) as session:
            for number in range(self.settings.max_pages_per_query):
                try:
                    await session.goto(url, ActionType.SEARCH if number == 0 else ActionType.PAGINATE)
                except SupplierError as exc:
                    if not candidates or exc.code == "SUPPLIER_CAPTCHA_REQUIRED":
                        raise
                    session.record(ActionType.ERROR, url, code=exc.code)
                    warnings.append(
                        f"Ucontay: продолжение поиска недоступно ({exc.code}); проверена часть страниц."
                    )
                    break
                # Selectors were recorded from the actual collection document, 2026-09-08.
                cards = await session.page.locator(".catalog-list .product-preview").evaluate_all(
                    """cards => cards.map(card => ({
                        url: card.querySelector('.product-preview__title a')?.href,
                        text: card.textContent
                    })).filter(card => card.url)"""
                )
                if not cards:
                    text = (await session.page.locator("body").inner_text()).casefold()
                    if not any(
                        marker in text for marker in ("не найден", "нет товаров", "ничего не найдено")
                    ):
                        raise SupplierError(
                            "SUPPLIER_PARSING_ERROR", "Ucontay: структура результатов поиска изменилась."
                        )
                    break
                for card in cards:
                    candidate = card["url"]
                    if urlsplit(candidate).hostname in self.allowed_hosts and urlsplit(
                        candidate
                    ).path.startswith("/product/"):
                        candidates.setdefault(candidate, card["text"])
                next_link = session.page.locator("a.pagination-next[href]").first
                next_href = await next_link.get_attribute("href") if await next_link.count() else None
                if not next_href:
                    break
                if number + 1 == self.settings.max_pages_per_query:
                    warnings.append("Ucontay: достигнут лимит страниц; выборка ограничена настройками.")
                    break
                url = urljoin(BASE_URL, next_href)

            # Prefer pages advertising a requested color, but never exclude a candidate here.
            normalizer = ColorNormalizer()
            ordered = sorted(
                candidates,
                key=lambda candidate: (
                    not normalizer.matches([normalizer.normalize(candidates[candidate])], filters.colors)
                ),
            )
            limit = self.settings.max_products_per_query
            if len(ordered) > limit:
                warnings.append(
                    "Ucontay: достигнут лимит карточек; доступны дополнительные результаты на сайте."
                )
            for candidate in ordered[:limit]:
                try:
                    products.extend(await self._products(session, candidate, category))
                except SupplierError as exc:
                    if exc.code == "SUPPLIER_CAPTCHA_REQUIRED":
                        raise
                    session.record(ActionType.ERROR, candidate, code=exc.code)
                    warnings.append(f"Ucontay: карточка пропущена ({exc.code}).")
            if ordered and not products:
                raise SupplierError(
                    "SUPPLIER_PARSING_ERROR", "Ucontay: не удалось прочитать найденные карточки."
                )
            return ProviderResult(
                products=products,
                traces=session.traces,
                pages_scanned=session.pages_scanned,
                warnings=list(dict.fromkeys(warnings)),
            )

    async def get_product(self, url: str) -> Product:
        parsed = urlsplit(url)
        if not parsed.path.startswith("/product/"):
            raise SupplierError("SUPPLIER_INVALID_URL", "Ucontay: требуется ссылка на карточку товара.")
        async with self.browser.session(self.supplier, self.allowed_hosts) as session:
            products = await self._products(session, url)
            requested = parse_qs(parsed.query).get("variant_id", [None])[0]
            if requested is not None:
                for product in products:
                    if product.metadata["variant_id"] == requested:
                        return product
                raise SupplierError(
                    "SUPPLIER_PRODUCT_NOT_FOUND", "Ucontay: выбранный вариант больше не найден."
                )
            return products[0]

    async def health_check(self) -> bool:
        try:
            async with self.browser.session(self.supplier, self.allowed_hosts) as session:
                await session.goto(BASE_URL)
                return await session.page.locator("form.header__search-form input[name='q']").count() > 0
        except SupplierError:
            return False
