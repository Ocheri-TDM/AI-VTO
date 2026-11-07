"""Owner-controlled tombstones and a fence for stale get-or-create requests."""

import sqlalchemy as sa

from alembic import op

revision = "0027_offer_deletion"
down_revision = "0026_chat_workflow"
branch_labels = depends_on = None


def upgrade():
    op.add_column("presentation_documents", sa.Column("deleted_at", sa.DateTime(timezone=True)))
    op.create_index("ix_presentation_documents_deleted_at", "presentation_documents", ["deleted_at"])
    op.add_column("projects", sa.Column("offer_generation", sa.Integer(), nullable=False, server_default="0"))


def downgrade():
    op.drop_column("projects", "offer_generation")
    op.drop_index("ix_presentation_documents_deleted_at", "presentation_documents")
    op.drop_column("presentation_documents", "deleted_at")
