"""Commercial publication capability and immutable per-selection quantity context."""

import sqlalchemy as sa
from alembic import op

revision = "0024_commercial_offers"
down_revision = "0023_request_epoch"
branch_labels = depends_on = None


def upgrade():
    op.add_column(
        "project_collection_items",
        sa.Column("selection_context", sa.JSON(), nullable=False, server_default="{}"),
    )
    op.create_table(
        "commercial_offer_shares",
        sa.Column(
            "document_id",
            sa.String(36),
            sa.ForeignKey("presentation_documents.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("encrypted_token", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("epoch", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("published_revision_id", sa.String(36), sa.ForeignKey("presentation_revisions.id")),
        sa.Column("pending_revision_id", sa.String(36), sa.ForeignKey("presentation_revisions.id")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_commercial_offer_shares_token_hash", "commercial_offer_shares", ["token_hash"], unique=True
    )


def downgrade():
    op.drop_table("commercial_offer_shares")
    op.drop_column("project_collection_items", "selection_context")
