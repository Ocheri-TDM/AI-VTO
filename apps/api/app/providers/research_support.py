"""Shared mechanics only; site selectors belong to individual provider modules."""
import re
from decimal import Decimal, InvalidOperation
from html import unescape
from urllib.parse import urljoin

from app.domain.errors import SupplierError
from app.domain.intent import CategoryResolver
from app.domain.models import AvailabilityRecord, Product, ProviderResult, SearchIntent
from app.domain.research import ResearchCoverage
from app.domain.services import ColorNormalizer
from app.providers.base import SupplierProvider


def text(value):
    return re.sub(r'\s+', ' ', unescape(re.sub('<[^>]+>', ' ', str(value or '')))).strip()


def number(value):
    try:
        return Decimal(re.sub(r'\s+', '', str(value)).replace(',', '.'))
    except InvalidOperation:
        return None


def conservative_stock(values):
    """Prove quantity at one warehouse; never add potentially overlapping stocks."""
    values = list(values)
    if not values or not all(isinstance(value, (int, float)) and value >= 0
                             and float(value).is_integer() for value in values):
        return None
    return int(max(values))


def observed_product(supplier, url, identifier, name, price, currency, stock, color, images,
                     attributes=None, description=None, stock_source='product_page', *,
                     incoming=None, reserved=None, total=None, incoming_date=None,
                     availability_payload=None, parser_version='availability-v1'):
    attrs = attributes or {}
    categories = CategoryResolver().classify(name)
    normalizer = ColorNormalizer()
    original = color or name
    normalized = normalizer.normalize(original)
    product = Product(
        id=f'{supplier}:{identifier}', supplier=supplier, source_url=url, name=text(name),
        category=categories[0] if categories else None, original_price=number(price),
        original_currency=currency, stock_quantity=stock, available=stock is not None and stock > 0,
        incoming_quantity=incoming, reserved_quantity=reserved, total_quantity=total,
        incoming_date=incoming_date,
        availability_status=('AVAILABLE_NOW' if stock is not None and stock > 0 else
                             'UNAVAILABLE_NOW' if stock == 0 else 'UNKNOWN'),
        incoming_status=('INCOMING' if incoming is not None and incoming > 0 else
                         'NO_INCOMING' if incoming == 0 else None),
        source_availability_payload=availability_payload or ({'available_now': stock} if stock is not None else {}),
        availability_parser_version=parser_version,
        colors=[normalized],
        images=list(dict.fromkeys(urljoin(url, x) for x in images if x)),
        description=text(description) or None, metadata={'attributes': attrs},
        material=attrs.get('Материал') or attrs.get('Материал товара'), brand=attrs.get('Бренд'),
        dimensions=attrs.get('Размер') or attrs.get('Размер товара'),
        capacity=attrs.get('Объем в литрах') or attrs.get('Вместимость'),
    )
    if stock is not None:
        product.availability_records.append(AvailabilityRecord(
            state='ON_HAND', total_quantity=total, free_quantity=stock,
            reserved_quantity=reserved, observed_at=product.fetched_at,
            parser_version=parser_version, source_label=stock_source,
            source_evidence=availability_payload or {'value': stock},
        ))
    if incoming is not None:
        product.availability_records.append(AvailabilityRecord(
            state='INCOMING', free_quantity=incoming, expected_at=incoming_date,
            observed_at=product.fetched_at, parser_version=parser_version,
            source_label='supplier incoming',
            source_evidence=availability_payload or {'value': incoming},
        ))
    volume = attrs.get('Объем', '')
    if not product.capacity and re.search(r'мл|\bml\b|\bл\b', volume.casefold()):
        product.capacity = volume
    product.primary_image = product.images[0] if product.images else None
    for field in ('name', 'original_price', 'original_currency', 'stock_quantity', 'colors',
                  'material', 'brand', 'dimensions', 'capacity', 'description', 'images'):
        value = product.model_dump(mode='json')[field]
        if value is not None and value != []:
            product.evidence[field] = {'value': value, 'source_url': url,
                                      'source': stock_source if field == 'stock_quantity' else 'product_page'}
    return product


class ResearchEnabledProvider(SupplierProvider):
    """Legacy search stays bounded; research calls listing/products directly."""
    def __init__(self, browser, settings):
        self.browser, self.settings = browser, settings

    async def search(self, query: str, filters: SearchIntent) -> ProviderResult:
        branch = ResearchCoverage(supplier=self.supplier,
                                  category=filters.categories[0] if filters.categories else '', query=query)
        result = ProviderResult()
        for _ in range(self.settings.max_pages_per_query):
            page = await self.research_listing(branch)
            result.pages_scanned += 1
            for url in page.urls:
                if len(result.products) >= self.settings.max_products_per_query:
                    result.warnings.append('Legacy search limit; use research for complete traversal.')
                    return result
                result.products.extend(await self.research_products(url))
            if page.exhausted:
                return result
            branch.cursor.listing_url = page.next_url
            branch.cursor.page_number += 1
        result.warnings.append('Legacy page limit; use research for complete traversal.')
        return result

    async def health_check(self):
        try:
            async with self.browser.session(self.supplier, self.allowed_hosts) as session:
                await session.goto(self.base_url)
            return True
        except SupplierError:
            return False
