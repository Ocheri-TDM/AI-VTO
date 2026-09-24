"""Pure normalization of Oasis public structured page data.

Observed field provenance and stock semantics: docs/providers/oasis.md.
"""

import re
from decimal import Decimal, InvalidOperation
from html import unescape
from typing import Any
from urllib.parse import urljoin, urlsplit

from app.domain.errors import SupplierError
from app.domain.models import ColorGroup, Product, ProductColor
from app.domain.services import ColorNormalizer

BASE_URL = "https://www.oasiscatalog.com"
ALLOWED_HOSTS = {"www.oasiscatalog.com", "oasiscatalog.com"}


def product_url(value: str) -> str:
    url = urljoin(BASE_URL, value)
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in ALLOWED_HOSTS
        or parsed.username
        or parsed.port not in (None, 443)
        or not re.fullmatch(r"/item/[A-Za-z0-9-]+", parsed.path)
    ):
        raise SupplierError("SUPPLIER_INVALID_URL", "Некорректная ссылка карточки Oasis.")
    return f"{BASE_URL}{parsed.path}"


def plain_text(value: Any) -> str:
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", str(value or ""))).split())


def numeric_stock(value: Any) -> int | None:
    # Strings such as 'in stock', '300+', booleans and fractions are not proof.
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = Decimal(str(value).replace("\u00a0", "").replace(" ", ""))
    except InvalidOperation:
        return None
    if not number.is_finite() or number != number.to_integral_value():
        return None
    return max(0, int(number))


def variant_stock(data: dict[str, Any]) -> int | None:
    """Only the exact Moscow variant; stock already excludes separate reserve."""
    warehouse = data.get("settings", {}).get("productWarehouses", {}).get("main", {})
    rows = warehouse.get("sizes") or []
    matches = [row for row in rows if str(row.get("id")) == str(data.get("id"))]
    if len(matches) != 1:
        return None
    return numeric_stock(matches[0].get("stock"))


def candidate_urls(cards: list[dict[str, Any]], colors: list[ColorGroup]) -> list[str]:
    normalizer = ColorNormalizer()
    result: list[str] = []
    for card in cards:
        variants = card.get("colorsProduct") or []
        if not variants:
            variants = [{"url": card.get("link"), "colorName": ""}]
        for variant in variants:
            color = str(variant.get("colorName") or "")
            if colors and color and not normalizer.matches([normalizer.normalize(color)], colors):
                continue
            value = variant.get("url")
            if value:
                try:
                    result.append(product_url(value))
                except SupplierError:
                    continue
    return list(dict.fromkeys(result))


def normalize_product(data: dict[str, Any], structured: dict[str, Any], url: str) -> Product:
    canonical = product_url(url)
    variant_id = canonical.rsplit("/", 1)[-1]
    if str(data.get("id")) != variant_id or not data.get("name"):
        raise SupplierError("SUPPLIER_PARSE_ERROR", "Не удалось подтвердить вариант товара Oasis.")
    offer = structured.get("offers") or {}
    if isinstance(offer, list):
        offer = next((item for item in offer if item.get("url", "").endswith(f"/{variant_id}")), {})
    # Product JSON-LD is selected by exact active variant; no min family price.
    price_value = offer.get("price")
    currency = offer.get("priceCurrency")
    price = None
    if price_value is not None:
        try:
            price = Decimal(str(price_value))
            if not price.is_finite() or price < 0:
                price = None
        except InvalidOperation:
            price = None
    if price is None:
        for variant in data.get("photosBlock", {}).get("types", []):
            if str(variant.get("productId")) == variant_id:
                try:
                    value = Decimal(str(variant.get("price", {}).get("client")))
                    price = value if value.is_finite() and value >= 0 else None
                except InvalidOperation:
                    pass
                # Currency only comes from explicit site structured data.
                break
    overview = data.get("description", {}).get("overview", {})
    properties = overview.get("main") or []
    original_color = next(
        (
            plain_text(item.get("content"))
            for item in properties
            if plain_text(item.get("title")).casefold() == "цвет товара"
        ),
        "",
    )
    if not original_color:
        original_color = next(
            (
                str(item.get("name") or "")
                for item in data.get("photosBlock", {}).get("types", [])
                if str(item.get("productId")) == variant_id
            ),
            "",
        )
    photos = data.get("photosBlock", {}).get("productPhotos") or []
    images = [item["big"] for item in photos if item.get("big")]
    if not images:
        images = structured.get("image") or []
        if isinstance(images, str):
            images = [images]
    images = list(
        dict.fromkeys(url for url in images if isinstance(url, str) and urlsplit(url).scheme == "https")
    )
    categories = data.get("categories", {}).get("items") or []
    description = structured.get("description") or " ".join(
        plain_text(item.get("text")) for item in overview.get("textBlock", [])
    )
    stock = variant_stock(data)
    return Product(
        id=f"oasis:{variant_id}",
        supplier="oasis",
        source_url=canonical,
        name=plain_text(structured.get("name") or data["name"]),
        category=plain_text(categories[0].get("name")) if categories else None,
        description=plain_text(description) or None,
        original_price=price,
        original_currency=currency,
        colors=[ProductColor(original_color=original_color)] if original_color else [],
        primary_image=images[0] if images else None,
        images=images,
        stock_quantity=stock,
        available=stock is not None and stock > 0,
        metadata={
            "attributes": {plain_text(item.get('title')): plain_text(item.get('content')) for item in properties},
            "supplier_product_id": variant_id,
            "sku": data.get("article"),
            "extraction_method": "json_ld_and_embedded_product_json",
            "stock_basis": "exact_variant_moscow_free_stock_excludes_reserve_transit_fabric",
            "warehouses": data.get("settings", {}).get("productWarehouses", {}),
            "variant_urls": [
                {"url": product_url(v["href"]), "color": v.get("name")}
                for v in data.get("photosBlock", {}).get("types", [])
                if v.get("href") and re.fullmatch(r"/item/[A-Za-z0-9-]+", v["href"])
            ],
        },
    )
