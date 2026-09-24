from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database.models import CachedProduct, SearchSession
from app.domain.errors import SearchExpiredError
from app.domain.models import Product, SearchIntent, SearchSnapshot, SupplierStatus, utcnow


def aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


class SearchRepository:
    def __init__(self, sessions: async_sessionmaker):
        self.sessions = sessions

    async def create(self, snapshot: SearchSnapshot, key: str) -> None:
        async with self.sessions.begin() as db:
            db.add(
                SearchSession(
                    id=snapshot.id,
                    cache_key=key,
                    status=snapshot.status,
                    intent=snapshot.intent.model_dump(mode="json"),
                    state=snapshot.state,
                    coverage=snapshot.coverage,
                    suppliers=[s.model_dump(mode="json") for s in snapshot.suppliers],
                    traces=[],
                    created_at=snapshot.created_at,
                    expires_at=snapshot.expires_at,
                )
            )

    async def save(self, snapshot: SearchSnapshot) -> None:
        async with self.sessions.begin() as db:
            record = await db.get(SearchSession, snapshot.id)
            if record is None:
                return
            record.status = snapshot.status
            record.intent = snapshot.intent.model_dump(mode="json")
            record.state = snapshot.state
            record.coverage = snapshot.coverage
            record.suppliers = [s.model_dump(mode="json") for s in snapshot.suppliers]
            record.traces = [t.model_dump(mode="json") for t in snapshot.traces[-2000:]]
            await db.execute(delete(CachedProduct).where(CachedProduct.search_session_id == snapshot.id))
            for product in snapshot.products:
                db.add(
                    CachedProduct(
                        search_session_id=snapshot.id,
                        product_id=product.id,
                        payload=product.model_dump(mode="json"),
                        fetched_at=product.fetched_at,
                        expires_at=snapshot.expires_at,
                    )
                )

    async def get(self, session_id: str, *, now: datetime | None = None) -> SearchSnapshot | None:
        current = now or utcnow()
        async with self.sessions() as db:
            record = await db.get(SearchSession, session_id)
            if record is None:
                return None
            if aware(record.expires_at) <= current:
                raise SearchExpiredError(session_id)
            products = (
                await db.scalars(
                    select(CachedProduct).where(
                        CachedProduct.search_session_id == session_id,
                        CachedProduct.expires_at > current,
                    )
                )
            ).all()
            return SearchSnapshot(
                id=record.id,
                status=record.status,
                intent=SearchIntent.model_validate(record.intent),
                state=record.state,
                coverage=record.coverage,
                suppliers=[SupplierStatus.model_validate(s) for s in record.suppliers],
                traces=record.traces,
                products=[Product.model_validate(p.payload) for p in products],
                created_at=aware(record.created_at),
                expires_at=aware(record.expires_at),
            )

    async def find_cached(self, key: str, *, now: datetime | None = None) -> SearchSnapshot | None:
        current = now or utcnow()
        async with self.sessions() as db:
            session_id = await db.scalar(
                select(SearchSession.id)
                .where(
                    SearchSession.cache_key == key,
                    SearchSession.expires_at > current,
                    SearchSession.status.in_(["queued", "running", "completed", "partial"]),
                )
                .order_by(SearchSession.created_at.desc())
                .limit(1)
            )
        if session_id:
            snapshot = await self.get(session_id, now=current)
            if snapshot:
                snapshot.cache_hit = True
            return snapshot
        return None

    async def purge_expired(self, *, now: datetime | None = None) -> int:
        async with self.sessions.begin() as db:
            result = await db.execute(
                delete(SearchSession).where(SearchSession.expires_at <= (now or utcnow()))
            )
            return result.rowcount

    async def recover_interrupted(self) -> None:
        # Stage one intentionally runs a single local API worker. A restart cannot leave polling stuck.
        async with self.sessions.begin() as db:
            records = (
                await db.scalars(select(SearchSession).where(SearchSession.status.in_(["queued", "running"])))
            ).all()
            for record in records:
                statuses = [SupplierStatus.model_validate(s) for s in record.suppliers]
                for status in statuses:
                    if status.status in ("pending", "running"):
                        status.status = "failed"
                        status.error_code = "SEARCH_INTERRUPTED"
                        status.message = "Поиск прерван перезапуском приложения. Обновите подборку."
                record.suppliers = [s.model_dump(mode="json") for s in statuses]
                record.status = "partial" if any(s.status == "completed" for s in statuses) else "failed"
