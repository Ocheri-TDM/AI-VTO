"""Capture PostgreSQL plans and timings for the production Composite retrieval shape."""

import asyncio
import json
import os
from pathlib import Path

import asyncpg

POSITIONS = {
    "umbrellas": ("umbrella", "зонты"),
    "pencils": ("pencil", "карандаши"),
    "pens": ("pen", "ручки"),
    "mugs": ("mug", "кружки"),
}
BLUE = ["BLUE", "NAVY", "DARK_BLUE", "LIGHT_BLUE", "ROYAL_BLUE", "BLUE_GREY"]


async def main():
    url = os.environ.get(
        "DATABASE_URL", "postgresql://souvenir:souvenir-test@127.0.0.1:55432/souvenir"
    ).replace("postgresql+asyncpg://", "postgresql://")
    connection = await asyncpg.connect(url)
    output = Path(".local/composite-sql-profile")
    output.mkdir(parents=True, exist_ok=True)
    summary = {}
    color_predicate = " OR ".join(
        f"so.normalized_colors @> '[\"{color}\"]'::jsonb" for color in BLUE
    )
    sql = f"""
      SELECT so.id
      FROM supplier_offers so
      JOIN product_observations po ON po.offer_id=so.id
      JOIN indexed_products ip ON ip.id=so.product_id
      WHERE (ip.category=$1 OR to_tsvector('russian',ip.search_document)
             @@ plainto_tsquery('russian',$2))
        AND so.lifecycle <> 'REMOVED'
        AND ({color_predicate})
    """
    for label, (category, query) in POSITIONS.items():
        plan = await connection.fetch(
            "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql, category, query
        )
        payload = plan[0][0]
        if isinstance(payload, str):
            payload = json.loads(payload)
        value = payload[0]
        (output / f"{label}.json").write_text(
            json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
        summary[label] = {
            "planning_ms": value["Planning Time"],
            "execution_ms": value["Execution Time"],
            "rows": value["Plan"]["Actual Rows"],
        }
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    await connection.close()


if __name__ == "__main__":
    asyncio.run(main())
