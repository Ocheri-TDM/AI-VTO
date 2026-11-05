import time
from collections import deque

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.pool import AsyncAdaptedQueuePool


class MeasuredPool(AsyncAdaptedQueuePool):
    """Bounded timing samples, scoped to this engine, without SQL/parameters."""

    def __init__(self, *args, **kwargs):
        self.wait_seconds = deque(maxlen=2048)
        self.checkout_count = 0
        super().__init__(*args, **kwargs)

    def _do_get(self):
        started = time.perf_counter()
        try:
            return super()._do_get()
        finally:
            self.wait_seconds.append(time.perf_counter() - started)
            self.checkout_count += 1


def create_database(url: str) -> tuple[AsyncEngine, async_sessionmaker]:
    options = {"pool_timeout": 3, "poolclass": MeasuredPool} if not url.startswith("sqlite") else {}
    engine = create_async_engine(url, pool_pre_ping=True, **options)
    engine.sync_engine.sql_seconds = deque(maxlen=2048)

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def sql_start(connection, cursor, statement, parameters, context, executemany):
        context._quality_started = time.perf_counter()

    @event.listens_for(engine.sync_engine, "after_cursor_execute")
    def sql_done(connection, cursor, statement, parameters, context, executemany):
        engine.sync_engine.sql_seconds.append(time.perf_counter() - context._quality_started)

    if url.startswith("postgresql"):

        @event.listens_for(engine.sync_engine, "begin")
        def bounded_search_transaction(connection):
            from app.api.request_safety import search_transaction

            if search_transaction.get():
                # Transaction-local only: background imports keep their policy.
                connection.exec_driver_sql("SET LOCAL statement_timeout = '12000ms'")
                connection.exec_driver_sql("SET LOCAL lock_timeout = '2000ms'")

    if url.startswith("sqlite"):
        # SQLite is exclusively a test dependency; production defaults to PostgreSQL.
        @event.listens_for(engine.sync_engine, "connect")
        def set_foreign_keys(connection, _record):
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine, async_sessionmaker(engine, expire_on_commit=False)
