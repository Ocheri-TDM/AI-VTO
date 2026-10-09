"""Add an indexed color projection for offer-level retrieval."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0019_offer_normalized_colors"
down_revision = "0018_offer_color_lookup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "supplier_offers",
        sa.Column(
            "normalized_colors",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.execute(
        "UPDATE supplier_offers SET normalized_colors = "
        "jsonb_path_query_array(payload, '$.colors[*].normalized_color')"
    )
    op.execute(
        "CREATE INDEX ix_supplier_offers_normalized_colors_gin "
        "ON supplier_offers USING gin (normalized_colors jsonb_path_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_supplier_offers_normalized_colors_gin")
    op.drop_column("supplier_offers", "normalized_colors")
