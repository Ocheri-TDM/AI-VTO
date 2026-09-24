"""Durable research aggregate and recoverable jobs, separate from expiring search cache."""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('research_sessions',
                    sa.Column('id', sa.String(36), primary_key=True),
                    sa.Column('chat_id', sa.String(36), sa.ForeignKey('chats.id', ondelete='CASCADE'), nullable=False),
                    sa.Column('payload', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=False),
                    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_research_sessions_chat_id', 'research_sessions', ['chat_id'])
    op.create_table('research_jobs',
                    sa.Column('id', sa.String(36), primary_key=True),
                    sa.Column('research_id', sa.String(36), sa.ForeignKey('research_sessions.id', ondelete='CASCADE'), nullable=False),
                    sa.Column('status', sa.String(20), nullable=False),
                    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
                    sa.Column('completed_at', sa.DateTime(timezone=True)))
    op.create_index('ix_research_jobs_research_id', 'research_jobs', ['research_id'])


def downgrade():
    op.drop_table('research_jobs')
    op.drop_table('research_sessions')
