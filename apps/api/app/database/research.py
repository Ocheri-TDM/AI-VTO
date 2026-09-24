from sqlalchemy import select, update

from app.database.models import ProductObservation, ResearchJobRecord, ResearchRecord, SupplierOffer
from app.domain.models import Product, utcnow
from app.domain.research import ResearchJobStatus, ResearchSession


class ResearchRepository:
    def __init__(self, sessions):
        self.sessions = sessions

    async def hydrate(self, db, payload):
        value = ResearchSession.model_validate(payload)
        if value.source_mode == 'index' and not value.products and value.pool_offer_ids:
            rows = (await db.execute(select(SupplierOffer.payload, ProductObservation.payload)
                .join(ProductObservation, ProductObservation.offer_id == SupplierOffer.id)
                .where(SupplierOffer.id.in_(value.pool_offer_ids)))).all()
            valid = set(value.indexed_offer_ids)
            value.products = [Product.model_validate(stable | observation) for stable, observation in rows]
            for product in value.products:
                if product.id not in valid:
                    product.metadata['research_invalid'] = True
        return value

    async def get(self, identifier: str) -> ResearchSession | None:
        async with self.sessions() as db:
            row = await db.get(ResearchRecord, identifier)
            return await self.hydrate(db, row.payload) if row else None

    async def latest(self, chat_id: str):
        async with self.sessions() as db:
            row = await db.scalar(select(ResearchRecord).where(ResearchRecord.chat_id == chat_id)
                                  .order_by(ResearchRecord.created_at.desc()).limit(1))
            return await self.hydrate(db, row.payload) if row else None

    async def save(self, research: ResearchSession):
        research.updated_at = utcnow()
        research.revision += 1
        async with self.sessions() as db, db.begin():
            row = await db.get(ResearchRecord, research.id)
            if row is None:
                row = ResearchRecord(id=research.id, chat_id=research.chat_id, created_at=research.created_at)
                db.add(row)
            # Cursor and products commit atomically. No cache TTL is consulted here.
            if research.source_mode == 'index':
                research.pool_offer_ids = [p.id for p in research.products]
                row.payload = research.model_dump(mode='json', exclude={'products'})
            else:
                row.payload = research.model_dump(mode='json')
            row.updated_at = research.updated_at

    async def job(self, research_id: str) -> str:
        async with self.sessions() as db, db.begin():
            row = ResearchJobRecord(research_id=research_id, status='QUEUED')
            db.add(row)
            await db.flush()
            return row.id

    async def job_status(self, identifier: str, status: ResearchJobStatus):
        async with self.sessions() as db, db.begin():
            await db.execute(update(ResearchJobRecord).where(ResearchJobRecord.id == identifier).values(
                status=status.value, completed_at=None if status in ('QUEUED', 'RUNNING') else utcnow()))

    async def recover(self):
        async with self.sessions() as db, db.begin():
            rows = (await db.scalars(select(ResearchJobRecord).where(
                ResearchJobRecord.status.in_(['QUEUED', 'RUNNING'])))).all()
            for job in rows:
                job.status = 'PARTIAL'
                job.completed_at = utcnow()
                row = await db.get(ResearchRecord, job.research_id)
                if row:
                    value = ResearchSession.model_validate(row.payload)
                    value.job_status = ResearchJobStatus.PARTIAL
                    row.payload = value.model_dump(mode='json')
        return len(rows)
