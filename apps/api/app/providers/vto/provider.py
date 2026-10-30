"""Public VTO Store API + published variation JSON. No JavaScript execution."""

import asyncio
import json
import re
import time
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qs, unquote, urlencode, urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup

from app.domain.catalog import CatalogNavigation, CatalogRoute
from app.domain.errors import SupplierError
from app.domain.models import AvailabilityRecord, Product, ProviderResult
from app.domain.research import ListingPage
from app.domain.services import ColorNormalizer
from app.providers.base import ProviderCapabilities, SupplierProvider

BASE = "https://vto.kz"
API = BASE + "/wp-json/wc/store/products"
VERSION = "vto-http-v3"


def plain(value):
    return BeautifulSoup(str(value or ""), "html.parser").get_text(" ", strip=True)


def money(prices):
    value = prices.get("price")
    return (Decimal(str(value)) / (Decimal(10) ** int(prices.get("currency_minor_unit", 0)))
            if value not in (None, "") else None)


def stock_from_html(value):
    text = plain(value)
    match = re.search(r"(\d[\d\s\xa0\u202f]*)\s*(?:в наличии|in stock|шт\b)", text, re.I)
    if match:
        return int(re.sub(r"\D", "", match[1]))
    if re.search(r"out-of-stock|нет в наличии|нет на складе|out of stock", str(value), re.I):
        return 0
    return None


def page_data(document):
    soup = BeautifulSoup(document, "html.parser")
    form = soup.select_one("form.variations_form")
    variants = None
    if form and form.get("data-product_variations") not in (None, "false", ""):
        variants = json.loads(form["data-product_variations"], parse_float=Decimal)
        if not isinstance(variants, list):
            raise ValueError("Invalid variation JSON")
    attributes = {}
    for row in soup.select("table.woocommerce-product-attributes tr"):
        label, value = row.select_one("th"), row.select_one("td")
        if label and value:
            attributes[label.get_text(" ", strip=True)] = value.get_text(" ", strip=True)
    options = {}
    for select in soup.select("form.variations_form select[name]"):
        options[select["name"]] = {
            unquote(option.get("value", "")): option.get_text(" ", strip=True)
            for option in select.select("option[value]") if option.get("value")
        }
    summary = soup.select_one(".summary")
    stock = summary.select_one("p.stock") if summary else None
    return {
        "parent_id": int(form["data-product_id"]) if form else None,
        "variants": variants, "attributes": attributes, "options": options,
        "simple_stock": str(stock) if stock else None,
    }


def api_variant(row):
    """Adapt one verified Store API child; do not infer inventory from flags."""
    attributes = {}
    for part in re.split(r",\s*(?=[^,:]+:)", plain(row.get("variation"))):
        if ":" in part:
            label, value = part.split(":", 1)
            if value.strip():
                attributes[label.strip()] = value.strip()
    if not attributes:
        attributes = {a["name"]: a["value"] for a in row.get("attributes", [])
                      if a.get("name") and a.get("value")}
    if not attributes:
        attributes = {key: values[0] for key, values in parse_qs(urlsplit(row["permalink"]).query).items()
                      if key.startswith("attribute_") and values and values[0]}
    images = row.get("images") or []
    if isinstance(images, dict):
        images = list(images.values())
    quantity = row.get("stock_quantity")
    source_field = "stock_quantity"
    if type(quantity) is not int:
        quantity = row.get("low_stock_remaining")
        source_field = "low_stock_remaining"
    return {
        "variation_id": row["id"], "sku": row.get("sku"), "attributes": attributes,
        "display_price": money(row.get("prices") or {}), "_store_prices": row.get("prices"),
        "availability_html": row.get("availability_html"),
        "stock_quantity": quantity if type(quantity) is int else None,
        "_stock_source": "Store API variation." + source_field,
        "_source": "STORE_API_VARIATION", "_source_id": row["id"],
        "is_in_stock": row.get("is_in_stock"), "backorders_allowed": row.get("is_on_backorder", False),
        "image": images[0] if images else {},
    }


