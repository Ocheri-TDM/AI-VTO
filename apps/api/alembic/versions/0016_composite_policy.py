"""Invalidate composite cases assembled before strict eligibility policy."""

from alembic import op

revision = "0016_composite_policy"
down_revision = "0015_hard_requests"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("UPDATE hard_request_cases SET status='STALE_ASSEMBLY'")
    op.execute("UPDATE hard_requests SET status='STALE_ASSEMBLY'")
    op.execute("UPDATE project_queries SET status='ARCHIVED' WHERE type='HARD'")


def downgrade():
    op.execute("UPDATE hard_request_cases SET status='COMPLETE' WHERE status='STALE_ASSEMBLY'")
    op.execute("UPDATE hard_requests SET status='COMPLETED' WHERE status='STALE_ASSEMBLY'")
