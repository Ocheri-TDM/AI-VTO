"""Project-owned selection, seeded exclusively from each active research."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from alembic import op

revision = "0022_project_collections"
down_revision = "0021_presentations"
branch_labels = depends_on = None


def upgrade():
    from app.database.collections import initialize_collection
    from app.database.models import Project, ProjectCollection, ProjectCollectionItem

    bind = op.get_bind()
    ProjectCollection.__table__.create(bind, checkfirst=True)
    ProjectCollectionItem.__table__.create(bind, checkfirst=True)
    with Session(bind=bind) as db:
        for id in db.scalars(select(Project.id)).all():
            initialize_collection(db, id)
        db.flush()


def downgrade():
    op.drop_table("project_collection_items")
    op.drop_table("project_collections")
