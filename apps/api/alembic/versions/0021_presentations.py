"""Project documents and immutable input revisions with independent render claims."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0021_presentations"
down_revision = "0020_query_understanding"
branch_labels = depends_on = None


def upgrade():
    # Historical DDL must not import future ORM columns/indexes (0025).
    json_type = sa.JSON().with_variant(JSONB(), "postgresql")
    op.create_table("presentation_documents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False), sa.Column("draft", json_type, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    for column in ("project_id", "user_id"):
        op.create_index("ix_presentation_documents_"+column, "presentation_documents", [column])
    op.create_table("presentation_revisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("document_id", sa.String(36), sa.ForeignKey("presentation_documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_version", sa.Integer(), nullable=False), sa.Column("status", sa.String(32), nullable=False),
        sa.Column("input", json_type, nullable=False), sa.Column("result", json_type, nullable=False),
        sa.Column("error", sa.String(300)), sa.Column("claim", sa.String(36)),
        sa.Column("lease_until", sa.DateTime(timezone=True)), sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_presentation_revisions_document_id", "presentation_revisions", ["document_id"])
    op.create_index("ix_presentation_queue", "presentation_revisions", ["status", "created_at"])


def downgrade():
    op.drop_table("presentation_revisions")
    op.drop_table("presentation_documents")
