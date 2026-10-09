"""Read-only trace of normalized availability used by composite requests."""

import asyncio
import json
import os
import sys
from datetime import UTC, datetime

import asyncpg

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "apps", "api"))
from app.application.availability import ProcurementAvailabilityService
from app.domain.models import AvailabilityRecord


async def main():
    url = os.environ.get(
        "DATABASE_URL", "postgresql://souvenir:souvenir-test@127.0.0.1:55432/souvenir"
    )
    url = url.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(url)
    rows = await conn.fetch("""
      SELECT ip.id indexed_product_id, so.id supplier_offer_id, so.supplier,
             ip.category, ip.name title, so.source_url, so.payload->'colors' colors,
             po.status observation_status, po.observed_at product_observed_at,
             po.expires_at, ao.state, ao.location_code, ao.location_label,
             ao.total_quantity, ao.free_quantity, ao.reserved_quantity,
             ao.expected_at, ao.observed_at, ao.parser_version, ao.confidence
      FROM availability_observations ao
      JOIN supplier_offers so ON so.id=ao.offer_id
      JOIN indexed_products ip ON ip.id=so.product_id
      LEFT JOIN product_observations po ON po.offer_id=so.id
      WHERE ao.free_quantity IS NOT NULL
      ORDER BY ao.free_quantity DESC NULLS LAST, ao.observed_at DESC
      LIMIT 40
    """)
    now = datetime.now(UTC)
    values = []
    for row in rows:
        item = dict(row)
        for key, value in list(item.items()):
            if hasattr(value, "isoformat"):
                item[key] = value.isoformat()
        expires = row["expires_at"]
        if expires and expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        item["fresh"] = bool(
            row["observation_status"] == "FRESH" and expires and expires > now
        )
        item["reject_reason"] = None if item["fresh"] else "STALE_OBSERVATION"
        values.append(item)
    counts = await conn.fetch("""
      SELECT so.supplier, count(DISTINCT so.id) offers,
             count(DISTINCT ao.offer_id) offers_with_availability,
             round(100.0*count(DISTINCT ao.offer_id)/nullif(count(DISTINCT so.id),0),2) coverage_percent,
             count(DISTINCT ao.offer_id) FILTER (WHERE po.status='FRESH' AND po.expires_at>now()) fresh,
             count(DISTINCT ao.offer_id) FILTER (WHERE NOT (po.status='FRESH' AND po.expires_at>now())) stale,
             count(DISTINCT ao.offer_id) FILTER (WHERE ao.free_quantity IS NOT NULL) known_free,
             count(DISTINCT ao.offer_id) FILTER (WHERE ao.free_quantity IS NULL) unknown_free,
             count(DISTINCT ao.offer_id) FILTER (WHERE ao.free_quantity > 0) positive_free,
             string_agg(DISTINCT ao.parser_version, ', ' ORDER BY ao.parser_version) parser_versions,
             max(ao.observed_at) latest_availability, max(po.expires_at) latest_expiry
      FROM supplier_offers so LEFT JOIN availability_observations ao ON ao.offer_id=so.id
      LEFT JOIN product_observations po ON po.offer_id=so.id
      GROUP BY so.supplier ORDER BY so.supplier
    """)
    blue = await conn.fetch("""
      SELECT ip.id indexed_product_id, so.id supplier_offer_id, so.product_id, so.supplier,
             ip.category, ip.name title, so.source_url, so.payload->'colors' colors,
             ao.id observation_id, ao.state, ao.free_quantity, ao.total_quantity,
             ao.reserved_quantity, ao.observed_at, ao.parser_version, po.expires_at
      FROM supplier_offers so JOIN indexed_products ip ON ip.id=so.product_id
      JOIN product_observations po ON po.offer_id=so.id
      JOIN availability_observations ao ON ao.offer_id=so.id AND ao.state='ON_HAND'
      WHERE po.status='FRESH' AND po.expires_at>now() AND ao.free_quantity>=20
        AND EXISTS (SELECT 1 FROM jsonb_array_elements(so.payload->'colors') c
                    WHERE c->>'normalized_color' IN ('BLUE','NAVY','DARK_BLUE','LIGHT_BLUE','ROYAL_BLUE','BLUE_GREY'))
      ORDER BY ao.free_quantity DESC LIMIT 40
    """)
    families = await conn.fetch("""
      SELECT so.product_id, count(*) offers,
             array_agg(so.id ORDER BY so.id) offer_ids,
             array_agg(so.payload->'colors' ORDER BY so.id) colors
      FROM supplier_offers so
      WHERE jsonb_array_length(coalesce(so.payload->'colors','[]'::jsonb))>0
      GROUP BY so.product_id HAVING count(*)>1
      ORDER BY count(*) DESC LIMIT 10
    """)
    rejects = await conn.fetch("""
      WITH candidates AS (
        SELECT ip.id indexed_product_id,so.id supplier_offer_id,so.supplier,ip.category,ip.name,
               so.source_url,so.payload->'colors' colors,ao.id observation_id,ao.state,
               ao.free_quantity,ao.total_quantity,ao.reserved_quantity,ao.observed_at,
               ao.parser_version,po.status,po.expires_at,
               CASE WHEN ao.id IS NULL THEN 'NO_AVAILABILITY_RECORD'
                    WHEN NOT (po.status='FRESH' AND po.expires_at>now()) THEN 'STALE_OBSERVATION'
                    WHEN ao.free_quantity IS NULL THEN 'UNKNOWN_FREE_QUANTITY'
                    WHEN ao.state='ON_HAND' AND ao.free_quantity<20 THEN 'FREE_QUANTITY_INSUFFICIENT'
               END reason
        FROM supplier_offers so JOIN indexed_products ip ON ip.id=so.product_id
        LEFT JOIN product_observations po ON po.offer_id=so.id
        LEFT JOIN availability_observations ao ON ao.offer_id=so.id AND ao.state='ON_HAND'
        WHERE ip.search_document ~* '(зонт|карандаш|ручк|круж|чаш)'
          AND EXISTS (SELECT 1 FROM jsonb_array_elements(so.payload->'colors') c
                      WHERE c->>'normalized_color' IN ('BLUE','NAVY','DARK_BLUE','LIGHT_BLUE','ROYAL_BLUE','BLUE_GREY'))
      ), ranked AS (
        SELECT *,row_number() OVER(PARTITION BY reason ORDER BY supplier,name) n FROM candidates WHERE reason IS NOT NULL
      ) SELECT * FROM ranked WHERE n<=10 ORDER BY reason,n
    """)
    variant_rows = await conn.fetch("""
      WITH families AS (
        SELECT product_id FROM supplier_offers GROUP BY product_id HAVING count(*)>1 LIMIT 5
      )
      SELECT so.product_id,so.id supplier_offer_id,so.supplier,so.source_url,
             so.payload->'colors' colors,ao.state,ao.free_quantity,ao.observed_at,po.expires_at
      FROM supplier_offers so JOIN families f ON f.product_id=so.product_id
      LEFT JOIN availability_observations ao ON ao.offer_id=so.id AND ao.state='ON_HAND'
      LEFT JOIN product_observations po ON po.offer_id=so.id
      ORDER BY so.product_id,so.id
    """)
    evaluated = []
    for row in blue:
        record = AvailabilityRecord(
            state=row["state"],
            free_quantity=row["free_quantity"],
            total_quantity=row["total_quantity"],
            reserved_quantity=row["reserved_quantity"],
            observed_at=row["observed_at"],
            parser_version=row["parser_version"],
            source_label="database forensic audit",
        )
        evaluation = ProcurementAvailabilityService().evaluate([record], 20)
        evaluated.append(
            {
                "supplier_offer_id": row["supplier_offer_id"],
                "result": evaluation.result,
                "reason_code": evaluation.reason_code,
                "available_quantity": evaluation.available_quantity,
            }
        )
    result = {
        "generated_at": now.isoformat(),
        "supplier_counts": [dict(x) for x in counts],
        "offers": values,
        "fresh_blue_free_gte_20": [dict(x) for x in blue],
        "procurement_evaluations": evaluated,
        "multi_offer_families": [dict(x) for x in families],
        "rejected_samples": [dict(x) for x in rejects],
        "variant_family_rows": [dict(x) for x in variant_rows],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
