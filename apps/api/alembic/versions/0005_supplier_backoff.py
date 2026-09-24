"""Supplier-wide backoff, shared by discovery, refresh and targeted jobs."""

from alembic import op
import sqlalchemy as sa

revision = "0005_supplier_backoff"
down_revision = "0004_research_index"
branch_labels = None
depends_on = None


def upgrade():
    # 0004 uses a frozen table definition; existing indexes and observations are retained.
    op.add_column(
        "supplier_sync_states",
        sa.Column("retry_after", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade():
    op.drop_column("supplier_sync_states", "retry_after")
