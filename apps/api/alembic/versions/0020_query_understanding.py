"""Persist original and normalized query interpretation."""

import sqlalchemy as sa

from alembic import op

revision = "0020_query_understanding"
down_revision = "0019_offer_normalized_colors"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("project_queries", sa.Column("normalized_query", sa.Text(), nullable=True))
    op.add_column(
        "project_queries",
        sa.Column("query_understanding", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )


def downgrade() -> None:
    op.drop_column("project_queries", "query_understanding")
    op.drop_column("project_queries", "normalized_query")
