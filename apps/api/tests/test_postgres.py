import os
from datetime import timedelta

import pytest
from conftest import make_product
from sqlalchemy import delete, text

from app.database.connection import create_database
from app.database.models import SearchSession
from app.database.repository import SearchRepository
from app.domain.models import SearchIntent, SearchSnapshot, utcnow

pytestmark = pytest.mark.postgres


async def test_real_postgres_jsonb_cache_and_cascade():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to an already migrated disposable PostgreSQL database")
    engine, sessions = create_database(url)
    repository = SearchRepository(sessions)
    now = utcnow()
    snapshot = SearchSnapshot(
        status="completed",
        intent=SearchIntent(raw_query="Тест PostgreSQL"),
        expires_at=now + timedelta(hours=1),
        products=[make_product()],
    )
    try:
        await repository.create(snapshot, snapshot.id)
        await repository.save(snapshot)
        result = await repository.find_cached(snapshot.id)
        assert result and result.products[0].original_price == snapshot.products[0].original_price
        assert result.expires_at.tzinfo is not None
        async with sessions() as db:
            assert (
                await db.scalar(
                    text("SELECT pg_typeof(payload)::text FROM cached_products WHERE search_session_id=:id"),
                    {"id": snapshot.id},
                )
                == "jsonb"
            )
        assert await repository.find_cached(snapshot.id, now=snapshot.expires_at) is None
        async with sessions.begin() as db:
            await db.execute(delete(SearchSession).where(SearchSession.id == snapshot.id))
        async with sessions() as db:
            assert (
                await db.scalar(
                    text("SELECT count(*) FROM cached_products WHERE search_session_id=:id"),
                    {"id": snapshot.id},
                )
                == 0
            )
    finally:
        async with sessions.begin() as db:
            await db.execute(delete(SearchSession).where(SearchSession.id == snapshot.id))
        await engine.dispose()
