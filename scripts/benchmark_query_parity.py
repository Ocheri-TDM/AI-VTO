"""Compare canonical and accepted typo queries through the full index retrieval path."""

import asyncio
import json
from pathlib import Path

from app.application.index_query import IndexQueryService
from app.application.research_planner import ResearchPlanner
from app.config import Settings
from app.database.connection import create_database
from app.database.index import IndexRepository
from app.domain.query_understanding import QueryUnderstandingService

PAIRS = [
    ("карандаш", "корондаш"),
    ("карандаш", "карондаш"),
    ("карандаш", "крандаш"),
    ("ручка", "ручька"),
    ("кружка", "крушка"),
    ("рюкзак", "рюкзаг"),
    ("футболка", "фудболка"),
    ("дождевик", "даждевик"),
    ("зонт", "зонтоф"),
    ("носки", "наскофф"),
]


async def retrieve(query, understanding, planner, engine):
    parsed = await understanding.understand(query)
    intent = await planner.parse(parsed.search_query)
    intent.original_query = parsed.original_query
    intent.normalized_query = parsed.normalized_query
    intent.raw_query = parsed.original_query
    intent.query_understanding = parsed.model_dump(mode="json")
    products, timings = await engine.search(intent)
    return parsed, intent, products, timings


async def main():
    settings = Settings()
    database, sessions = create_database(settings.database_url)
    understanding = QueryUnderstandingService(sessions)
    planner = ResearchPlanner()
    engine = IndexQueryService(IndexRepository(sessions, settings))
    rows = []
    for canonical_query, typo_query in PAIRS:
        _canonical, canonical_intent, canonical_products, canonical_timings = await retrieve(
            canonical_query, understanding, planner, engine
        )
        typo, typo_intent, typo_products, typo_timings = await retrieve(
            typo_query, understanding, planner, engine
        )
        canonical_ids = {product.id for product in canonical_products}
        typo_ids = {product.id for product in typo_products}
        union = canonical_ids | typo_ids
        accepted = bool(typo.corrections) and typo.confidence >= 0.9
        rows.append(
            {
                "canonical_query": canonical_query,
                "typo_query": typo_query,
                "normalized_query": typo.normalized_query,
                "accepted": accepted,
                "confidence": typo.confidence,
                "canonical_categories": canonical_intent.categories,
                "typo_categories": typo_intent.categories,
                "canonical_count": len(canonical_ids),
                "typo_count": len(typo_ids),
                "count_difference": len(typo_ids) - len(canonical_ids),
                "exact_pool_parity": canonical_ids == typo_ids,
                "jaccard": round(len(canonical_ids & typo_ids) / max(1, len(union)), 6),
                "false_zero": bool(canonical_ids and not typo_ids),
                "false_narrowing": accepted and not canonical_ids.issubset(typo_ids),
                "canonical_timings": canonical_timings,
                "typo_timings": typo_timings,
            }
        )
    accepted_rows = [row for row in rows if row["accepted"]]
    report = {
        "database": "current Research Index",
        "pairs": len(rows),
        "accepted_pairs": len(accepted_rows),
        "exact_pool_parity_rate": round(
            sum(row["exact_pool_parity"] for row in accepted_rows) / max(1, len(accepted_rows)), 6
        ),
        "mean_jaccard": round(
            sum(row["jaccard"] for row in accepted_rows) / max(1, len(accepted_rows)), 6
        ),
        "false_zero_rate": round(
            sum(row["false_zero"] for row in accepted_rows) / max(1, len(accepted_rows)), 6
        ),
        "false_narrowing_rate": round(
            sum(row["false_narrowing"] for row in accepted_rows) / max(1, len(accepted_rows)), 6
        ),
        "rows": rows,
    }
    Path(".local/query-retrieval-parity.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    await database.dispose()


asyncio.run(main())
