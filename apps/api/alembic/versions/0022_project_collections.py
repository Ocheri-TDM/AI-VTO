"""Project-owned selection, seeded exclusively from each active research."""

from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0022_project_collections"
down_revision = "0021_presentations"
branch_labels = depends_on = None


def upgrade():
    bind = op.get_bind()
    json_type = sa.JSON().with_variant(JSONB(), "postgresql")
    # Freeze the 0022 shape: selection_context belongs to 0024; request_epoch
    # does not exist yet. Backfill reads reflected historical tables, not ORM.
    op.create_table("project_collections",
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("project_collection_items",
        sa.Column("project_id", sa.String(36), sa.ForeignKey("project_collections.project_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("offer_id", sa.String(200), primary_key=True),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_research_id", sa.String(36)),
        sa.Column("last_known", json_type, nullable=False))
    metadata = sa.MetaData()
    tables = {name: sa.Table(name, metadata, autoload_with=bind) for name in (
        "projects", "project_queries", "research_sessions", "supplier_offers", "project_collections", "project_collection_items")}
    projects, queries, research, offers, collections, items = tables.values()
    now = datetime.now(UTC)
    for project in bind.execute(sa.select(projects.c.id, projects.c.active_query_id)).mappings():
        query = bind.execute(sa.select(queries.c.research_session_id).where(queries.c.id == project["active_query_id"])).scalar_one_or_none() if project["active_query_id"] else None
        row = bind.execute(sa.select(research.c.id, research.c.payload).where(research.c.id == query)).mappings().first() if query else None
        ids = list(dict.fromkeys(row["payload"].get("view", {}).get("selected_ids", []) if row else []))
        bind.execute(collections.insert().values(project_id=project["id"], version=1 if ids else 0, updated_at=now))
        for order, identifier in enumerate(ids):
            offer = bind.execute(sa.select(offers.c.payload, offers.c.supplier).where(offers.c.id == identifier)).mappings().first()
            if offer and offer["supplier"] != "vto":
                continue
            bind.execute(items.insert().values(project_id=project["id"], offer_id=identifier, display_order=order,
                added_at=now, source_research_id=row["id"] if row else None,
                last_known=offer["payload"] if offer else {"id": identifier, "name": "Товар недоступен в каталоге"}))


def downgrade():
    op.drop_table("project_collection_items")
    op.drop_table("project_collections")
