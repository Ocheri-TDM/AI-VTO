"""Validation boundary for public supplier product links."""

from urllib.parse import urlsplit, urlunsplit

SUPPLIER_HOSTS = {
    "oasis": {"oasiscatalog.com"},
    "gifts": {"gifts.ru"},
    "ucontay": {"ucontay.kz"},
    "portobello": {"portobello.ru"},
    "happygifts": {"happygifts.ru"},
    "artegifts": {"artegifts.by"},
}


def validated_product_url(supplier: str, value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = urlsplit(value.strip())
    except ValueError:
        return None
    host = (parsed.hostname or "").casefold().rstrip(".")
    allowed = SUPPLIER_HOSTS.get(supplier.casefold(), set())
    if parsed.scheme not in {"http", "https"} or not any(
        host == domain or host.endswith("." + domain) for domain in allowed
    ):
        return None
    if parsed.username or parsed.password:
        return None
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", parsed.query, ""))
