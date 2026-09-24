import re
from datetime import UTC, datetime
from urllib.parse import urlencode, urljoin, urlsplit

from app.domain.errors import SupplierError
from app.domain.models import AvailabilityRecord
from app.domain.research import ListingPage
from app.providers.base import ProviderCapabilities
from app.providers.research_support import ResearchEnabledProvider, observed_product


class HappyGiftsProvider(ResearchEnabledProvider):
    parser_version = "happygifts-availability-v3"
    supplier = 'happygifts'
    base_url = 'https://happygifts.ru'
    allowed_hosts = {'happygifts.ru', 'www.happygifts.ru'}
    capabilities = ProviderCapabilities(
        discovery='published /catalog links', identity='active color data-id-value',
        pagination='next-pag link', variants='color-item data-url variants',
        price='active product DOM price', currency='RUB', stock='central warehouse free tooltip',
        images='active color thumbnail', category_path='observed catalog route', refresh='exact color URL')

    def targeted_category_route(self, product_url):
        parts = urlsplit(product_url)
        segments = [part for part in parts.path.split('/') if part]
        if len(segments) >= 3 and segments[0] == 'catalog':
            return f'{parts.scheme}://{parts.netloc}/catalog/{segments[1]}/{segments[2]}/'
        return None

    async def research_listing(self, branch):
        route = branch.route or (self.base_url + '/catalog/butylki_dlya_vody/' if branch.query == 'бутылка' else None)
        branch.route = route
        url = branch.cursor.listing_url or route or self.base_url + '/catalog/?' + urlencode({'q': branch.query})
        async with self.browser.session(self.supplier, self.allowed_hosts) as session:
            await session.goto(url)
            urls = await session.page.locator('a.product-card__title[href]').evaluate_all('xs=>xs.map(x=>x.href)')
            body = (await session.page.locator('body').inner_text()).casefold()
            if not urls and not any(x in body for x in ('не найден', 'ничего не', '0 товаров')):
                raise SupplierError('SUPPLIER_PARSING_ERROR', 'HappyGifts listing not recognized')
            link = session.page.locator('a.next-pag[href]').first
            next_url = urljoin(self.base_url, await link.get_attribute('href')) if await link.count() else None
            count = session.page.locator('.left-filters__category-count').first
            count_text = (await count.inner_text()).strip() if await count.count() else ''
            return ListingPage(urls=list(dict.fromkeys(urls)), next_url=next_url, exhausted=not next_url,
                               total=int(count_text) if count_text.isdigit() else None, total_unit='families')

    async def _read(self, session, url):
        await session.goto(url)
        data = await session.page.evaluate('''()=>{
            const attrs={};document.querySelectorAll('p > b.d-block').forEach(b=>{
                const key=b.textContent.trim();if(!(key in attrs))attrs[key]=b.parentElement.textContent.replace(b.textContent,'').trim();
            });
            const active=document.querySelector('.product-colors-container .color-item.active');
            const availability=[...new Set([...document.querySelectorAll('.avilability-tabs-item')]
                .map(x=>x.innerText?.trim()).filter(Boolean))];
            return {name:document.querySelector('h1')?.textContent,
                price:document.querySelector('#vu-price_0')?.textContent,
                availability,color:active?.getAttribute('data-color'),
                id:active?.getAttribute('data-id-value'),attrs,
                images:[...document.querySelectorAll('img')].map(x=>x.src).filter(x=>x.includes('/catalog-images-webp/') && x.includes('/photo/')),
                variants:[...document.querySelectorAll('.product-colors-container .color-item[data-url]')].map(x=>x.getAttribute('data-url'))};
        }''')
        # Only current color photos: thumbnail URL identifies the selected variant.
        active = session.page.locator('.product-colors-container .color-item.active img').first
        thumbnail = await active.get_attribute('src') if await active.count() else None
        return self.normalize_page(url, data, thumbnail)

    def normalize_page(self, url, data, thumbnail):
        if not data['name'] or not data['id']:
            raise SupplierError('SUPPLIER_PARSING_ERROR', 'HappyGifts exact variant missing')
        blocks = data.get('availability') or ([data['stock']] if data.get('stock') else [])
        central = max((x for x in blocks if 'Центральный' in x), key=len, default='')
        free_match = re.search(r'Свободно\s*([\d\s]+)\s*шт', central, re.IGNORECASE)
        reserve_match = re.search(r'В\s+резерве\s*([\d\s]+)\s*шт', central, re.IGNORECASE)
        stock = int(re.sub(r'\s', '', free_match[1])) if free_match else None
        reserved = int(re.sub(r'\s', '', reserve_match[1])) if reserve_match else None
        total = stock + reserved if stock is not None and reserved is not None else stock
        images = [urljoin(self.base_url, thumbnail.strip())] if thumbnail else []
        product = observed_product(
            self.supplier, url, data['id'], data['name'], data['price'], 'RUB', stock,
            data['color'], images, data['attrs'], stock_source='central_warehouse_free_tooltip',
            availability_payload={'blocks': [x[:500] for x in blocks], 'available_now': stock,
                                  'reserved': reserved, 'total': total},
            parser_version='happygifts-availability-v3',
        )
        product.reserved_quantity = reserved
        product.total_quantity = total
        if product.availability_records:
            product.availability_records[0].location_code = 'CENTRAL'
            product.availability_records[0].location_label = 'Центральный склад'
            product.availability_records[0].reserved_quantity = reserved
            product.availability_records[0].total_quantity = total
            product.availability_records[0].source_evidence = {'text': central[:500]}
        for block in blocks:
            if 'В пути' not in block:
                continue
            quantity_match = re.search(r'(?:В\s*пути|\u041eжидается)\D{0,30}([\d\s]+)\s*шт', block, re.IGNORECASE)
            date_match = re.search(r'Поступит\s+на\s+склад\s+(\d{2}\.\d{2}\.\d{4})', block, re.IGNORECASE)
            quantity = int(re.sub(r'\s', '', quantity_match[1])) if quantity_match else None
            expected = datetime.strptime(date_match[1], '%d.%m.%Y').replace(tzinfo=UTC) if date_match else None
            product.availability_records.append(AvailabilityRecord(
                location_code='IN_TRANSIT', location_label='В пути', state='INCOMING',
                free_quantity=quantity, total_quantity=quantity, expected_at=expected,
                parser_version='happygifts-availability-v3', source_label='availability_tabs',
                source_evidence={'text': block[:500]}, confidence='HIGH' if quantity is not None else 'MEDIUM',
            ))
            if quantity is not None:
                product.incoming_quantity = (product.incoming_quantity or 0) + quantity
            if expected and (product.incoming_date is None or expected < product.incoming_date):
                product.incoming_date = expected
        return product, data['variants']

    async def get_product(self, url):
        async with self.browser.session(self.supplier, self.allowed_hosts) as session:
            product, _ = await self._read(session, url)
            return product

    async def research_products(self, url):
        # Expose variant links as additional traversal candidates through the product metadata.
        async with self.browser.session(self.supplier, self.allowed_hosts) as session:
            product, variants = await self._read(session, url)
            product.metadata['variant_urls'] = [urljoin(self.base_url, v) for v in variants
                                                if urlsplit(urljoin(self.base_url, v)).hostname in self.allowed_hosts]
            return [product]
