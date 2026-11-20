"""Bounded, idempotent local projection from saved VTO data. No supplier HTTP."""

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))

from app.config import Settings
from app.database.connection import create_database
from app.database.index import IndexRepository
from app.database.models import OfferAttributeProjection, SupplierOffer
from app.database.product_attributes import project_attributes
from app.domain.product_attributes import ATTRIBUTE_VERSION
from sqlalchemy import func, or_, select


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-size", type=int, default=250)
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--verify-all",
        action="store_true",
        help="Also verify hashes of already projected rows",
    )
    args = parser.parse_args()
    assert 1 <= args.batch_size <= 1000
    engine, sessions = create_database(Settings().database_url)
    index = IndexRepository(sessions, Settings())
    cursor, seen, changed = "", 0, 0
    try:
        while args.limit is None or seen < args.limit:
            async with sessions.begin() as db:
                count = (
                    min(args.batch_size, args.limit - seen)
                    if args.limit
                    else args.batch_size
                )
                stmt = select(SupplierOffer.id).where(
                    SupplierOffer.supplier == "vto", SupplierOffer.id > cursor
                )
                if not args.verify_all:
                    stmt = stmt.outerjoin(
                        OfferAttributeProjection,
                        OfferAttributeProjection.offer_id == SupplierOffer.id,
                    ).where(
                        or_(
                            OfferAttributeProjection.offer_id.is_(None),
                            OfferAttributeProjection.version != ATTRIBUTE_VERSION,
                        )
                    )
                ids = list(
                    await db.scalars(stmt.order_by(SupplierOffer.id).limit(count))
                )
                if not ids:
                    break
                if db.bind.dialect.name == "postgresql":
                    for identifier in ids:
                        lock = int.from_bytes(
                            hashlib.sha256(identifier.encode()).digest()[:8],
                            "big",
                            signed=True,
                        )
                        await db.execute(select(func.pg_advisory_xact_lock(lock)))
                rows = (
                    await db.execute(
                        select(SupplierOffer.id, SupplierOffer.payload)
                        .where(SupplierOffer.id.in_(ids))
                        .order_by(SupplierOffer.id)
                    )
                ).all()
                for identifier, payload in rows:
                    changed += await project_attributes(
                        db, identifier, payload, index.insert
                    )
                cursor = ids[-1]
                seen += len(ids)
            print(
                json.dumps(
                    {
                        "processed": seen,
                        "projections_changed": changed,
                        "cursor": cursor,
                    }
                ),
                flush=True,
            )
            await asyncio.sleep(0.02)
    finally:
        await engine.dispose()
    print(
        json.dumps(
            {"status": "COMPLETED", "processed": seen, "projections_changed": changed}
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
