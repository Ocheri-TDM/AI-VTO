"""Durable catalog run evidence and conservative offer reconciliation."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '0006_catalog_safety'
down_revision = '0005_supplier_backoff'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('supplier_offers', sa.Column('lifecycle', sa.String(30), nullable=False, server_default='ACTIVE'))
    op.add_column('supplier_offers', sa.Column('missing_runs', sa.Integer(), nullable=False, server_default='0'))
    op.create_table('catalog_runs',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('supplier', sa.String(40), nullable=False),
        sa.Column('kind', sa.String(40), nullable=False),
        sa.Column('status', sa.String(30), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True)),
        sa.Column('anomalies', JSONB(), nullable=False))
    op.create_index('ix_catalog_runs_supplier', 'catalog_runs', ['supplier'])
    op.create_table('catalog_seen',
        sa.Column('run_id', sa.String(36), sa.ForeignKey('catalog_runs.id'), primary_key=True),
        sa.Column('offer_id', sa.String(200), primary_key=True),
        sa.Column('payload', JSONB(), nullable=False))


def downgrade():
    op.drop_table('catalog_seen')
    op.drop_table('catalog_runs')
    op.drop_column('supplier_offers', 'missing_runs')
    op.drop_column('supplier_offers', 'lifecycle')
