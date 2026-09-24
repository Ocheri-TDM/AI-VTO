"""Reproducible PostgreSQL trace for the keychain regression case."""
import asyncio
import json
import sys
import time
from collections import Counter
from pathlib import Path

from sqlalchemy import func, select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.application.index_query import IndexQueryService
from app.application.research_analysis import ProductGroupingService
from app.application.research_planner import ResearchPlanner
from app.config import Settings
from app.database.connection import create_database
from app.database.index import IndexRepository
from app.database.models import CatalogSyncJob, IndexedProduct, ProductObservation, SupplierOffer, SupplierSyncState


async def main():
    settings = Settings()
    engine, sessions = create_database(settings.database_url)
    index = IndexRepository(sessions, settings)
    intent = await ResearchPlanner().parse("брелки")
    report = {
        "query": "брелки",
        "normalized_query": "брелок",
        "resolved_categories": intent.categories,
        "quantity": intent.quantity,
        "sql_semantics": "taxonomy OR russian FTS; lifecycle != REMOVED; no freshness constraint without quantity",
        "terms": {},
    }
    async with sessions() as db:
        for term in ("брелок", "брелки", "брелока", "брелок-", "keychain"):
            product_ids = select(IndexedProduct.id).where(IndexedProduct.search_document.ilike(f"%{term}%"))
            offers = select(SupplierOffer.id).where(SupplierOffer.product_id.in_(product_ids))
            report["terms"][term] = {
                "indexed_products": await db.scalar(select(func.count()).select_from(product_ids.subquery())),
                "supplier_offers": await db.scalar(select(func.count()).select_from(offers.subquery())),
                "observations": await db.scalar(select(func.count()).select_from(ProductObservation).where(ProductObservation.offer_id.in_(offers))),
            }
        supplier_rows = (await db.execute(
            select(SupplierOffer.supplier, func.count(SupplierOffer.id),
                   func.count(func.distinct(SupplierOffer.product_id)))
            .group_by(SupplierOffer.supplier).order_by(SupplierOffer.supplier)
        )).all()
        report["catalog_by_supplier"] = {
            supplier: {"offers": offers_count, "products": products_count}
            for supplier, offers_count, products_count in supplier_rows
        }
        states = (await db.scalars(select(SupplierSyncState).order_by(SupplierSyncState.supplier))).all()
        report["sync_states"] = {
            state.supplier: {
                "next_discovery_at": state.next_discovery_at.isoformat(),
                "error": state.error,
            }
            for state in states
        }
        jobs = (await db.scalars(
            select(CatalogSyncJob).where(CatalogSyncJob.kind == "CATALOG_DISCOVERY")
            .order_by(CatalogSyncJob.supplier)
        )).all()
        report["discovery_jobs"] = {
            job.supplier: {"status": job.status, "error": job.error,
                           "updated_at": job.updated_at.isoformat()}
            for job in jobs
        }
        report["database_size_bytes"] = await db.scalar(select(func.pg_database_size(func.current_database())))
    candidates = await index.candidates(intent)
    started = time.perf_counter()
    products, metrics = await IndexQueryService(index).search(intent)
    latency = round((time.perf_counter() - started) * 1000, 2)
    groups = ProductGroupingService().groups(products)
    report.update({
        "matching_pool": len(products),
        "sql_candidates": len(candidates),
        "matcher_rejected": [p.id for p in candidates if p.id not in {v.id for v in products}],
        "canonical_groups": len(groups),
        "visible_first_page": min(50, len(products)),
        "by_supplier": dict(sorted(Counter(p.supplier for p in products).items())),
        "latency_ms": latency,
        "browser_invoked": metrics.get("browser_invoked"),
    })
    print(json.dumps(report, ensure_ascii=False, indent=2))
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
