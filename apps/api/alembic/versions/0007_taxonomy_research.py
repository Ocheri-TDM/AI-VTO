"""Reviewable taxonomy suggestions and indexed research creation time."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '0007_taxonomy_research'
down_revision = '0006_catalog_safety'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('research_sessions', sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    op.execute("UPDATE research_sessions SET created_at=COALESCE((payload->>'created_at')::timestamptz, updated_at)")
    op.create_index('ix_research_sessions_created_at', 'research_sessions', ['created_at'])
    op.create_table('taxonomy_suggestions',
        sa.Column('id',sa.String(64),primary_key=True),
        sa.Column('supplier',sa.String(40),nullable=False),
        sa.Column('supplier_label',sa.Text(),nullable=False),
        sa.Column('supplier_path',sa.Text(),nullable=False),
        sa.Column('samples',JSONB(),nullable=False),
        sa.Column('suggested_mapping',sa.String(200)),
        sa.Column('confidence',sa.Numeric(5,4),nullable=False),
        sa.Column('status',sa.String(30),nullable=False))


def downgrade():
    op.drop_table('taxonomy_suggestions')
    op.drop_column('research_sessions','created_at')
