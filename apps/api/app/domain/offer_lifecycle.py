"""Seven actual UTC days; no read or content update extends a capability."""

from datetime import UTC, timedelta

LINK_LIFETIME = timedelta(days=7)


def aware(value):
    return value.replace(tzinfo=UTC) if value is not None and value.tzinfo is None else value


def expired(share, now):
    return bool(share and share.expires_at and aware(now) >= aware(share.expires_at))


def live(share, now):
    return bool(
        share and share.active and share.activated_at and share.expires_at and not expired(share, now)
    )


def activate(share, now):
    """Call only after a ready revision. Existing periods never move."""
    if share.activated_at is None:
        share.activated_at = aware(now)
        share.expires_at = aware(now) + LINK_LIFETIME
        share.activation_source = "READY_ACTIVATION"
    if not expired(share, now):
        share.active = True


def primary_rank(document, published=False):
    items = document["draft"].get("items", [])
    manual = sum(
        any(i.get(k) == "MANUAL" for k in ("price_source", "quantity_source", "sku_source")) for i in items
    )
    return (
        bool(items),
        bool(manual),
        manual,
        document["version"] > 1,
        published,
        document["version"],
        document["updated_at"],
        document["id"],
    )
