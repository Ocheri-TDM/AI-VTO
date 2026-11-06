"""One active commercial editor; conservative finite lifetime of existing links."""

from datetime import timedelta

import sqlalchemy as sa

from alembic import op

revision = "0025_offer_lifecycle"
down_revision = "0024_commercial_offers"
branch_labels = depends_on = None


def upgrade():
    from app.domain.offer_lifecycle import primary_rank

    op.add_column(
        "presentation_documents",
        sa.Column("is_primary_offer", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("presentation_documents", sa.Column("primary_reason", sa.String(64)))
    for name in ("activated_at", "expires_at"):
        op.add_column("commercial_offer_shares", sa.Column(name, sa.DateTime(timezone=True)))
    op.add_column("commercial_offer_shares", sa.Column("activation_source", sa.String(64)))
    op.add_column("commercial_offer_shares", sa.Column("pending_epoch", sa.Integer()))
    connection = op.get_bind()
    docs = sa.Table("presentation_documents", sa.MetaData(), autoload_with=connection)
    shares = sa.Table("commercial_offer_shares", sa.MetaData(), autoload_with=connection)
    revisions = sa.Table("presentation_revisions", sa.MetaData(), autoload_with=connection)
    rows = connection.execute(sa.select(docs)).mappings().all()
    groups = {}
    existing = {s["document_id"]: s for s in connection.execute(sa.select(shares)).mappings()}
    connection.execute(
        shares.update().where(shares.c.pending_revision_id.is_not(None)).values(pending_epoch=shares.c.epoch)
    )
    for row in rows:
        if row["draft"].get("kind") == "COMMERCIAL_OFFER":
            groups.setdefault(row["project_id"], []).append(row)
    for group in groups.values():
        chosen = max(
            group, key=lambda r: primary_rank(r, bool(existing.get(r["id"], {}).get("published_revision_id")))
        )
        manual = any(
            any(i.get(k) == "MANUAL" for k in ("price_source", "quantity_source", "sku_source"))
            for i in chosen["draft"].get("items", [])
        )
        reason = "PRESERVED_MANUAL_EDITS" if manual else "NONEMPTY_SAVED_OR_PUBLISHED"
        connection.execute(
            docs.update()
            .where(docs.c.id == chosen["id"])
            .values(is_primary_offer=True, primary_reason=reason)
        )
    # Never reset dates on startup. This one-time conservative reconstruction
    # changes neither ciphertext nor active/revoked state.
    by_id = {r["id"]: r for r in rows}
    for share in existing.values():
        ready = (
            connection.execute(
                sa.select(revisions)
                .where(revisions.c.document_id == share["document_id"], revisions.c.status == "READY")
                .order_by(revisions.c.created_at)
            )
            .mappings()
            .all()
        )
        ready = [r for r in ready if r["input"].get("kind") == "COMMERCIAL_OFFER"]
        published = next((r for r in ready if r["id"] == share["published_revision_id"]), None)
        if (
            published
            and len(ready) == 1
            and abs((share["updated_at"] - published["updated_at"]).total_seconds()) <= 2
        ):
            activated, source = share["updated_at"], "CONFIRMED_FIRST_READY"
        elif ready:
            activated, source = ready[0]["created_at"], "EARLIEST_RENDER_CONSERVATIVE"
        elif share["published_revision_id"] or share["active"]:
            activated, source = by_id[share["document_id"]]["created_at"], "DOCUMENT_CREATED_CONSERVATIVE"
        else:
            continue  # A never-activated queued share starts only when READY.
        connection.execute(
            shares.update()
            .where(shares.c.document_id == share["document_id"], shares.c.activated_at.is_(None))
            .values(
                activated_at=activated, expires_at=activated + timedelta(days=7), activation_source=source
            )
        )
    op.create_index(
        "uq_primary_commercial_offer",
        "presentation_documents",
        ["project_id"],
        unique=True,
        postgresql_where=sa.text("is_primary_offer"),
        sqlite_where=sa.text("is_primary_offer"),
    )
    op.create_index("ix_commercial_offer_shares_expires_at", "commercial_offer_shares", ["expires_at"])
    op.create_table(
        "commercial_offer_requests",
        sa.Column(
            "document_id",
            sa.String(36),
            sa.ForeignKey("presentation_documents.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("request_id", sa.String(36), primary_key=True),
        sa.Column(
            "revision_id",
            sa.String(36),
            sa.ForeignKey("presentation_revisions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("publication", sa.Boolean(), nullable=False),
        sa.Column("epoch", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("commercial_offer_requests")
    op.drop_index("uq_primary_commercial_offer", "presentation_documents")
    op.drop_index("ix_commercial_offer_shares_expires_at", "commercial_offer_shares")
    for name in ("activated_at", "expires_at", "activation_source", "pending_epoch"):
        op.drop_column("commercial_offer_shares", name)
    op.drop_column("presentation_documents", "is_primary_offer")
    op.drop_column("presentation_documents", "primary_reason")
