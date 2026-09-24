"""Pure normalization of observed InSales data delivered to Chromium."""

import json
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlencode, urljoin, urlsplit

from app.domain.models import Product, ProductColor

BASE_URL = "https://ucontay.kz"


class DocumentData(HTMLParser):
    """Hydration removes the attribute, so parse the browser's document response."""

    def __init__(self, content: str):
        super().__init__(convert_charrefs=True)
        self.products: list[dict[str, Any]] = []
        self.shop: dict[str, Any] = {}
        self.structured: list[dict[str, Any]] = []
        self._in_json = False
        self._json_parts: list[str] = []
        self.feed(content)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if "data-product-json" in attributes:
            value = self._json(attributes["data-product-json"])
            if isinstance(value, dict):
                self.products.append(value)
        if tag == "meta" and attributes.get("name") == "shop-config":
            value = self._json(attributes.get("data-config"))
            if isinstance(value, dict):
                self.shop = value
        if tag == "script" and attributes.get("type") == "application/ld+json":
            self._in_json = True
            self._json_parts = []

    def handle_data(self, data: str) -> None:
        if self._in_json:
            self._json_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._in_json:
            value = self._json("".join(self._json_parts))
            if isinstance(value, dict):
                self.structured.append(value)
            self._in_json = False

    @staticmethod
    def _json(value: str | None) -> Any:
        try:
            return json.loads(value or "")
        except (TypeError, ValueError):
            return None


def numeric(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() and number >= 0 else None


def quantity(value: Any) -> int | None:
    number = numeric(value)
    return int(number) if number is not None and number == number.to_integral_value() else None


def image_url(image: dict[str, Any]) -> str | None:
    raw = image.get("original_url") or image.get("large_url") or image.get("url")
    if not isinstance(raw, str):
        return None
    result = urljoin(BASE_URL, raw)
    return result if urlsplit(result).scheme in {"http", "https"} else None


def normalize_product(
    data: dict[str, Any],
    *,
    currency: str | None,
    category: str | None = None,
    description: str | None = None,
) -> list[Product]:
    """Never add stocks across colors, pending deliveries, or reserved units."""
    result: list[Product] = []
    color_ids = {
        str(option.get("id"))
        for option in data.get("option_names", [])
        if str(option.get("title", "")).casefold() in {"цвет", "color", "colour"}
    }
    images = {str(item.get("id")): item for item in data.get("images", [])}
    family_images = list(dict.fromkeys(url for item in images.values() if (url := image_url(item))))
    properties = {str(item.get("id")): item.get("title") for item in data.get("properties", [])}
    attributes = {
        str(properties.get(str(item.get("property_id")), item.get("property_id"))): item.get("title")
        for item in data.get("characteristics", [])
    }
    for variant in data.get("variants", []):
        variant_id = variant.get("id")
        if variant_id is None or not data.get("title"):
            continue
        colors = [
            ProductColor(original_color=str(option["title"]))
            for option in variant.get("option_values", [])
            if str(option.get("option_name_id")) in color_ids and option.get("title")
        ]
        ordered_ids = [variant.get("image_id"), *variant.get("image_ids", [])]
        variant_images = list(
            dict.fromkeys(
                url for identifier in ordered_ids if (url := image_url(images.get(str(identifier), {})))
            )
        )
        # Never use another color's family photo as the selected variant's primary image.
        # Family originals remain in metadata for a secondary details view.
        selected_images = variant_images
        stock = quantity(variant.get("quantity"))
        warehouse_fields = {
            str(item.get("variant_field_id")): quantity(item.get("value"))
            for item in variant.get("variant_field_values", [])
            if str(item.get("variant_field_id"))
            in {"13312", "13313", "13314", "13315", "13383", "13384", "13385", "13386"}
        }
        path = str(data.get("url") or "")
        parsed = urlsplit(urljoin(BASE_URL, path))
        source_url = f"{BASE_URL}{parsed.path}?{urlencode({'variant_id': variant_id})}"
        result.append(
            Product(
                id=f"ucontay:{variant_id}",
                supplier="ucontay",
                source_url=source_url,
                name=str(data["title"]),
                category=category,
                description=description or data.get("short_description"),
                original_price=numeric(variant.get("price")),
                original_currency=currency,
                colors=colors,
                primary_image=selected_images[0] if selected_images else None,
                images=selected_images,
                stock_quantity=stock,
                available=stock is not None and stock > 0,
                metadata={
                    "product_id": str(data.get("id")),
                    "variant_id": str(variant_id),
                    "sku": variant.get("sku"),
                    "variant_title": variant.get("title"),
                    "stock_source": "variants.quantity (site label: Доступно)",
                    "warehouse_fields": warehouse_fields,
                    "attributes": attributes,
                    "family_images": family_images,
                    "extraction": "browser_document:data-product-json",
                },
            )
        )
    return result
