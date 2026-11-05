"""One-time migration from the canonical active research, never historical unions."""

from sqlalchemy import select

from app.database.models import (
    Project,
    ProjectCollection,
    ProjectCollectionItem,
    ProjectQuery,
    ResearchRecord,
    SupplierOffer,
)


def initialize_collection(db, project_id):
    existing = db.get(ProjectCollection, project_id)
    if existing:
        return existing
    project = db.get(Project, project_id)
    if not project:
        return None
    collection = ProjectCollection(project_id=project_id, version=0)
    db.add(collection)
    db.flush()
    query = db.get(ProjectQuery, project.active_query_id) if project.active_query_id else None
    research = (
        db.get(ResearchRecord, query.research_session_id) if query and query.research_session_id else None
    )
    ids = list(dict.fromkeys(research.payload.get("view", {}).get("selected_ids", []) if research else []))
    offers = (
        {o.id: o for o in db.scalars(select(SupplierOffer).where(SupplierOffer.id.in_(ids)))} if ids else {}
    )
    for order, id in enumerate(ids):
        offer = offers.get(id)
        if offer and offer.supplier != "vto":
            continue
        db.add(
            ProjectCollectionItem(
                project_id=project_id,
                offer_id=id,
                display_order=order,
                source_research_id=research.id if research else None,
                last_known=offer.payload if offer else {"id": id, "name": "Товар недоступен в каталоге"},
            )
        )
    if ids:
        collection.version = 1
    return collection
