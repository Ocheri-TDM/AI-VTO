"""Add catalog-derived open-vocabulary product concepts."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0012_open_product_concepts"
down_revision = "0011_user_accounts"
branch_labels = None
depends_on = None


def upgrade():
    json_type = JSONB() if op.get_bind().dialect.name == "postgresql" else sa.JSON()
    op.create_table(
        "product_concepts",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("canonical_name", sa.String(300), nullable=False),
        sa.Column("display_name", sa.String(300), nullable=False),
        sa.Column("aliases", json_type, nullable=False, server_default="[]"),
        sa.Column("evidence", json_type, nullable=False, server_default="{}"),
        sa.Column("products_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_product_concepts_canonical_name", "product_concepts", ["canonical_name"], unique=True)
    op.create_table(
        "product_concept_offers",
        sa.Column("concept_id", sa.String(64), sa.ForeignKey("product_concepts.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("offer_id", sa.String(200), sa.ForeignKey("supplier_offers.id", ondelete="CASCADE"), primary_key=True),
    )


def downgrade():
    op.drop_table("product_concept_offers")
    op.drop_table("product_concepts")