def normalize(parent, detail):
    parent_id = int(parent["id"])
    variable = parent.get("type") == "variable"
    if detail.get("parent_id") not in (None, parent_id):
        raise ValueError("Detail/parent identity mismatch")
    variants = detail.get("variants") if variable else [None]
    if variable and variants == [] and not parent.get("variations") and not parent.get("is_purchasable"):
        # Publicly empty variable family is not a sellable parent offer.
        return []
    if variable and not variants:
        raise ValueError("Variable product has no publicly confirmed variations")
    advertised = {int(v["id"]) for v in parent.get("variations", [])}
    actual = {int(v["variation_id"]) for v in variants if v}
    if variable and advertised and not advertised <= actual:
        raise ValueError("Published variation set is incomplete")
    attrs = detail.get("attributes", {})
    categories = parent.get("categories", [])
    # Brand/collection navigation is retained separately, never used as a type.
    category_roles = []
    for category in categories:
        slug = category.get("slug", "").casefold()
        link = category.get("link", "").casefold()
        name = category["name"]
        role = ("MANUFACTURER" if slug in {"oasis", "gifts", "ucontay", "portobello", "happygifts", "artegifts"}
                else "BRAND" if name.casefold() == str(attrs.get("Бренд", "")).casefold()
                or "/brands/" in link or "/brand/" in link
                else "COLLECTION" if "/collections/" in link or slug in {"new", "novinki", "sale", "sales", "hit"}
                else "NAVIGATION" if slug in {"categories", "brands"} else "PRODUCT_TYPE")
        category_roles.append({**category, "role": role})
    type_categories = [category["name"] for category in category_roles if category["role"] == "PRODUCT_TYPE"]
    products = []
    for variant in variants:
        vid = int(variant["variation_id"]) if variant else None
        values = {}
        inherited_attributes = {}
        for key, value in (variant or {}).get("attributes", {}).items():
            if not value:
                choices = detail.get("options", {}).get(key, {})
                if len(choices) != 1:
                    raise ValueError("Wildcard variant attribute cannot identify a sellable combination")
                # Only one published form option: inherit it for this real ID.
                # VTO get_variation was checked for this unique-choice case.
                value = next(iter(choices))
                inherited_attributes[key] = "UNIQUE_PUBLISHED_FORM_OPTION"
            values[key] = detail.get("options", {}).get(key, {}).get(unquote(value), unquote(value))
        raw_color = next((value for key, value in values.items() if "color" in key.casefold() or "цвет" in key.casefold()), None)
        effective_attrs = dict(attrs)
        variant_attribute_labels = set()
        for label in attrs:
            if label in values:
                effective_attrs[label] = values[label]
                variant_attribute_labels.add(label)
        for attribute in parent.get("attributes", []):
            key = "attribute_" + attribute.get("taxonomy", "")
            if key in values:
                effective_attrs[attribute["name"]] = values[key]
                variant_attribute_labels.add(attribute["name"])
        if not variable:
            raw_color = attrs.get("Цвет")
        prices = parent.get("prices") or {}
        currency = ((variant or {}).get("_store_prices") or {}).get("currency_code") or prices.get("currency_code")
        if currency not in (None, "KZT"):
            raise ValueError("VTO price currency is not KZT")
        price = (Decimal(str(variant["display_price"]))
                 if variant and variant.get("display_price") is not None else
                 None if variable else money(prices))
        raw_stock = variant.get("availability_html") if variant else detail.get("simple_stock")
        published_quantity = (variant or parent).get("stock_quantity")
        quantity = (published_quantity if type(published_quantity) is int and published_quantity >= 0
                    else stock_from_html(raw_stock))
        backorder = bool((variant or {}).get("backorders_allowed") and not (variant or {}).get("is_in_stock"))
        backorder = backorder or (not variable and parent.get("is_on_backorder", False))
        backorder = backorder or bool(re.search(r"available-on-backorder|предзаказ|под заказ", str(raw_stock), re.I))
        if backorder:
            quantity = None
        image = (variant or {}).get("image") or {}
        images = ([urljoin(BASE, image.get("full_src") or image.get("src") or image.get("url"))]
                  if any(image.get(key) for key in ("full_src", "src", "url")) else [])
        if not variable:
            published_images = parent.get("images") or []
            if isinstance(published_images, dict):
                # VTO sometimes serializes a PHP array with non-zero keys as an object.
                published_images = published_images.values()
            images = [urljoin(BASE, x["src"]) for x in published_images if isinstance(x, dict) and x.get("src")]
        stock_scope = ("PARENT_SHARED" if variable and quantity is None
                       and isinstance(parent.get("stock_quantity"), int)
                       else "VARIANT" if variable else "PRODUCT")
        evidence = {"source": ("variation.stock_quantity" if variable else "product.stock_quantity")
                    if type(published_quantity) is int else "variation.availability_html" if variable else "product.summary.stock",
                    "raw": published_quantity if type(published_quantity) is int else raw_stock, "scope": stock_scope,
                    "semantics": "SUPPLIER_PUBLISHED_QUANTITY", "variation_id": vid}
        if variant and variant.get("_source"):
            evidence["source"] = variant["_stock_source"] if quantity is not None else "Store API: quantity not published"
        url = parent["permalink"].split("?", 1)[0]
        # HTTP checks did not confirm server-side preselection from query attributes.
        # Link to the parent; retain selected color/SKU/ID in the application.
        size = next((value for key, value in values.items() if "size" in key.casefold() or "размер" in key.casefold()), None)
        product = Product(
            id=f"vto:{vid if vid else parent_id}", supplier="vto", source_url=url,
            name=plain(parent["name"]) + (" — " + ", ".join(values.values()) if values else ""),
            description=plain(parent.get("description")), category=type_categories[0] if type_categories else None,
            original_price=price, original_currency=currency,
            price_kzt=int(price.quantize(Decimal("1"), rounding=ROUND_HALF_UP)) if price is not None else None,
            stock_quantity=quantity, total_quantity=quantity,
            availability_status="PREORDER" if backorder else (
                "UNAVAILABLE_NOW" if quantity == 0 else "AVAILABLE_NOW" if quantity is not None else "UNKNOWN"),
            available=quantity is not None and quantity > 0,
            colors=[ColorNormalizer().normalize(raw_color)] if raw_color else [],
            images=images, primary_image=images[0] if images else None,
            brand=effective_attrs.get("Бренд"), material=effective_attrs.get("Материал"), dimensions=effective_attrs.get("Габариты"),
            availability_parser_version=VERSION, source_availability_payload=evidence,
            metadata={"vto_product_id": parent_id, "vto_variation_id": vid,
                      "parent_name": plain(parent["name"]), "sku": (variant or parent).get("sku"),
                      "stock_scope": stock_scope, "stock_semantics": "SUPPLIER_PUBLISHED_QUANTITY",
                      "parent_shared_stock": parent.get("stock_quantity") if stock_scope == "PARENT_SHARED" else None,
                      "source_stock_status": {"is_in_stock": (variant or parent).get("is_in_stock"),
                                              "is_on_backorder": backorder},
                      "variant_attributes": values, "size": size, "attributes": effective_attrs,
                      "raw_variant_attributes": dict((variant or {}).get("attributes", {})),
                      "inherited_variant_attributes": inherited_attributes,
                      "variant_source": (variant or {}).get("_source", "PUBLISHED_VARIATION_JSON" if variable else "STORE_API_PRODUCT"),
                      "variant_store_prices": (variant or {}).get("_store_prices"),
                      "supplier_category": type_categories[0] if type_categories else "", "breadcrumbs": type_categories,
                      "vto_categories": categories,
                      "vto_category_roles": category_roles,
                      "original_prices": {"parent": prices, "variation": {
                          key: str(variant.get(key)) for key in ("display_price", "display_regular_price")
                      } if variant else None},
                      "image_provenance": "VARIANT" if variable and images else "PRODUCT" if images else "MISSING"},
            evidence={"stock_quantity": evidence, "price": {"source": "variation.display_price" if variable else "Store API prices", "currency": currency},
                      "brand": {"source": "variation.attributes" if "Бренд" in variant_attribute_labels else "parent HTML attributes"},
                      "material": {"source": "variation.attributes" if "Материал" in variant_attribute_labels else "parent HTML attributes"}},
        )
        product.availability_records = [AvailabilityRecord(
            location_label="Остаток VTO",
            state="PREORDER" if backorder else "ON_HAND" if quantity is not None else "UNKNOWN",
            total_quantity=quantity, free_quantity=quantity,
            parser_version=VERSION, source_label="VTO: опубликованный остаток",
            source_evidence=evidence, observed_at=product.fetched_at,
        )]
        products.append(product)
    if len({p.id for p in products}) != len(products):
        raise ValueError("Duplicate VTO variation ids")
    return products


