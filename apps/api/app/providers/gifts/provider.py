import re
from urllib.parse import quote, urljoin, urlsplit

from app.browser.engine import BrowserEngine, BrowserSession
from app.config import Settings
from app.domain.errors import SupplierError
from app.domain.models import ActionType, Product, ProviderResult, SearchIntent
from app.domain.services import ColorNormalizer
from app.providers.base import ProviderCapabilities, SupplierProvider

from .normalization import BASE_URL, normalize_product, search_items


class GiftsProvider(SupplierProvider):
    supplier = "gifts"
    allowed_hosts = {"gifts.ru", "www.gifts.ru"}
    capabilities = ProviderCapabilities(
        discovery='published /catalog links', identity='ArticlesData article/variant id',
        pagination='document next URL', variants='ArticlesData exact variants',
        price='product page price', currency='RUB', stock='free warehouse quantity excluding reserve',
        images='variant gallery', category_path='observed catalog route', refresh='exact product URL')

    def __init__(self, browser: BrowserEngine, settings: Settings):
        self.browser, self.settings = browser, settings

    async def research_listing(self, branch):
        from app.domain.research import ListingPage
        url = branch.cursor.listing_url or BASE_URL + '/search/text/' + quote(branch.query, safe='')
        async with self.browser.session(self.supplier, self.allowed_hosts) as session:
            async with session.page.expect_response(lambda r: r.request.resource_type == 'document'
                                                   and r.request.frame == session.page.main_frame
                                                   and r.status == 200) as pending:
                await session.goto(url, ActionType.SEARCH)
            items = search_items(await (await pending.value).text())
            if not items:
                text = (await session.page.locator('body').inner_text()).casefold()
                if not any(x in text for x in ('не найден', 'ничего не', 'нет результатов')):
                    raise SupplierError('SUPPLIER_PARSING_ERROR', 'Gifts listing not recognized')
            urls = list(dict.fromkeys(urljoin(BASE_URL, x['url']) for x in items
                                     if re.fullmatch(r'/id/\d+', urlsplit(x['url']).path)))
            link = session.page.locator('a.ctlg-pages-arrow.next[href]').first
            next_url = urljoin(BASE_URL, await link.get_attribute('href')) if await link.count() else None
            return ListingPage(urls=urls, next_url=next_url, exhausted=next_url is None)

    async def _product(self, session: BrowserSession, url: str) -> Product:
        await session.goto(url, ActionType.OPEN_PRODUCT)
        root = session.page.locator("#j_product")
        name = root.locator('h1[itemprop="name"]')
        if not await name.count():
            raise SupplierError("SUPPLIER_PARSING_ERROR", "Gifts: карточка не распознана.")
        data = {"name": await name.inner_text()}
        description = root.locator('[itemprop="description"]').first
        data['description'] = await description.inner_text() if await description.count() else None
        data['attributes'] = await root.locator('.itm-opts-label').evaluate_all('''xs=>Object.fromEntries(xs.map(x=>[
            x.textContent.trim(),x.parentElement.textContent.replace(x.textContent,'').trim()]))''')
        for key, prop in (("price", "price"), ("currency", "priceCurrency"), ("sku", "sku")):
            locator = root.locator(f'meta[itemprop="{prop}"]').first
            data[key] = await locator.get_attribute("content") if await locator.count() else None
        data["quantities"] = await root.locator("tr.j_salearticle .itm-ord-qty input.j_qty").evaluate_all(
            "xs => xs.map(x=>x.getAttribute('placeholder'))"
        )
        data["images"] = (
            await root.locator("g-gallery")
            .first.locator("img[data-hd]")
            .evaluate_all(
                "xs => xs.map(x=>x.getAttribute('data-hd') || x.getAttribute('data-src') || x.src).filter(Boolean)"
            )
        )
        for action in (
            ActionType.READ_PRICE,
            ActionType.READ_STOCK,
            ActionType.READ_COLOR,
            ActionType.READ_IMAGES,
        ):
            session.record(action, url)
        return normalize_product(data, url)

    async def search(self, query: str, filters: SearchIntent) -> ProviderResult:
        url = BASE_URL + "/search/text/" + quote(query, safe="")
        candidates, products, warnings = {}, [], []
        async with self.browser.session(self.supplier, self.allowed_hosts) as session:
            for number in range(self.settings.max_pages_per_query):
                async with session.page.expect_response(
                    lambda r: (
                        r.request.resource_type == "document"
                        and r.request.frame == session.page.main_frame
                        and r.status == 200
                    )
                ) as pending:
                    await session.goto(url, ActionType.SEARCH if number == 0 else ActionType.PAGINATE)
                response = await pending.value
                items = search_items(await response.text())
                if not items:
                    text = (await session.page.locator("body").inner_text()).casefold()
                    if not any(x in text for x in ("не найден", "ничего не", "нет результатов")):
                        raise SupplierError("SUPPLIER_PARSING_ERROR", "Gifts: структура поиска изменилась.")
                    break
                for item in items:
                    candidate = urljoin(BASE_URL, item["url"])
                    if urlsplit(candidate).hostname in self.allowed_hosts and re.fullmatch(
                        r"/id/\d+", urlsplit(candidate).path
                    ):
                        candidates[candidate] = item["name"]
                link = session.page.locator("a.ctlg-pages-arrow.next[href]").first
                if not await link.count():
                    break
                if number + 1 == self.settings.max_pages_per_query:
                    warnings.append("Gifts: достигнут лимит страниц; проверена часть результатов.")
                    break
                url = urljoin(BASE_URL, await link.get_attribute("href"))
            normalizer = ColorNormalizer()
            ordered = sorted(
                candidates,
                key=lambda u: not normalizer.matches([normalizer.normalize(candidates[u])], filters.colors),
            )
            if len(ordered) > self.settings.max_products_per_query:
                warnings.append("Gifts: достигнут лимит карточек; выборка ограничена.")
            for candidate in ordered[: self.settings.max_products_per_query]:
                try:
                    products.append(await self._product(session, candidate))
                except SupplierError as exc:
                    if exc.code == "SUPPLIER_CAPTCHA_REQUIRED":
                        raise
                    session.record(ActionType.ERROR, candidate, code=exc.code)
                    warnings.append(f"Gifts: карточка пропущена ({exc.code}).")
            if ordered and not products:
                raise SupplierError("SUPPLIER_PARSING_ERROR", "Gifts: найденные карточки недоступны.")
            return ProviderResult(
                products=products,
                traces=session.traces,
                pages_scanned=session.pages_scanned,
                warnings=list(dict.fromkeys(warnings)),
            )

    async def get_product(self, url: str) -> Product:
        if not re.fullmatch(r"/id/\d+", urlsplit(url).path):
            raise SupplierError("SUPPLIER_INVALID_URL", "Gifts: требуется URL точного варианта.")
        async with self.browser.session(self.supplier, self.allowed_hosts) as session:
            return await self._product(session, url)

    async def health_check(self) -> bool:
        try:
            async with self.browser.session(self.supplier, self.allowed_hosts) as session:
                await session.goto(BASE_URL)
                return await session.page.locator("#j_search_input").count() > 0
        except SupplierError:
            return False
