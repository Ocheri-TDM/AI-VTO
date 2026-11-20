"""Derived characteristics only; never modifies supplier or commercial data."""

from sqlalchemy import delete

from app.database.models import OfferAttributeProjection, OfferAttributeValue
from app.domain.models import utcnow
from app.domain.product_attributes import ATTRIBUTE_VERSION, attribute_projection


async def project_attributes(db, offer_id, payload, insert):
    digest, values, issues = attribute_projection(payload)
    old = await db.get(OfferAttributeProjection, offer_id)
    if old and old.version == ATTRIBUTE_VERSION and old.source_hash == digest:
        return False
    await db.execute(
        insert(db, OfferAttributeProjection)
        .values(
            offer_id=offer_id,
            version=ATTRIBUTE_VERSION,
            source_hash=digest,
            diagnostics=issues,
            derived_at=utcnow(),
        )
        .on_conflict_do_update(
            index_elements=["offer_id"],
            set_={
                "version": ATTRIBUTE_VERSION,
                "source_hash": digest,
                "diagnostics": issues,
                "derived_at": utcnow(),
            },
        )
    )
    await db.execute(delete(OfferAttributeValue).where(OfferAttributeValue.offer_id == offer_id))
    if values:
        await db.execute(
            insert(db, OfferAttributeValue), [{"offer_id": offer_id, **value} for value in values]
        )
    return True
