"""Index literal supplier text used alongside Russian FTS."""
from alembic import op

revision = "0008_search_trigram"
down_revision = "0007_taxonomy_research"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_bind().dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_index_search_trgm "
            "ON indexed_products USING gin (search_document gin_trgm_ops)"
        )


def downgrade():
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS ix_index_search_trgm")
