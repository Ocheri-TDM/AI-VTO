"""Independent guest interest, durable owner events and grouped unread state."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0028_client_selections"
down_revision = "0027_offer_deletion"
branch_labels = depends_on = None


def fk(name, target, primary=False, nullable=False):
    return sa.Column(
        name, sa.String(36), sa.ForeignKey(target, ondelete="CASCADE"), primary_key=primary, nullable=nullable
    )


def stamp(name):
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False)


def upgrade():
    op.create_table(
        "manager_event_cursors",
        fk("user_id", "users.id", True),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
    )
    op.create_table(
        "offer_visitor_budgets",
        fk("document_id", "presentation_documents.id", True),
        sa.Column("generation", sa.String(64), primary_key=True),
        *[
            sa.Column(n, sa.Integer(), nullable=False)
            for n in ["total_sessions", "total_mutations", "window_sessions", "window_mutations"]
        ],
        stamp("window_at"),
        stamp("created_at"),
    )
    op.create_table(
        "offer_visitors",
        sa.Column("id", sa.String(36), primary_key=True),
        fk("document_id", "presentation_documents.id"),
        sa.Column("generation", sa.String(64), nullable=False),
        sa.Column("secret_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("last_sequence", sa.BigInteger(), nullable=False),
        sa.Column("total_requests", sa.Integer(), nullable=False),
        sa.Column("window_requests", sa.Integer(), nullable=False),
        *[stamp(n) for n in ["window_at", "expires_at", "created_at", "updated_at"]],
        sa.UniqueConstraint("document_id", "generation", "ordinal", name="uq_offer_visitor_number"),
    )
    op.create_index("ix_offer_visitors_document_id", "offer_visitors", ["document_id"])
    op.create_table(
        "offer_client_selections",
        fk("visitor_id", "offer_visitors.id", True),
        sa.Column("item_id", sa.String(36), primary_key=True),
        sa.Column("selected", sa.Boolean(), nullable=False),
        fk("published_revision_id", "presentation_revisions.id"),
        stamp("created_at"),
        stamp("updated_at"),
    )
    op.create_table(
        "offer_client_mutations",
        fk("visitor_id", "offer_visitors.id", True),
        sa.Column("mutation_id", sa.String(36), primary_key=True),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("result_version", sa.BigInteger(), nullable=False),
        sa.Column("event_sequence", sa.BigInteger()),
        stamp("created_at"),
    )
    op.create_table(
        "manager_notifications",
        sa.Column("id", sa.String(36), primary_key=True),
        fk("user_id", "users.id"),
        fk("document_id", "presentation_documents.id"),
        sa.Column(
            "visitor_id",
            sa.String(36),
            sa.ForeignKey("offer_visitors.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("selected_count", sa.Integer(), nullable=False),
        sa.Column("last_sequence", sa.BigInteger(), nullable=False),
        sa.Column("read_sequence", sa.BigInteger(), nullable=False),
        stamp("updated_at"),
    )
    for name in ["user_id", "document_id"]:
        op.create_index("ix_manager_notifications_" + name, "manager_notifications", [name])
    op.create_table(
        "manager_events",
        fk("user_id", "users.id", True),
        sa.Column("sequence", sa.BigInteger(), primary_key=True),
        fk("document_id", "presentation_documents.id", nullable=True),
        fk("visitor_id", "offer_visitors.id", nullable=True),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("payload", sa.JSON().with_variant(JSONB(), "postgresql"), nullable=False),
        stamp("created_at"),
    )
    op.create_index("ix_manager_event_document", "manager_events", ["document_id", "sequence"])


def downgrade():
    for name in [
        "manager_events",
        "manager_notifications",
        "offer_client_mutations",
        "offer_client_selections",
        "offer_visitors",
        "offer_visitor_budgets",
        "manager_event_cursors",
    ]:
        op.drop_table(name)
