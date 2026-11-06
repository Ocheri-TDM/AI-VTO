"""Offer snapshot contracts. Alternatives have independent Decimal line amounts."""

import hashlib
import json
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from uuid import UUID

from bs4 import BeautifulSoup
from pydantic import BaseModel, ConfigDict, Field, field_validator

KIND = "COMMERCIAL_OFFER"
CENT = Decimal("0.01")
MAX_PRICE = Decimal("1000000000.00")
MAX_LINE = Decimal("999999999999.99")  # Excel's numeric precision still preserves kopecks.
MAX_QUANTITY = 1_000_000


def price(value, *, optional=False):
    if value is None and optional:
        return None
    try:
        if not isinstance(value, (str, int, Decimal)) or isinstance(value, bool):
            raise ValueError("Укажите цену текстом с точностью до двух знаков")
        value = Decimal(str(value).replace(",", "."))
        if not value.is_finite() or value <= 0 or value > MAX_PRICE or value != value.quantize(CENT):
            raise ValueError("Цена должна быть положительной, не более 1 млрд ₸, с точностью до 0.01")
        return format(value.quantize(CENT, rounding=ROUND_HALF_UP), ".2f")
    except (InvalidOperation, TypeError) as exc:
        raise ValueError("Некорректная цена") from exc


def line_amount(unit_price, quantity):
    if type(quantity) is not int or not 1 <= quantity <= MAX_QUANTITY:
        raise ValueError("Тираж должен быть целым числом от 1 до 1 000 000")
    if unit_price is None:
        return None
    amount = (Decimal(price(unit_price)) * quantity).quantize(CENT, rounding=ROUND_HALF_UP)
    if amount > MAX_LINE:
        raise ValueError("Стоимость одной позиции превышает предел точного экспорта Excel")
    return format(amount, ".2f")


def draft_amount(unit_price, quantity):
    try:
        return line_amount(unit_price, quantity)
    except ValueError:
        return None


def plain_text(value):
    if value is None:
        return ""
    soup = BeautifulSoup(str(value), "html.parser")
    for tag in soup(["script", "style", "iframe", "object", "svg", "math", "noscript"]):
        tag.decompose()
    for tag in soup.find_all(["p", "li", "br", "div", "h1", "h2", "h3", "tr"]):
        tag.insert_before("\n\n")
    text = soup.get_text(" ", strip=False)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    return "\n\n".join(re.sub(r"[ \t]+", " ", p).strip() for p in re.split(r"\n\s*\n", text) if p.strip())


def public_description(value):
    # Availability blocks are separate evidence, not product characteristics.
    return "\n\n".join(
        p
        for p in plain_text(value).split("\n\n")
        if not re.match(
            r"^(?:(?:Склад|Остаток|Остатки|Резерв|Наличие|Закупочная цена|Цена поставщика)\b[^:—-]{0,100}[:—-]|На\s+складе\b|В\s+наличии\b)",
            p,
            re.I,
        )
    )


def availability(stock, evidence):
    if stock is not None and stock > 0:
        return "В наличии"
    records = evidence.get("availability_records", [])
    if any(
        r.get("state") in ("INCOMING", "PREORDER", "WAITING_SHIPMENT", "RECEIVING")
        and (r.get("free_quantity") is None or r.get("free_quantity", 0) > 0)
        for r in records
    ):
        return "Ожидается поступление"
    return "Нет в наличии" if stock == 0 else "Наличие уточняется"


def characteristics(payload):
    metadata = payload.get("metadata", {})
    values = metadata.get("attributes", {})
    pairs = list(values.items()) if isinstance(values, dict) else []
    actual_color = ", ".join(
        c.get("original_color", "") for c in payload.get("colors", []) if c.get("original_color")
    )
    explicit = {
        "Цвет": actual_color,
        "Размер": metadata.get("size"),
        "Материал": payload.get("material"),
        "Бренд": payload.get("brand"),
    }
    output, seen = [], set()
    for key, raw in pairs + list(explicit.items()):
        key = plain_text(key)
        if re.search(
            r"склад|остат|резерв|stock|availability|поступлен|налич|warehouse|reserved|^город$|цена|стоимост|price|cost",
            key,
            re.I,
        ):
            continue
        normalized = key.casefold()
        canonical = {
            "цвет": "Цвет",
            "color": "Цвет",
            "размер": "Размер",
            "size": "Размер",
            "материал": "Материал",
            "material": "Материал",
            "бренд": "Бренд",
            "brand": "Бренд",
        }.get(normalized)
        if canonical and explicit.get(canonical):
            key, raw = canonical, explicit[canonical]
        elif canonical == "Размер" and isinstance(raw, str) and "," in raw:
            continue  # Parent options do not establish the selected variant's size.
        value = plain_text(", ".join(map(str, raw)) if isinstance(raw, (list, tuple)) else raw)
        if key and value and (key.casefold(), value) not in seen:
            output.append({"name": key, "value": value})
            seen.add((key.casefold(), value))
    return output


def snapshot_hash(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


class OfferItemEdit(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str
    title: str = Field(min_length=1, max_length=500)
    sku: str = Field(default="", max_length=200)
    sku_note: str = Field(default="", max_length=500)
    unit_price: str | None = None
    quantity: int = Field(ge=1, le=MAX_QUANTITY)
    main_image: str | None = None

    @field_validator("unit_price")
    @classmethod
    def validate_price(cls, value):
        return price(value, optional=True)


class OfferEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=200)
    company: str = Field(default="", max_length=200)
    items: list[OfferItemEdit] = Field(min_length=1, max_length=200)
    use_logo: bool = True
    use_banner: bool = True


class OfferCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    collection_version: int = Field(ge=0)


class OfferAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)
    request_id: UUID
    share_epoch: int = Field(default=0, ge=0)


class ShareAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)
    epoch: int = Field(ge=0)


def validate_document(draft):
    issues = []
    for item in draft["items"]:
        if not item["title"].strip():
            issues.append(
                {"item_id": item["id"], "code": "TITLE_REQUIRED", "message": "Укажите название позиции"}
            )
        if not item["sku"].strip():
            issues.append(
                {
                    "item_id": item["id"],
                    "code": "SKU_REQUIRED",
                    "message": "Укажите достоверный артикул или исключите позицию",
                }
            )
        if item["unit_price"] is None:
            issues.append(
                {
                    "item_id": item["id"],
                    "code": "PRICE_REQUIRED",
                    "message": "Заполните цену перед публикацией",
                }
            )
        try:
            line_amount(item["unit_price"], item["quantity"])
        except ValueError as exc:
            issues.append({"item_id": item["id"], "code": "INVALID_LINE", "message": str(exc)})
    return issues


def client_snapshot(draft):
    """Positive allowlist: no purchase price, provenance, stock count, offer/user IDs."""
    return {
        "schema_version": 1,
        "title": draft["settings"]["title"],
        "company": draft["settings"].get("client", ""),
        "logo_background": draft.get("resolved_logo", {}).get("background", "#151918"),
        "items": [
            {
                "id": i["id"],
                "title": i["title"],
                "sku": i["sku"],
                "brand": i.get("brand"),
                "category": i["category_label"],
                "description": i["description"],
                "characteristics": i["characteristics"],
                "unit_price": i["unit_price"],
                "quantity": i["quantity"],
                "line_amount": draft_amount(i["unit_price"], i["quantity"]),
                "availability": i["availability"],
                "image_ids": [p["id"] for p in i["photos"]],
                "main_image": i.get("main_image"),
            }
            for i in draft["items"]
        ],
    }
