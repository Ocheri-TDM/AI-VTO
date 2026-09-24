"""Normalize observations from an exact Gifts variant, never related products."""

import json
import re
from decimal import Decimal, InvalidOperation
from urllib.parse import urljoin, urlsplit

from app.domain.models import Product
from app.domain.services import ColorNormalizer

BASE_URL = "https://gifts.ru"


def search_items(html: str) -> list[dict]:
    # JSON literal observed in the supplier document; never execute scraped code.
    match = re.search(r"\.ArticlesData\s*=\s*", html)
    if not match:
        return []
    data, _ = json.JSONDecoder().raw_decode(html[match.end() :])
    return [item for blocks in data.values() for block in blocks for item in block.get("items", [])]


def normalize_product(data: dict, url: str) -> Product:
    price = None
    try:
        if data.get("price") is not None:
            price = Decimal(str(data["price"]))
    except InvalidOperation:
        pass
    if price is not None and (not price.is_finite() or price < 0):
        price = None
    quantities = data.get("quantities", [])
    stock = None
    if len(quantities) == 1 and re.fullmatch(r"\d[\d\s\u00a0]*", quantities[0] or ""):
        stock = int(re.sub(r"\s", "", quantities[0]))
    images = []
    for value in data.get("images", []):
        image = urljoin(BASE_URL, value)
        if urlsplit(image).scheme == "https" and urlsplit(image).hostname == "files.gifts.ru":
            images.append(image)
    images = list(dict.fromkeys(images))
    name = data["name"].strip()
    return Product(
        id="gifts:" + urlsplit(url).path.rstrip("/").split("/")[-1],
        supplier="gifts",
        source_url=url,
        name=name,
        description=data.get("description"),
        original_price=price,
        original_currency=data.get("currency"),
        stock_quantity=stock,
        available=stock is not None and stock > 0,
        colors=[ColorNormalizer().normalize(name)],
        images=images,
        primary_image=images[0] if images else None,
        material=data.get('attributes', {}).get('Материал'),
        capacity=data.get('attributes', {}).get('Объем, мл'),
        dimensions=data.get('attributes', {}).get('Размеры'),
        metadata={"article": data.get("sku"), "stock_source": "variant.free_quantity",
                  'attributes': data.get('attributes', {})},
    )
