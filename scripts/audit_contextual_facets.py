"""Read-only evidence for every reported bad size; no supplier HTTP."""

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "apps/api"), str(ROOT / "scripts")]

from app.config import Settings
from app.database.connection import create_database
from app.domain.product_attributes import ATTRIBUTE_VERSION
from app.domain.product_purpose import PURPOSE_VERSION, identity_inputs, purpose_match
from app.domain.research import ProductBranch
from check_chat_workflow import save
from sqlalchemy import text


async def main():
    engine, sessions = create_database(Settings().database_url)
    output = []
    try:
        async with sessions() as db:
            await db.execute(
                text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            )
            for raw in [
                "900 мл",
                "9–12 лет (134–152 см)",
                "A4",
                "A5",
                "A6",
                "JINGLE BELLS",
                "L V2",
                "L/XL",
                "M V2",
            ]:
                sql = "SELECT id,payload->>'name' name,payload->>'category' category,payload->'metadata'->>'size' raw_size,payload->'metadata'->'attributes' attributes,payload->'metadata'->'variant_attributes' variant_attributes FROM supplier_offers WHERE "
                rows = (
                    (
                        await db.execute(
                            text(
                                sql
                                + "upper(payload->'metadata'->>'size')=upper(:raw) LIMIT 2"
                            ),
                            {"raw": raw},
                        )
                    )
                    .mappings()
                    .all()
                )
                if not rows and raw.startswith("9"):
                    rows = (
                        (
                            await db.execute(
                                text(
                                    sql
                                    + "payload->'metadata'->>'size' LIKE '9%134%152%' LIMIT 2"
                                )
                            )
                        )
                        .mappings()
                        .all()
                    )
                output.append(
                    {
                        "requested_value": raw,
                        "source_field": "SupplierOffer.payload.metadata.size",
                        "source_rows": [dict(r) for r in rows],
                    }
                )
            public = ROOT / "docs/screenshots/contextual-facets"
            before = json.loads((public / "before.json").read_text("utf-8"))["cases"][0]
            rows = (
                await db.execute(
                    text("SELECT id,payload FROM supplier_offers WHERE id=ANY(:ids)"),
                    {"ids": before["offer_ids"]},
                )
            ).all()
            removed = []
            for identifier, payload in rows:
                meta = payload.get("metadata") or {}
                identity = identity_inputs(
                    payload["name"],
                    meta.get("supplier_category"),
                    meta.get("breadcrumbs"),
                    meta.get("attributes"),
                    (payload.get("description") or "")[:2000],
                    payload.get("brand"),
                )
                if not any(
                    purpose_match(identity, ProductBranch(category=c, label=c))[
                        "accepted"
                    ]
                    for c in ["bottle", "mug", "umbrella"]
                ):
                    removed.append(
                        {
                            "offer_id": identifier,
                            "name": payload["name"],
                            "raw_category": payload.get("category"),
                            "supplier_category": meta.get("supplier_category"),
                            "raw_size": meta.get("size"),
                            "identity": identity.public(),
                        }
                    )
            assert len(rows) == len(before["offer_ids"])
            save(
                public / "membership-repair.json",
                {
                    "status": "PASS",
                    "purpose_version": PURPOSE_VERSION,
                    "method": "same saved before offer IDs; local read-only semantic revalidation",
                    "before_offers": len(rows),
                    "after_offers": len(rows) - len(removed),
                    "removed": removed,
                },
            )
            projection = (
                (
                    await db.execute(
                        text(
                            "SELECT count(*) offers,count(h.offer_id) projected,"
                            "count(*) FILTER (WHERE h.version=:version) current_version "
                            "FROM supplier_offers o LEFT JOIN offer_attribute_projections h ON h.offer_id=o.id"
                        ),
                        {"version": ATTRIBUTE_VERSION},
                    )
                )
                .mappings()
                .one()
            )
            save(
                public / "projection.json",
                {
                    "version": ATTRIBUTE_VERSION,
                    **dict(projection),
                    "attributes": [
                        dict(r)
                        for r in (
                            await db.execute(
                                text(
                                    "SELECT attribute_id,count(DISTINCT offer_id) offers,count(DISTINCT value_key) distinct_values "
                                    "FROM offer_attribute_values GROUP BY attribute_id ORDER BY attribute_id"
                                )
                            )
                        ).mappings()
                    ],
                },
            )
        save(ROOT / "docs/screenshots/contextual-facets/raw-value-sources.json", output)
        print("Raw source evidence saved:", len(output), "reported values")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
