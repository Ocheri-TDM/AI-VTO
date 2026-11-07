"""Owner-scoped bounded document summaries, without share keys or asset paths."""

import base64
import json
from datetime import datetime

from sqlalchemy import and_, func, or_, select

from app.database.models import (
    Chat,
    CommercialOfferShare,
    Project,
)
from app.database.models import (
    PresentationDocument as Document,
)
from app.database.models import (
    PresentationRevision as Revision,
)
from app.domain.commercial_offer import KIND
from app.domain.offer_lifecycle import expired, live


async def listing(service, user, cursor=None, limit=20, archive=False):
    async with service.sessions() as db:
        count_array = (
            func.jsonb_array_length if db.bind.dialect.name == "postgresql" else func.json_array_length
        )
        chat = (
            select(Chat.id)
            .where(Chat.project_id == Document.project_id, Chat.user_id == user)
            .order_by(Chat.created_at, Chat.id)
            .limit(1)
            .correlate(Document)
            .scalar_subquery()
        )
        latest = (
            select(Revision.id)
            .where(Revision.document_id == Document.id, Revision.input["kind"].as_string() == KIND)
            .order_by(Revision.created_at.desc(), Revision.id.desc())
            .limit(1)
            .correlate(Document)
            .scalar_subquery()
        )
        ready = (
            select(Revision.id)
            .where(
                Revision.document_id == Document.id,
                Revision.status == "READY",
                Revision.input["kind"].as_string() == KIND,
            )
            .order_by(Revision.created_at.desc(), Revision.id.desc())
            .limit(1)
            .correlate(Document)
            .scalar_subquery()
        )
        stmt = select(
            Document.id,
            Document.version,
            Document.updated_at,
            Document.is_primary_offer,
            Document.project_id,
            Document.draft["settings"]["title"].as_string().label("title"),
            Project.name.label("project_name"),
            chat.label("chat_id"),
            count_array(Document.draft["items"]).label("item_count"),
            CommercialOfferShare,
            latest.label("latest_id"),
            ready.label("ready_id"),
        ).join(Project, Project.id == Document.project_id)
        stmt = stmt.outerjoin(CommercialOfferShare, CommercialOfferShare.document_id == Document.id).where(
            Document.user_id == user, Project.user_id == user, Document.deleted_at.is_(None)
        )
        kind = Document.draft["kind"].as_string()
        stmt = stmt.where(or_(kind.is_(None), kind != KIND) if archive else kind == KIND)
        if cursor:
            try:
                if len(cursor) > 256:
                    raise ValueError()
                stamp, identifier = json.loads(base64.urlsafe_b64decode(cursor.encode()))
                if not isinstance(stamp, str) or not isinstance(identifier, str):
                    raise ValueError()
                stamp = datetime.fromisoformat(stamp)
            except (ValueError, TypeError, UnicodeError) as exc:
                raise ValueError("INVALID_CURSOR") from exc
            stmt = stmt.where(
                or_(Document.updated_at < stamp, and_(Document.updated_at == stamp, Document.id < identifier))
            )
        rows = (
            await db.execute(stmt.order_by(Document.updated_at.desc(), Document.id.desc()).limit(limit + 1))
        ).all()
        more = len(rows) > limit
        rows = rows[:limit]
        ids = [id for row in rows for id in [row.latest_id, row.ready_id] if id]
        revisions = (
            {
                r.id: r
                for r in (
                    await db.execute(
                        select(
                            Revision.id,
                            Revision.status,
                            Revision.source_version,
                            Revision.result["pdf"].as_string().is_not(None).label("pdf"),
                            Revision.result["xlsx"].as_string().is_not(None).label("xlsx"),
                        ).where(Revision.id.in_(ids))
                    )
                ).all()
            }
            if ids
            else {}
        )
        items = []
        now = service.documents.offer_now()
        for row in rows:
            latest_revision = revisions.get(row.latest_id)
            ready_revision = revisions.get(row.ready_id)
            usable = ready_revision and (
                ready_revision.source_version == row.version or not row.is_primary_offer
            )
            share = row.CommercialOfferShare
            publication = (
                "EXPIRED"
                if expired(share, now)
                else "ACTIVE"
                if live(share, now)
                else "PENDING"
                if share and share.pending_revision_id
                else "UNPUBLISHED"
            )
            items.append(
                {
                    "id": row.id,
                    "version": row.version,
                    "title": row.title or "Документ",
                    "project_id": row.project_id,
                    "project_name": row.project_name,
                    "chat_id": row.chat_id,
                    "item_count": row.item_count or 0,
                    "updated_at": row.updated_at.isoformat(),
                    "is_primary": row.is_primary_offer,
                    "draft_state": latest_revision.status
                    if latest_revision and latest_revision.source_version == row.version
                    else "SAVED",
                    "publication_state": publication,
                    "expires_at": share.expires_at.isoformat() if share and share.expires_at else None,
                    "can_copy": bool(live(share, now) and service.origin),
                    "pdf_url": f"/api/offers/{row.id}/revisions/{ready_revision.id}/pdf"
                    if usable and ready_revision.pdf
                    else None,
                    "xlsx_url": f"/api/offers/{row.id}/revisions/{ready_revision.id}/xlsx"
                    if usable and ready_revision.xlsx
                    else None,
                }
            )
        next_cursor = (
            base64.urlsafe_b64encode(
                json.dumps([rows[-1].updated_at.isoformat(), rows[-1].id]).encode()
            ).decode()
            if more and rows
            else None
        )
        return {"items": items, "next_cursor": next_cursor}