class VTOProvider(SupplierProvider):
    supplier = "vto"
    parser_version = VERSION
    capabilities = ProviderCapabilities(
        discovery="public Store API", identity="WooCommerce product/variation ID", pagination="explicit page/per_page + totals",
        variants="Store API children + published variation JSON", price="Decimal", currency="KZT", stock="published variant evidence or UNKNOWN",
        images="variant image", category_path="VTO categories", refresh="HTTP",
    )

    def __init__(self, settings, client=None):
        self.settings = settings
        self.client = client or httpx.AsyncClient(timeout=httpx.Timeout(30, connect=10), follow_redirects=False,
                                                  limits=httpx.Limits(max_connections=2, max_keepalive_connections=2))
        self.slots = asyncio.Semaphore(2)
        self.pace = asyncio.Lock()
        self.last_request = 0.0
        self.requests = 0
        self.errors = 0
        self.cache = {}  # At most one collection page; not a second catalog.

    async def request(self, url, data=None):
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname != "vto.kz" or parsed.username or parsed.password:
            raise SupplierError("INVALID_SOURCE", "VTO URL required")
        async with self.slots:
            for attempt in range(3):
                async with self.pace:
                    await asyncio.sleep(max(0, self.settings.vto_request_delay_seconds - (time.monotonic() - self.last_request)))
                    self.last_request = time.monotonic()
                try:
                    self.requests += 1
                    response = (await self.client.get(url) if data is None
                                else await self.client.post(url, data=data))
                    if response.status_code in (429, 500, 502, 503, 504):
                        retry = response.headers.get("Retry-After", "")
                        try:
                            delay = float(retry) if retry.isdigit() else (
                                max(0, (parsedate_to_datetime(retry) - datetime.now(UTC)).total_seconds())
                                if retry else 2 ** attempt)
                        except (ValueError, TypeError):
                            delay = 2 ** attempt
                        if delay > 60:
                            raise SupplierError("VTO_HTTP_ERROR", f"VTO Retry-After: {int(delay)} seconds", retryable=True)
                        await asyncio.sleep(delay)
                        response.raise_for_status()
                    response.raise_for_status()
                    return response
                except (httpx.TimeoutException, httpx.HTTPStatusError, httpx.NetworkError) as exc:
                    self.errors += 1
                    if attempt == 2 or (isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code not in (429, 500, 502, 503, 504)):
                        raise SupplierError("VTO_HTTP_ERROR", str(exc), retryable=True) from exc

    async def collection(self, page=1, per_page=20, search=None):
        params = {"page": page, "per_page": per_page, "orderby": "date", "order": "desc"}
        if search:
            params["search"] = search
        response = await self.request(API + "?" + urlencode(params))
        data = response.json()
        if not isinstance(data, list):
            raise SupplierError("VTO_INCOMPLETE_RESPONSE", "Missing product collection")
        total, pages = int(response.headers["X-WP-Total"]), int(response.headers["X-WP-TotalPages"])
        if not data and not (search and total == 0):
            raise SupplierError("VTO_INCOMPLETE_RESPONSE", "Unexpected empty product collection")
        if len(data) != min(per_page, total - (page - 1) * per_page):
            raise SupplierError("VTO_INCOMPLETE_RESPONSE", "Collection page length differs from declared total")
        return data, total, pages

    async def products(self, parent):
        response = await self.request(parent["permalink"].split("?", 1)[0])
        try:
            detail = await asyncio.to_thread(page_data, response.text)
            advertised = {int(row["id"]) for row in parent.get("variations", [])}
            published = {int(row["variation_id"]) for row in detail["variants"] or []}
            if parent.get("type") == "variable" and (not published or advertised - published):
                children = await self.variation_collection(parent["id"])
                confirmed = {int(row["variation_id"]): row for row in detail["variants"] or []}
                for child in children:
                    confirmed.setdefault(int(child["id"]), api_variant(child))
                detail["variants"] = list(confirmed.values())
            if (parent.get("type") == "variable" and not published and parent.get("variations")
                    and "add-to-cart-variation" in response.text and detail["options"]):
                # VTO loads large variation sets via its published WooCommerce
                # add-to-cart-variation.js get_variation call. Only combinations
                # explicitly listed by Store API are requested; never a cart action.
                if "add-to-cart-variation" not in response.text:
                    raise ValueError("No published variation request mechanism")
                fields = {attribute["name"]: "attribute_" + attribute["taxonomy"]
                          for attribute in parent.get("attributes", []) if attribute.get("taxonomy")}
                confirmed = {int(row["variation_id"]): row for row in detail["variants"] or []}
                for row in parent["variations"]:
                    attributes = {fields[a["name"]]: a["value"] for a in row["attributes"] if a["name"] in fields}
                    if not attributes or not set(attributes) <= set(detail["options"]):
                        continue
                    for key, value in attributes.items():
                        if not value and len(detail["options"][key]) == 1:
                            attributes[key] = next(iter(detail["options"][key]))
                    result = await self.request(BASE + "/?wc-ajax=get_variation",
                                                {"product_id": parent["id"], **attributes})
                    variant = result.json(parse_float=Decimal)
                    if isinstance(variant, dict) and int(variant.get("variation_id", 0)) == int(row["id"]):
                        confirmed[int(row["id"])] = variant
                detail["variants"] = list(confirmed.values())
            return await asyncio.to_thread(normalize, parent, detail)
        except (ValueError, KeyError, TypeError) as exc:
            raise SupplierError("VTO_PARSING_ERROR", str(exc)) from exc

    async def variation_collection(self, parent_id):
        children, page = [], 1
        while True:
            response = await self.request(API + "?" + urlencode({"type": "variation", "parent": parent_id,
                                                                  "per_page": 20, "page": page}))
            data = response.json()
            total, pages = int(response.headers["X-WP-Total"]), int(response.headers["X-WP-TotalPages"])
            if not isinstance(data, list) or any(int(row.get("parent", 0)) != int(parent_id) for row in data):
                raise SupplierError("VTO_PARENT_FILTER_ERROR", "Variation response does not belong to this parent")
            if not data and total:
                raise SupplierError("VTO_INCOMPLETE_RESPONSE", "Missing variation page")
            children.extend(data)
            if page >= pages:
                if len({int(row["id"]) for row in children}) != total:
                    raise SupplierError("VTO_INCOMPLETE_RESPONSE", "Variation coverage differs from declared count")
                return children
            page += 1

    async def research_products(self, url):
        slug = urlsplit(url).path.rstrip("/").split("/")[-1]
        cached = self.cache.get(slug)
        if cached is None:
            response = await self.request(API + "?" + urlencode({"slug": slug}))
            data = response.json()
            if not isinstance(data, list) or len(data) != 1:
                raise SupplierError("VTO_NOT_FOUND", "Product slug not found")
            cached = data[0]
        return await self.products(cached)

    async def get_product(self, url):
        products = await self.research_products(url)
        vid = parse_qs(urlsplit(url).query).get("variation_id", [None])[0]
        if vid:
            return next(p for p in products if p.id == f"vto:{vid}")
        if len(products) != 1:
            raise SupplierError("VARIANT_REQUIRED", "Select a real variation")
        return products[0]

    async def get_stock(self, product):
        return next(p.stock_quantity for p in await self.research_products(product.source_url) if p.id == product.id)

    async def get_price(self, product):
        fresh = next(p for p in await self.research_products(product.source_url) if p.id == product.id)
        return fresh.original_price, fresh.original_currency

    async def get_images(self, product):
        return next(p.images for p in await self.research_products(product.source_url) if p.id == product.id)

    async def catalog_navigation(self, url=None):
        return CatalogNavigation(routes=[] if url else [CatalogRoute(url=API, label="VTO catalog")])

    async def research_listing(self, branch):
        page = max(1, branch.cursor.page_number or 1)
        data, total, pages = await self.collection(page, self.settings.vto_page_size, branch.query or None)
        self.cache = {row["permalink"].rstrip("/").split("/")[-1]: row for row in data}
        return ListingPage(urls=[row["permalink"] for row in data], total=total, total_unit="families",
                           exhausted=page >= pages, next_url=None if page >= pages else API + "?page=" + str(page + 1))

    async def search(self, query, filters):
        # Ordinary interactive search reads PostgreSQL; only background research calls this path.
        data, _, _ = await self.collection(search=query)
        products = []
        for parent in data:
            products.extend(await self.products(parent))
        return ProviderResult(products=products, pages_scanned=1)

    async def health_check(self):
        await self.collection(per_page=1)
        return True

    async def close(self):
        await self.client.aclose()
