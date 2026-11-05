"""Serialize publication without retaining a project transaction during planning."""

import sqlalchemy as sa

from alembic import op

revision = "0023_request_epoch"
down_revision = "0022_project_collections"
branch_labels = depends_on = None


def upgrade():
    op.add_column("projects", sa.Column("request_epoch", sa.Integer(), nullable=False, server_default="0"))


def downgrade():
    op.drop_column("projects", "request_epoch")
