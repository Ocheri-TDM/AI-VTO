"""Catalog coverage and commercial freshness are deliberately separate metrics."""
from sqlalchemy import select

from app.database.models import (
    AvailabilityObservation,
    CatalogRun,
    CatalogSyncJob,
    IndexedProduct,
    ProductObservation,
    SupplierOffer,
    SupplierSyncState,
)
from app.domain.models import utcnow


class IndexQualityService:
    def __init__(self, index, suppliers):
        self.index, self.suppliers = index, suppliers

    async def report(self):
        result = []
        async with self.index.sessions() as db:
            for supplier in self.suppliers:
                rows = (await db.execute(select(SupplierOffer, ProductObservation, IndexedProduct.category)
                    .outerjoin(ProductObservation, ProductObservation.offer_id == SupplierOffer.id)
                    .join(IndexedProduct, IndexedProduct.id == SupplierOffer.product_id)
                    .where(SupplierOffer.supplier == supplier))).all()
                availability = (
                    await db.scalars(
                        select(AvailabilityObservation)
                        .join(SupplierOffer, SupplierOffer.id == AvailabilityObservation.offer_id)
                        .where(SupplierOffer.supplier == supplier)
                    )
                ).all()
                jobs = (await db.scalars(select(CatalogSyncJob).where(CatalogSyncJob.supplier == supplier)
                    .order_by(CatalogSyncJob.updated_at.desc()).limit(20))).all()
                runs = (await db.scalars(select(CatalogRun).where(CatalogRun.supplier == supplier)
                    .order_by(CatalogRun.started_at.desc()).limit(20))).all()
                state = await db.get(SupplierSyncState, supplier)
                discovery = next((j for j in jobs if j.kind == 'CATALOG_DISCOVERY'), None)
                branches = discovery.checkpoint.get('branches', []) if discovery else []
                fresh = sum(bool(o and o.status == 'FRESH' and o.expires_at.replace(tzinfo=utcnow().tzinfo) > utcnow()) for _, o, _ in rows)
                fresh_offer_ids = {
                    offer.id
                    for offer, observation, _ in rows
                    if observation
                    and observation.status == "FRESH"
                    and observation.expires_at.replace(tzinfo=utcnow().tzinfo) > utcnow()
                }
                complete = next((r for r in runs if r.kind == 'CATALOG_DISCOVERY' and r.status == 'COMPLETE'), None)
                refresh = next((r for r in runs if r.kind in (
                    'OBSERVATION_REFRESH', 'AVAILABILITY_REFRESH', 'TARGETED_REFRESH'
                ) and r.status == 'COMPLETE'), None)
                incremental = next((r for r in runs if r.kind == 'INCREMENTAL_CATALOG_SYNC' and r.status == 'COMPLETE'), None)
                reconciliation = next((r for r in runs if r.kind in ('FULL_CATALOG_RECONCILIATION', 'CATALOG_DISCOVERY') and r.status == 'COMPLETE'), None)
                _due, usage_counts = await self.index.adaptive_refresh_candidates(supplier)
                current_job = next((j for j in jobs if j.status in ('RUNNING', 'PENDING')), None)
                labels = sorted({str(p.payload.get('metadata', {}).get('supplier_category', '')) for p, _, c in rows if not c})
                provider = self.suppliers.get(supplier) if isinstance(self.suppliers, dict) else None
                result.append(dict(supplier=supplier,
                    capabilities=provider.capabilities.model_dump() if provider and hasattr(provider, 'capabilities') else {},
                    offers_total=len(rows),
                    offers_active=sum(p.lifecycle == 'ACTIVE' for p, _, _ in rows), fresh=fresh, stale=len(rows)-fresh,
                    without_price=sum(o is None or o.price_kzt is None for _, o, _ in rows),
                    without_stock=sum(o is None or o.stock is None for _, o, _ in rows),
                    availability_observations=len(availability),
                    availability_fresh=sum(a.offer_id in fresh_offer_ids for a in availability),
                    availability_stale=sum(a.offer_id not in fresh_offer_ids for a in availability),
                    availability_current_known=sum(
                        a.state == "ON_HAND" and a.free_quantity is not None for a in availability
                    ),
                    availability_current_unknown=sum(
                        a.state == "ON_HAND" and a.free_quantity is None for a in availability
                    ),
                    availability_incoming_known=sum(
                        a.state in {"INCOMING", "RECEIVING"} and a.free_quantity is not None
                        for a in availability
                    ),
                    availability_remote_known=sum(
                        a.state == "REMOTE_STOCK" and a.free_quantity is not None
                        for a in availability
                    ),
                    availability_preorder_known=sum(
                        a.state in {"PREORDER", "ORDER_ON_DEMAND", "PRODUCTION", "FACTORY"}
                        for a in availability
                    ),
                    availability_parser_versions=sorted({a.parser_version for a in availability}),
                    without_category=sum(not c for _, _, c in rows),
                    without_image=sum(not p.payload.get('primary_image') for p, _, _ in rows),
                    unmapped_categories=[x for x in labels if x],
                    failed_branches=sum(bool(b.get('error_code')) for b in branches),
                    pending_branches=sum(b.get('status') != 'COMPLETE' for b in branches),
                    last_discovery=discovery.updated_at.isoformat() if discovery else None,
                    last_complete_discovery=complete.completed_at.isoformat() if complete else None,
                    last_refresh=refresh.completed_at.isoformat() if refresh else None,
                    last_incremental_sync=incremental.completed_at.isoformat() if incremental else None,
                    last_full_reconciliation=reconciliation.completed_at.isoformat() if reconciliation else None,
                    next_incremental=(state.next_incremental_at.isoformat() if state and state.next_incremental_at else None),
                    next_full_reconciliation=(state.next_reconciliation_at.isoformat() if state and state.next_reconciliation_at else None),
                    circuit_open_until=(state.circuit_open_until.isoformat() if state and state.circuit_open_until else None),
                    offer_temperature=usage_counts,
                    availability_refreshing=sum(bool(o and o.status == 'REFRESHING') for _, o, _ in rows),
                    current_job=(dict(kind=current_job.kind, status=current_job.status,
                                      checkpoint=current_job.checkpoint, metrics=current_job.metrics,
                                      error=current_job.error) if current_job else None),
                    products_per_minute=((current_job.metrics or {}).get('products_per_minute') if current_job else None),
                    requests_per_minute=((current_job.metrics or {}).get('requests_per_minute') if current_job else None),
                    useful_change_rate=((current_job.metrics or {}).get('useful_change_rate') if current_job else None),
                    http_403=sum((j.metrics or {}).get('http_403', 0) for j in jobs),
                    http_429=sum((j.metrics or {}).get('http_429', 0) for j in jobs),
                    discovery_status=('COMPLETE' if discovery and discovery.status == 'COMPLETED' and complete else
                                      'PARTIAL' if discovery and discovery.error in ('TIME_BUDGET','COVERAGE_LIMITED') else
                                      'FAILED' if discovery and discovery.error else 'PARTIAL'),
                    roots=discovery.checkpoint.get('roots', []) if discovery else [],
                    branches=[{k: b.get(k) for k in ('route', 'status', 'pages_scanned', 'products_seen', 'total_results_reported', 'error_code')} for b in branches],
                    jobs=[dict(id=j.id, kind=j.kind, status=j.status, error=j.error, metrics=j.metrics) for j in jobs],
                    anomalies=[dict(run_id=r.id, codes=r.anomalies) for r in runs if r.anomalies]))
        return {'suppliers': result}
