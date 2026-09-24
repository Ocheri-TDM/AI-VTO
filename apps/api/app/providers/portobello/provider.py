import json
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.domain.errors import SupplierError
from app.domain.research import ListingPage
from app.providers.base import ProviderCapabilities
from app.providers.research_support import ResearchEnabledProvider, conservative_stock, observed_product


class PortobelloProvider(ResearchEnabledProvider):
    supplier = 'portobello'
    base_url = 'https://portobello.ru'
    allowed_hosts = {'portobello.ru', 'www.portobello.ru'}
    capabilities = ProviderCapabilities(
        discovery='published /catalog links', identity='Angular nomenclature code',
        pagination='zero-based Angular paginator', variants='active paginator family nomenclatures',
        price='Apollo nomenclature price', currency='RUB', stock='maximum one MAIN storage free quantity',
        images='offer thumbnails', category_path='observed catalog route', refresh='exact offer URL + StocksDataLoader')

    def listing_offer_urls(self, state, paginator):
        # Apollo also contains recommendation/header data. Only references from
        # the active paginator establish branch membership and coverage counts.
        urls = []
        for family_ref in paginator.get('items', []):
            family = state.get(family_ref.get('__ref'), family_ref)
            for offer_ref in family.get('nomenclatures', []):
                offer = state.get(offer_ref.get('__ref'), offer_ref)
                if offer.get('code'):
                    urls.append(self.base_url + '/catalog/offer/' + offer['code'])
        return list(dict.fromkeys(urls))

    async def _state(self, session):
        raw = await session.page.locator('script#ng-state').text_content()
        try:
            return json.loads(raw)['apollo.state']
        except (ValueError, KeyError, TypeError) as exc:
            raise SupplierError('SUPPLIER_PARSING_ERROR', 'Portobello structured data missing') from exc

    async def research_listing(self, branch):
        url = branch.cursor.listing_url or self.base_url + '/catalog?' + urlencode(
            {'filter-query': branch.query, 'order': 'STOCKS_DESC'})
        async with self.browser.session(self.supplier, self.allowed_hosts) as session:
            await session.goto(url)
            state = await self._state(session)
            paginator = next((v for k, v in state.get('ROOT_QUERY', {}).items()
                              if k.startswith('Catalog_Products_Paginator(')), None)
            if paginator is None:
                raise SupplierError('SUPPLIER_PARSING_ERROR', 'Portobello paginator missing')
            # SSR includes every offer of the page's families, not just the first visible color.
            urls = self.listing_offer_urls(state, paginator)
            parts = urlsplit(url)
            params = dict(parse_qsl(parts.query))
            page = int(params.get('page', 0))
            total_pages = paginator.get('totalPages')
            if not isinstance(total_pages, int) or total_pages < 1:
                raise SupplierError('SUPPLIER_PARSING_ERROR', 'Portobello paginator totalPages missing')
            exhausted = page + 1 >= total_pages
            params['page'] = str(page + 1)
            next_url = None if exhausted else urlunsplit((parts.scheme, parts.netloc, parts.path,
                                                          urlencode(params), ''))
            return ListingPage(urls=list(dict.fromkeys(urls)), next_url=next_url,
                               total=paginator.get('totalDocumentsCount'), total_unit='offers',
                               exhausted=exhausted)

    async def get_product(self, url):
        code = urlsplit(url).path.rsplit('/', 1)[-1]
        if not code.isdigit() or not urlsplit(url).path.startswith('/catalog/offer/'):
            raise SupplierError('SUPPLIER_INVALID_URL', 'Portobello exact offer URL required')
        async with self.browser.session(self.supplier, self.allowed_hosts) as session:
            async with session.page.expect_response(
                lambda r: r.url == 'https://api.portobello.ru/graphql'
                and 'StocksDataLoader' in (r.request.post_data or ''), timeout=30000
            ) as pending:
                await session.goto(url)
            stocks = (await (await pending.value).json()).get('data', {}).get('stocks', [])
            state = await self._state(session)
            images = await session.page.locator('img.thumb').evaluate_all('xs=>xs.map(x=>x.src)')
            return self.normalize_offer(url, code, state, stocks, images)

    def normalize_offer(self, url, code, state, stocks, images):
        p = state.get('CatalogNomenclature:' + json.dumps({'code': code}, separators=(',', ':')))
        if not p:
            raise SupplierError('SUPPLIER_PARSING_ERROR', 'Portobello exact offer missing')
        color = p.get('color1') or {}
        color = state.get(color.get('__ref'), color)
        own = [row for row in stocks if row.get('offerCode') == code
               and row.get('storage', {}).get('type') == 'MAIN']
        stock = conservative_stock(row.get('free') for row in own)
        name = p.get('name')
        if not isinstance(name, str) or not name.strip():
            raise SupplierError('SUPPLIER_PARSING_ERROR', 'Portobello offer name missing')
        attrs = {'Материал': (p.get('material') or {}).get('name'),
                 'Бренд': ', '.join(x.get('name', '') for x in p.get('brandChain', [])
                                                    if isinstance(x, dict) and x.get('name'))}
        if p.get('capacity'):
            attrs['Вместимость'] = str(p['capacity'])
        dims = [p.get(x) for x in ('lengthWithoutPack', 'widthWithoutPack', 'heightWithoutPack')]
        if all(value is not None for value in dims):
            attrs['Размер'] = ' × '.join(str(value) for value in dims)
        product = observed_product(self.supplier, url, code, name, p.get('price'), 'RUB', stock,
                                   color.get('name'), images, attrs, p.get('description'),
                                   stock_source='max_single_StocksDataLoader.free:MAIN')
        if p.get('collection'):
            product.metadata['family'] = p['collection'].get('code')
        return product
