"""Application entities and expiring observations; no product master catalog."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "projects",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("client_name", sa.String(300)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "chats",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "search_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("cache_key", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="SET NULL")),
        sa.Column("intent", json_type, nullable=False),
        sa.Column("suppliers", json_type, nullable=False),
        sa.Column("traces", json_type, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_search_cache_expiry", "search_sessions", ["cache_key", "expires_at"])
    op.create_index("ix_search_sessions_expires_at", "search_sessions", ["expires_at"])
    op.create_table(
        "messages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("chat_id", sa.String(36), sa.ForeignKey("chats.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "search_session_id", sa.String(36), sa.ForeignKey("search_sessions.id", ondelete="SET NULL")
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_messages_chat_id", "messages", ["chat_id"])
    op.create_table(
        "cached_products",
        sa.Column(
            "search_session_id",
            sa.String(36),
            sa.ForeignKey("search_sessions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("product_id", sa.String(200), primary_key=True),
        sa.Column("payload", json_type, nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_cached_products_expires_at", "cached_products", ["expires_at"])


def downgrade():
    for table in ("cached_products", "messages", "search_sessions", "chats", "projects", "users"):
        op.drop_table(table)
