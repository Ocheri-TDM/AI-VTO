"""Index structured supplier-offer colors used by Composite retrieval."""

from alembic import op

revision = "0018_offer_color_lookup"
down_revision = "0017_composite_conversation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_supplier_offers_colors_gin "
        "ON supplier_offers USING gin ((payload -> 'colors') jsonb_path_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_supplier_offers_colors_gin")
