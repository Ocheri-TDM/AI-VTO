"""Store supplier availability as multiple source-preserving records."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0010_availability_records"
down_revision = "0009_availability"
branch_labels = None
depends_on = None


def upgrade():
    json_type = JSONB() if op.get_bind().dialect.name == "postgresql" else sa.JSON()
    op.create_table(
        "availability_observations",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("offer_id", sa.String(200), sa.ForeignKey("supplier_offers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("location_code", sa.String(100)),
        sa.Column("location_label", sa.Text()),
        sa.Column("state", sa.String(40), nullable=False),
        sa.Column("total_quantity", sa.Integer()),
        sa.Column("free_quantity", sa.Integer()),
        sa.Column("reserved_quantity", sa.Integer()),
        sa.Column("expected_at", sa.DateTime(timezone=True)),
        sa.Column("lead_time_min_days", sa.Integer()),
        sa.Column("lead_time_max_days", sa.Integer()),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("parser_version", sa.String(80), nullable=False),
        sa.Column("source_label", sa.Text(), nullable=False),
        sa.Column("source_evidence", json_type, nullable=False, server_default="{}"),
        sa.Column("confidence", sa.String(20), nullable=False, server_default="HIGH"),
    )
    for column in ("offer_id", "state", "expected_at", "observed_at"):
        op.create_index(f"ix_availability_observations_{column}", "availability_observations", [column])


def downgrade():
    op.drop_table("availability_observations")
