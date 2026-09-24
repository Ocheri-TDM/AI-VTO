"""Stage observations before publishing; reconciliation requires complete evidence."""
from sqlalchemy import select

from app.database.models import CatalogRun, CatalogSeen, ProductObservation, SupplierOffer
from app.domain.models import Product, utcnow


class CatalogSafety:
    def __init__(self, index):
        self.index = index

    async def begin(self, job):
        if job['checkpoint'].get('run_id'):
            return
        async with self.index.sessions() as db, db.begin():
            run = CatalogRun(supplier=job['supplier'], kind=job['kind'])
            db.add(run)
            await db.flush()
            job['checkpoint']['run_id'] = run.id
        await self.index.checkpoint(job['id'], job['checkpoint'], job['metrics'])

    async def stage(self, job, products):
        async with self.index.sessions() as db, db.begin():
            for product in products:
                values = dict(run_id=job['checkpoint']['run_id'], offer_id=product.id,
                              payload=product.model_dump(mode='json'))
                stmt = self.index.insert(db, CatalogSeen).values(**values)
                await db.execute(stmt.on_conflict_do_update(
                    index_elements=['run_id', 'offer_id'], set_={'payload': values['payload']}))

    async def finish(self, job, complete):
        run_id = job['checkpoint']['run_id']
        async with self.index.sessions() as db:
            run = await db.get(CatalogRun, run_id)
            if run.status in ('COMPLETE', 'QUARANTINED'):
                return run.status != 'QUARANTINED'
            rows = (await db.scalars(select(CatalogSeen).where(CatalogSeen.run_id == run_id))).all()
            old = (await db.execute(select(SupplierOffer.id, ProductObservation.payload)
                .join(ProductObservation, ProductObservation.offer_id == SupplierOffer.id)
                .where(SupplierOffer.supplier == job['supplier']))).all()
        products = [Product.model_validate(r.payload) for r in rows]
        previous = dict(old)
        anomalies = set()
        compared, collapsed = 0, 0
        for p in products:
            before = previous.get(p.id)
            if not before:
                continue
            if before.get('original_currency') and p.original_currency != before['original_currency']:
                anomalies.add('CURRENCY_CHANGE')
            if float(before.get('original_price') or 0) > 0 and (p.original_price is None or p.original_price <= 0):
                anomalies.add('PRICE_COLLAPSE')
            if (before.get('stock_quantity') or 0) > 0:
                compared += 1
                collapsed += p.stock_quantity == 0
                if p.stock_quantity is None:
                    anomalies.add('PARSE_FAILURE')
        if compared >= 10 and collapsed / compared >= .8:
            anomalies.add('STOCK_COLLAPSE')
        if complete and job['kind'] in ('CATALOG_DISCOVERY', 'FULL_CATALOG_RECONCILIATION') and len(previous) >= 20 and len(products) < .5 * len(previous):
            anomalies.add('MASS_REMOVAL')
        if not complete:
            # A catalog can take many execution slices. Publish trustworthy observed
            # changes without treating this slice as reconciliation evidence. Hold
            # zero-stock transitions until aggregate run validation is possible.
            if not anomalies:
                safe = [p for p in products if not (
                    (previous.get(p.id, {}).get('stock_quantity') or 0) > 0 and p.stock_quantity == 0)]
                summary = await self.index.ingest(safe)
                for key, value in summary.items():
                    job['metrics'][key] = job['metrics'].get(key, 0) + value
            job['metrics']['anomalies'] = sorted(anomalies)
            return not anomalies
        if not anomalies:
            summary = await self.index.ingest(products)
            for key, value in summary.items():
                job['metrics'][key] = job['metrics'].get(key, 0) + value
        async with self.index.sessions() as db, db.begin():
            run = await db.get(CatalogRun, run_id)
            run.status = 'QUARANTINED' if anomalies else 'COMPLETE'
            run.anomalies, run.completed_at = sorted(anomalies), utcnow()
            if not anomalies and job['kind'] in ('CATALOG_DISCOVERY', 'FULL_CATALOG_RECONCILIATION'):
                seen = {p.id for p in products}
                offers = (await db.scalars(select(SupplierOffer).where(SupplierOffer.supplier == job['supplier']))).all()
                for offer in offers:
                    if offer.id in seen:
                        offer.missing_runs, offer.lifecycle = 0, 'ACTIVE'
                    else:
                        offer.missing_runs += 1
                        offer.lifecycle = ('NOT_SEEN' if offer.missing_runs == 1 else
                                           'SUSPECTED_REMOVED' if offer.missing_runs == 2 else 'REMOVED')
        job['metrics']['anomalies'] = sorted(anomalies)
        return not anomalies
