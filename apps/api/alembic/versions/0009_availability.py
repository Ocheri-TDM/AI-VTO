"""Source-driven normalized availability observations."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0009_availability"
down_revision = "0008_search_trigram"
branch_labels = None
depends_on = None


def upgrade():
    json_type = JSONB() if op.get_bind().dialect.name == "postgresql" else sa.JSON()
    for name in ("incoming", "reserved", "total"):
        op.add_column("product_observations", sa.Column(name, sa.Integer(), nullable=True))
    op.add_column("product_observations", sa.Column("incoming_at", sa.DateTime(timezone=True)))
    op.add_column("product_observations", sa.Column("availability_status", sa.String(40)))
    op.add_column("product_observations", sa.Column("incoming_status", sa.String(40)))
    op.add_column("product_observations", sa.Column("parser_version", sa.String(80)))
    op.add_column("product_observations", sa.Column("source_availability", json_type, nullable=False, server_default='{}'))


def downgrade():
    for name in ("source_availability", "parser_version", "incoming_status", "availability_status",
                 "incoming_at", "total", "reserved", "incoming"):
        op.drop_column("product_observations", name)
