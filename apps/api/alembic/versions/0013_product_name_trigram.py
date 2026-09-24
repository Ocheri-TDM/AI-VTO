"""Index product names for bounded typo-tolerant retrieval."""

from alembic import op

revision = "0013_product_name_trigram"
down_revision = "0012_open_product_concepts"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "CREATE INDEX ix_indexed_products_name_trgm "
            "ON indexed_products USING gin (name gin_trgm_ops)"
        )


def downgrade():
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS ix_indexed_products_name_trgm")
