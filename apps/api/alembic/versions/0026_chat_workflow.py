"""Durable conversation operations, naming provenance and fourteen-day links."""

from datetime import UTC, datetime, timedelta

import sqlalchemy as sa

from alembic import op

revision = "0026_chat_workflow"
down_revision = "0025_offer_lifecycle"
branch_labels = depends_on = None


def extend_live_links(connection, now):
    shares = sa.Table("commercial_offer_shares", sa.MetaData(), autoload_with=connection)

    def aware(value):
        return value.replace(tzinfo=UTC) if value and value.tzinfo is None else value

    for row in connection.execute(sa.select(shares)).mappings():
        activated, expires = aware(row["activated_at"]), aware(row["expires_at"])
        if row["active"] and (not activated or not expires or activated > now or expires < activated):
            connection.execute(
                shares.update()
                .where(shares.c.document_id == row["document_id"])
                .values(
                    active=False,
                    epoch=row["epoch"] + 1,
                    pending_revision_id=None,
                    pending_epoch=None,
                    activation_source="INVALID_ACTIVATION_REQUIRES_REISSUE",
                    expires_at=now,
                )
            )
            continue
        if not row["active"] or not activated or not expires or expires <= now:
            continue
        # Only a proved original seven-day period is extended. An uncertain
        # historical reconstruction retains its finite old boundary, never now+14.
        if row["activation_source"] not in {"READY_ACTIVATION", "CONFIRMED_FIRST_READY"}:
            continue
        if expires == activated + timedelta(days=7) and activated <= now:
            connection.execute(
                shares.update()
                .where(shares.c.document_id == row["document_id"])
                .values(expires_at=activated + timedelta(days=14))
            )


def upgrade():
    op.add_column(
        "projects", sa.Column("name_source", sa.String(20), nullable=False, server_default="manual")
    )
    op.add_column("projects", sa.Column("name_version", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("chats", sa.Column("creation_key", sa.String(36)))
    op.create_index("uq_chat_create_request", "chats", ["user_id", "creation_key"], unique=True)
    op.add_column("messages", sa.Column("operation_id", sa.String(36)))
    op.create_index("ix_messages_operation_id", "messages", ["operation_id"])
    op.create_index("ix_chat_message_cursor", "messages", ["chat_id", "created_at", "id"])
    op.create_table(
        "conversation_turns",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("chat_id", sa.String(36), sa.ForeignKey("chats.id", ondelete="CASCADE"), nullable=False),
        sa.Column("request_key", sa.String(36), nullable=False),
        sa.Column("request_epoch", sa.Integer(), nullable=False),
        sa.Column(
            "user_message_id", sa.String(36), sa.ForeignKey("messages.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("assistant_message_id", sa.String(36), sa.ForeignKey("messages.id", ondelete="SET NULL")),
        sa.Column("research_id", sa.String(36), sa.ForeignKey("research_sessions.id", ondelete="SET NULL")),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("action", sa.String(32)),
        sa.Column("error_code", sa.String(64)),
        sa.Column("title_candidate", sa.String(300)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("uq_conversation_request", "conversation_turns", ["chat_id", "request_key"], unique=True)
    op.create_index("ix_conversation_turns_chat_id", "conversation_turns", ["chat_id"])
    extend_live_links(op.get_bind(), datetime.now(UTC))


def downgrade():
    # A shorter TTL is not retroactively imposed on already issued links.
    op.drop_table("conversation_turns")
    op.drop_index("ix_chat_message_cursor", "messages")
    op.drop_index("ix_messages_operation_id", "messages")
    op.drop_column("messages", "operation_id")
    op.drop_index("uq_chat_create_request", "chats")
    op.drop_column("chats", "creation_key")
    op.drop_column("projects", "name_version")
    op.drop_column("projects", "name_source")
