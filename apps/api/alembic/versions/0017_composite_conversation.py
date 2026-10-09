"""Persistent composite conversation and revision references."""
import sqlalchemy as sa

from alembic import op

revision = "0017_composite_conversation"
down_revision = "0016_composite_policy"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("hard_requests", sa.Column("parent_request_id", sa.String(36), nullable=True))
    op.add_column("hard_requests", sa.Column("revision_number", sa.Integer(), nullable=False, server_default="1"))
    op.create_index("ix_hard_requests_parent_request_id", "hard_requests", ["parent_request_id"])
    op.add_column("messages", sa.Column("project_query_id", sa.String(36), nullable=True))
    op.add_column("messages", sa.Column("hard_request_id", sa.String(36), nullable=True))
    op.add_column("messages", sa.Column("structured_operation", sa.JSON(), nullable=True))
    op.add_column("messages", sa.Column("result_revision", sa.Integer(), nullable=True))
    op.create_index("ix_messages_project_query_id", "messages", ["project_query_id"])
    op.create_index("ix_messages_hard_request_id", "messages", ["hard_request_id"])


def downgrade():
    op.drop_index("ix_messages_hard_request_id", table_name="messages")
    op.drop_index("ix_messages_project_query_id", table_name="messages")
    for column in ("result_revision", "structured_operation", "hard_request_id", "project_query_id"):
        op.drop_column("messages", column)
    op.drop_index("ix_hard_requests_parent_request_id", table_name="hard_requests")
    op.drop_column("hard_requests", "revision_number")
    op.drop_column("hard_requests", "parent_request_id")
