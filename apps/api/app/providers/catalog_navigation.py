"""Read published navigation links, with site-specific URL shapes observed in Chromium.

No taxonomy, generated query, guessed pagination or model-provided URL is used.
"""
import re
from urllib.parse import urlsplit, urlunsplit

from app.domain.catalog import CatalogNavigation, CatalogRoute
from app.domain.errors import SupplierError

HOMES = {
    'gifts': 'https://gifts.ru/', 'ucontay': 'https://ucontay.kz/',
    'portobello': 'https://portobello.ru/', 'happygifts': 'https://happygifts.ru/',
    'artegifts': 'https://kz.artegifts.by/', 'oasis': 'https://www.oasiscatalog.com/',
}
PATHS = {
    'gifts': r'/catalog/[^/]+/?',
    'ucontay': r'/collection/[^/]+/?',
    'portobello': r'/catalog(?:/[^/]+)?/?',
    'happygifts': r'/catalog/[^/]+/?',
    'artegifts': r'/catalog/[^/]+/?',
    'oasis': r'/categories/[^?#]+',
}


def catalog_links(supplier, links, parent):
    home = urlsplit(HOMES[supplier])
    routes = {}
    for link in links:
        parts = urlsplit(link['url'])
        if parts.scheme != 'https' or parts.hostname != home.hostname or parts.query:
            continue
        if not re.fullmatch(PATHS[supplier], parts.path):
            continue
        url = urlunsplit((parts.scheme, parts.netloc, parts.path, '', ''))
        if url != parent:
            routes[url] = CatalogRoute(url=url, label=link.get('label', ''), parent_url=parent)
    return list(routes.values())


async def navigation(provider, url=None):
    home = HOMES.get(provider.supplier)
    if not home:
        raise SupplierError('SUPPLIER_DISCOVERY_UNSUPPORTED', 'No observed catalog navigation')
    url = url or home
    hosts = {urlsplit(home).hostname}
    async with provider.browser.session(provider.supplier, hosts) as session:
        await session.goto(url)
        links = await session.page.locator('a[href]').evaluate_all(
            'xs=>xs.map(x=>({url:x.href,label:x.textContent.trim().slice(0,150)}))')
        routes = catalog_links(provider.supplier, links, url)
        if url == home and not routes:
            raise SupplierError('SUPPLIER_PARSING_ERROR', 'Catalog roots not observed')
        return CatalogNavigation(routes=routes)
