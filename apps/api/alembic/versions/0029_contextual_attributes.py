"""Add an evidence-backed attribute projection without rewriting catalog rows."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0029_contextual_attributes"
down_revision = "0028_client_selections"
branch_labels = depends_on = None


def upgrade():
    json_type = sa.JSON().with_variant(JSONB(), "postgresql")
    op.create_table(
        "offer_attribute_projections",
        sa.Column(
            "offer_id",
            sa.String(200),
            sa.ForeignKey("supplier_offers.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("version", sa.String(60), nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("diagnostics", json_type, nullable=False),
        sa.Column("derived_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "offer_attribute_values",
        sa.Column(
            "offer_id",
            sa.String(200),
            sa.ForeignKey("supplier_offers.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("attribute_id", sa.String(40), primary_key=True),
        sa.Column("value_key", sa.String(150), primary_key=True),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("numeric_value", sa.Numeric(18, 6)),
        sa.Column("evidence", json_type, nullable=False),
    )
    op.create_index(
        "ix_offer_attributes_lookup", "offer_attribute_values", ["attribute_id", "value_key", "offer_id"]
    )


def downgrade():
    op.drop_table("offer_attribute_values")
    op.drop_table("offer_attribute_projections")
