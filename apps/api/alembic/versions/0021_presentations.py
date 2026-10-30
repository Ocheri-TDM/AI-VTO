"""Project documents and immutable input revisions with independent render claims."""

from alembic import op

revision = "0021_presentations"
down_revision = "0020_query_understanding"
branch_labels = depends_on = None


def upgrade():
    from app.database.models import PresentationDocument, PresentationRevision

    PresentationDocument.__table__.create(op.get_bind(), checkfirst=True)
    PresentationRevision.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    op.drop_table("presentation_revisions")
    op.drop_table("presentation_documents")
