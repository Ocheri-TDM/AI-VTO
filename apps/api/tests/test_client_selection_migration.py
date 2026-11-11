"""Additive migration against a populated isolated old schema, never live data."""

import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text

from app.database.models import Base

FEATURE = {
    "manager_event_cursors",
    "offer_visitor_budgets",
    "offer_visitors",
    "offer_client_selections",
    "offer_client_mutations",
    "manager_notifications",
    "manager_events",
}


def test_selection_migration_preserves_existing_rows_and_has_scoped_keys(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "migration.db"))
    spec = importlib.util.spec_from_file_location(
        "client_selection_migration", Path(__file__).parents[1] / "alembic/versions/0028_client_selections.py"
    )
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    try:
        with engine.begin() as connection:
            Base.metadata.create_all(
                connection, tables=[t for t in Base.metadata.sorted_tables if t.name not in FEATURE]
            )
            connection.execute(
                text(
                    "INSERT INTO users(id,display_name,normalized_name,password_hash,created_at,updated_at) VALUES ('keep','Preserve','preserve','!',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
                )
            )
            before = connection.execute(text("SELECT * FROM users")).all()
            migration.op = Operations(MigrationContext.configure(connection))
            migration.upgrade()
            inspector = inspect(connection)
            assert FEATURE <= set(inspector.get_table_names())
            assert connection.execute(text("SELECT * FROM users")).all() == before
            assert inspector.get_pk_constraint("offer_client_selections")["constrained_columns"] == [
                "visitor_id",
                "item_id",
            ]
            assert inspector.get_pk_constraint("offer_client_mutations")["constrained_columns"] == [
                "visitor_id",
                "mutation_id",
            ]
            assert inspector.get_pk_constraint("manager_events")["constrained_columns"] == [
                "user_id",
                "sequence",
            ]
            assert any(
                c["column_names"] == ["visitor_id"]
                for c in inspector.get_unique_constraints("manager_notifications")
            )
            assert all(connection.scalar(text("SELECT count(*) FROM " + name)) == 0 for name in FEATURE)
            migration.downgrade()  # This test-only SQLite database, no other DB.
            assert not FEATURE & set(inspect(connection).get_table_names())
            assert connection.execute(text("SELECT * FROM users")).all() == before
    finally:
        engine.dispose()
