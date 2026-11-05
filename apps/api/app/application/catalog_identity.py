"""Shared SQL brand predicate for initial intent, chat patches and HUD."""

from sqlalchemy import false, func, or_, select

from app.database.models import SupplierOffer
from app.domain.catalog_identity import BrandRegistry, brand_key


async def brand_registry(sessions):
    async with sessions() as db:
        return BrandRegistry(
            await db.scalars(
                select(SupplierOffer.payload["brand"].as_string())
                .where(SupplierOffer.lifecycle != "REMOVED")
                .distinct()
            )
        )


async def identity_catalog(sessions):
    async with sessions() as db:
        return (
            await db.execute(
                select(
                    SupplierOffer.payload["metadata"]["parent_name"].as_string(),
                    SupplierOffer.payload["name"].as_string(),
                    SupplierOffer.payload["brand"].as_string(),
                )
                .where(SupplierOffer.lifecycle != "REMOVED")
                .distinct()
            )
        ).all()


def brand_predicate(registry, values, column=None):
    column = column if column is not None else SupplierOffer.payload["brand"].as_string()
    column = func.coalesce(column, "")
    raw = [label for value in values for label in registry.raw.get(brand_key(value), []) if label is not None]
    conditions = [column.in_(raw)] if raw else []
    if "" in [brand_key(v) for v in values] and None in registry.raw.get("", []):
        conditions.append(column == "")
    return or_(*conditions) if conditions else false()
