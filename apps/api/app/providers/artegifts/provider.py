import re
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit

from app.domain.errors import SupplierError
from app.domain.research import ListingPage
from app.providers.base import ProviderCapabilities
from app.providers.research_support import ResearchEnabledProvider, conservative_stock, observed_product


class ArteGiftsProvider(ResearchEnabledProvider):
    supplier = 'artegifts'
    base_url = 'https://kz.artegifts.by'
    allowed_hosts = {'kz.artegifts.by'}
    capabilities = ProviderCapabilities(
        discovery='published /catalog links', identity='configurator active SKU id',
        pagination='load-more PAGEN_1 link', variants='product color data-sku-id',
        price='configurator priceRrc', currency='configurator currency',
        stock='maximum one selected-variant store available quantity', images='active product image',
        category_path='observed catalog route', refresh='exact product active_sku_id URL')

    async def research_listing(self, branch):
        route = branch.route or (self.base_url + '/catalog/butylki/' if branch.query == 'бутылка' else None)
        branch.route = route
        url = branch.cursor.listing_url or route or self.base_url + '/catalog/search/?' + urlencode({'q': branch.query})
        async with self.browser.session(self.supplier, self.allowed_hosts) as session:
            await session.goto(url)
            body = (await session.page.locator('body').inner_text()).casefold()
            urls = await session.page.locator('a.title.title-base[href]').evaluate_all('xs=>xs.map(x=>x.href)')
            if not urls:
                body = (await session.page.locator('body').inner_text()).casefold()
                if not any(x in body for x in ('не найден', 'ничего не', 'найдено\n0')):
                    raise SupplierError('SUPPLIER_PARSING_ERROR', 'ArteGifts listing not recognized')
            link = session.page.locator('a.load-more[href]').first
            next_url = urljoin(self.base_url, await link.get_attribute('href')) if await link.count() else None
            total = re.search(r'найдено\s+(\d+)', body)
            return ListingPage(urls=list(dict.fromkeys(urls)), next_url=next_url, exhausted=not next_url,
                               total=int(total[1]) if total else None, total_unit='families')

    async def get_product(self, url):
        async with self.browser.session(self.supplier, self.allowed_hosts) as session:
            await session.goto(url)
            data = await session.page.evaluate('''()=>{
                const node=document.querySelector('dataconf');
                const raw=node && [...node.attributes].find(x=>x.value.startsWith('{"product":'));
                const product=raw?JSON.parse(raw.value).product:null;
                const attrs={};document.querySelectorAll('.product-features__item').forEach(x=>{
                    const k=x.querySelector('.product-features__prop')?.textContent.trim();
                    if(k && !(k in attrs))attrs[k]=x.querySelector('.product-features__val')?.textContent.trim();
                });
                return {product,attrs,images:[document.querySelector('img.js-fly-img')?.src].filter(Boolean),
                    variants:[...document.querySelectorAll('.product-colors__item[data-sku-id]')].map(x=>x.getAttribute('data-sku-id'))};
            }''')
            return self.normalize_configurator(url, data)

    def normalize_configurator(self, url, data):
        p = data['product']
        if not p or not p.get('id'):
            raise SupplierError('SUPPLIER_PARSING_ERROR', 'ArteGifts exact variant missing')
        stock = conservative_stock(s.get('available') for s in p.get('stores', []))
        product = observed_product(self.supplier, url, p['id'], p['name'], p.get('priceRrc'),
                                   p.get('currency'), stock, data['attrs'].get('Цвет'),
                                   data['images'], data['attrs'], stock_source='max_single_store.available')
        parts = urlsplit(url)
        product.metadata['variant_urls'] = [urlunsplit((parts.scheme, parts.netloc, parts.path,
                                            urlencode({'active_sku_id': v}), '')) for v in data['variants']]
        return product
