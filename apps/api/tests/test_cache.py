from datetime import timedelta

import pytest
from conftest import make_product
from sqlalchemy import func, select

from app.database.models import CachedProduct, SearchSession
from app.domain.errors import SearchExpiredError
from app.domain.models import SearchIntent, SearchSnapshot, SupplierStatus, utcnow


async def test_cache_ttl_is_strict_and_cleanup_cascades(repository):
    now = utcnow()
    snapshot = SearchSnapshot(
        status="completed",
        intent=SearchIntent(raw_query="ручка"),
        expires_at=now + timedelta(hours=1),
        products=[make_product()],
    )
    await repository.create(snapshot, "key")
    await repository.save(snapshot)
    hit = await repository.find_cached("key", now=now + timedelta(minutes=59))
    assert hit is not None and hit.cache_hit and len(hit.products) == 1
    assert await repository.find_cached("key", now=snapshot.expires_at) is None
    with pytest.raises(SearchExpiredError):
        await repository.get(snapshot.id, now=snapshot.expires_at)
    assert await repository.purge_expired(now=snapshot.expires_at) == 1
    async with repository.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(CachedProduct)) == 0
        assert await db.scalar(select(func.count()).select_from(SearchSession)) == 0


async def test_restart_marks_abandoned_jobs_and_preserves_completed_supplier(repository):
    snapshot = SearchSnapshot(
        intent=SearchIntent(raw_query="ручка"),
        expires_at=utcnow() + timedelta(hours=1),
        suppliers=[
            SupplierStatus(supplier="oasis", status="completed"),
            SupplierStatus(supplier="ucontay", status="running"),
        ],
    )
    await repository.create(snapshot, "key")
    await repository.recover_interrupted()
    recovered = await repository.get(snapshot.id)
    assert recovered.status == "partial"
    assert recovered.suppliers[1].error_code == "SEARCH_INTERRUPTED"
