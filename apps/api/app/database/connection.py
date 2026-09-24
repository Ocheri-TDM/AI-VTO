from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine


def create_database(url: str) -> tuple[AsyncEngine, async_sessionmaker]:
    engine = create_async_engine(url, pool_pre_ping=True)
    if url.startswith("sqlite"):
        # SQLite is exclusively a test dependency; production defaults to PostgreSQL.
        @event.listens_for(engine.sync_engine, "connect")
        def set_foreign_keys(connection, _record):
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine, async_sessionmaker(engine, expire_on_commit=False)
