"""Rebuild the open concept registry from persisted supplier navigation evidence."""

import asyncio
import hashlib

from sqlalchemy import func, select, update

from app.config import Settings
from app.database.connection import create_database
from app.database.index import IndexRepository
from app.database.models import ProductConcept, ProductConceptOffer, SupplierOffer
from app.domain.models import utcnow
from app.domain.services import normalize_text


async def main():
    settings = Settings()
    engine, sessions = create_database(settings.database_url)
    repository = IndexRepository(sessions, settings)
    concepts = {}
    links = set()
    async with sessions() as db:
        offers = (await db.execute(select(SupplierOffer.id, SupplierOffer.supplier, SupplierOffer.payload))).all()
    for offer_id, supplier, payload in offers:
        metadata = payload.get("metadata", {})
        breadcrumbs = metadata.get("breadcrumbs", [])
        if isinstance(breadcrumbs, str):
            breadcrumbs = [part.strip() for part in breadcrumbs.split(">") if part.strip()]
        labels = [str(metadata.get("supplier_category") or ""), *map(str, breadcrumbs)]
        for label in dict.fromkeys(value for value in labels if value):
            canonical = normalize_text(label)
            concept_id = hashlib.sha256(canonical.encode()).hexdigest()
            concepts[concept_id] = (canonical, label, supplier)
            links.add((concept_id, offer_id))
    async with sessions.begin() as db:
        for concept_id, (canonical, label, supplier) in concepts.items():
            values = dict(
                id=concept_id, canonical_name=canonical, display_name=label, aliases=[label],
                evidence={"supplier": supplier, "source": "persisted_catalog_navigation"},
                products_count=0, last_seen=utcnow(),
            )
            await db.execute(repository.insert(db, ProductConcept).values(**values).on_conflict_do_update(
                index_elements=["id"], set_={"display_name": label, "last_seen": values["last_seen"]},
            ))
        for concept_id, offer_id in links:
            await db.execute(repository.insert(db, ProductConceptOffer).values(
                concept_id=concept_id, offer_id=offer_id,
            ).on_conflict_do_nothing())
        counts = (await db.execute(
            select(ProductConceptOffer.concept_id, func.count())
            .group_by(ProductConceptOffer.concept_id)
        )).all()
        for concept_id, count in counts:
            await db.execute(update(ProductConcept).where(ProductConcept.id == concept_id).values(
                products_count=count
            ))
    print(f"concepts={len(concepts)} links={len(links)} offers={len(offers)}")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
