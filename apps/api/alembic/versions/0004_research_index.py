"""Persistent supplier observation index and leased background queue."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0004_research_index"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    js = sa.JSON().with_variant(JSONB(), "postgresql")
    op.create_table(
        "indexed_products",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("category", sa.String(200)),
        sa.Column("search_document", sa.Text(), nullable=False),
        sa.Column("metadata_payload", js, nullable=False),
    )
    op.create_index("ix_indexed_products_category", "indexed_products", ["category"])
    op.create_table(
        "supplier_offers",
        sa.Column("id", sa.String(200), primary_key=True),
        sa.Column("product_id", sa.String(64), sa.ForeignKey("indexed_products.id"), nullable=False),
        sa.Column("supplier", sa.String(40), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("payload", js, nullable=False),
    )
    op.create_index("ix_supplier_offers_product_id", "supplier_offers", ["product_id"])
    op.create_index("ix_supplier_offers_supplier", "supplier_offers", ["supplier"])
    op.create_table(
        "product_observations",
        sa.Column(
            "offer_id",
            sa.String(200),
            sa.ForeignKey("supplier_offers.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("price_kzt", sa.Numeric(18, 2)),
        sa.Column("stock", sa.Integer()),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("payload", js, nullable=False),
    )
    op.create_index("ix_product_observations_expires_at", "product_observations", ["expires_at"])
    op.create_table(
        "supplier_sync_states",
        sa.Column("supplier", sa.String(40), primary_key=True),
        sa.Column("lease_owner", sa.String(36)),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("next_refresh_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("next_discovery_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("error", sa.String(100)),
        sa.Column("failures", sa.Integer(), nullable=False),
    )
    op.create_table(
        "catalog_sync_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("supplier", sa.String(40), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("dedup_key", sa.String(64), unique=True, nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("checkpoint", js, nullable=False),
        sa.Column("metrics", js, nullable=False),
        sa.Column("error", sa.String(100)),
    )
    op.create_index("ix_catalog_sync_jobs_supplier", "catalog_sync_jobs", ["supplier"])
    op.create_index("ix_sync_queue", "catalog_sync_jobs", ["status", "available_at", "priority"])
    if bind.dialect.name == "postgresql":
        op.execute(
            "CREATE INDEX ix_index_fts ON indexed_products USING gin (to_tsvector('russian', search_document))"
        )
        op.execute("CREATE INDEX ix_observation_stock_price ON product_observations(stock, price_kzt)")


def downgrade():
    for table in (
        "catalog_sync_jobs",
        "supplier_sync_states",
        "product_observations",
        "supplier_offers",
        "indexed_products",
    ):
        op.drop_table(table)
