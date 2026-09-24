"""Persistent conversations, expiring selection state and bounded history."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    j = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
    op.add_column(
        "chats",
        sa.Column(
            "active_search_session_id",
            sa.String(36),
            sa.ForeignKey("search_sessions.id", ondelete="SET NULL"),
        ),
    )
    op.add_column("chats", sa.Column("last_intent", j, nullable=False, server_default="{}"))
    for name in ("state", "coverage"):
        op.add_column("search_sessions", sa.Column(name, j, nullable=False, server_default="{}"))
    op.create_table(
        "search_state_history",
        sa.Column(
            "search_session_id",
            sa.String(36),
            sa.ForeignKey("search_sessions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("version", sa.Integer, primary_key=True),
        sa.Column("snapshot", j, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "agent_executions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("chat_id", sa.String(36), sa.ForeignKey("chats.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "user_message_id", sa.String(36), sa.ForeignKey("messages.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("decision", j, nullable=False),
        sa.Column("tool_calls", j, nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_agent_executions_chat_id", "agent_executions", ["chat_id"])
    # Defaults populate old rows; application supplies all subsequent values.
    for table, names in [("chats", ["last_intent"]), ("search_sessions", ["state", "coverage"])]:
        for name in names:
            op.alter_column(table, name, server_default=None)


def downgrade():
    op.drop_table("agent_executions")
    op.drop_table("search_state_history")
    op.drop_column("search_sessions", "coverage")
    op.drop_column("search_sessions", "state")
    op.drop_column("chats", "last_intent")
    op.drop_column("chats", "active_search_session_id")
