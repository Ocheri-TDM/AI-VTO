"""Adaptive catalog and availability synchronization state."""

from alembic import op
import sqlalchemy as sa

revision = "0014_adaptive_sync"
down_revision = "0013_product_name_trigram"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("supplier_offers") as batch:
        batch.add_column(sa.Column("metadata_hash", sa.String(64)))
        batch.add_column(sa.Column("commercial_hash", sa.String(64)))
        batch.add_column(sa.Column("last_searched_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("last_opened_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("last_selected_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("research_hit_count", sa.Integer(), server_default="0", nullable=False))
        batch.add_column(sa.Column("unchanged_refreshes", sa.Integer(), server_default="0", nullable=False))
        batch.add_column(sa.Column("last_commercial_change_at", sa.DateTime(timezone=True)))
        batch.create_index("ix_supplier_offers_commercial_hash", ["commercial_hash"])
        batch.create_index("ix_supplier_offers_last_searched_at", ["last_searched_at"])
        batch.create_index("ix_supplier_offers_last_opened_at", ["last_opened_at"])
        batch.create_index("ix_supplier_offers_last_selected_at", ["last_selected_at"])
    with op.batch_alter_table("product_observations") as batch:
        batch.add_column(sa.Column("last_verified_at", sa.DateTime(timezone=True)))
        batch.create_index("ix_product_observations_last_verified_at", ["last_verified_at"])
    with op.batch_alter_table("supplier_sync_states") as batch:
        batch.add_column(sa.Column("next_incremental_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("next_reconciliation_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("circuit_open_until", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("last_parser_version", sa.String(80)))
    op.execute("UPDATE supplier_sync_states SET next_incremental_at=next_discovery_at, next_reconciliation_at=next_discovery_at")
    op.execute("UPDATE product_observations SET last_verified_at=observed_at")


def downgrade():
    with op.batch_alter_table("supplier_sync_states") as batch:
        for name in ("last_parser_version", "circuit_open_until", "next_reconciliation_at", "next_incremental_at"):
            batch.drop_column(name)
    with op.batch_alter_table("product_observations") as batch:
        batch.drop_index("ix_product_observations_last_verified_at")
        batch.drop_column("last_verified_at")
    with op.batch_alter_table("supplier_offers") as batch:
        for name in ("ix_supplier_offers_last_selected_at", "ix_supplier_offers_last_opened_at", "ix_supplier_offers_last_searched_at", "ix_supplier_offers_commercial_hash"):
            batch.drop_index(name)
        for name in ("last_commercial_change_at", "unchanged_refreshes", "research_hit_count", "last_selected_at", "last_opened_at", "last_searched_at", "commercial_hash", "metadata_hash"):
            batch.drop_column(name)
