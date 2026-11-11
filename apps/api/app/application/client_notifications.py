"""Owner-only read models and replay; streams use short database reads."""

from contextlib import asynccontextmanager

from sqlalchemy import and_, distinct, func, or_, select, text

from app.application.client_selections import next_owner_event
from app.database.models import (
    CommercialOfferShare as Share,
)
from app.database.models import (
    ManagerEvent as Event,
)
from app.database.models import (
    ManagerEventCursor as Cursor,
)
from app.database.models import (
    ManagerNotification as Notification,
)
from app.database.models import (
    OfferClientSelection as Selection,
)
from app.database.models import (
    OfferVisitor as Visitor,
)
from app.database.models import (
    PresentationDocument as Document,
)
from app.database.models import (
    PresentationRevision as Revision,
)
from app.domain.client_selection import SelectionProblem
from app.domain.commercial_offer import KIND


class ClientNotificationService:
    def __init__(self, selections):
        self.selections, self.sessions, self.offers = selections, selections.sessions, selections.offers

    @asynccontextmanager
    async def snapshot(self):
        async with self.sessions() as db:
            # Cursor and rows must describe one snapshot, even if a guest commits
            # between the SELECTs. No transaction survives beyond this response.
            if db.bind.dialect.name == "postgresql":
                await db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
            yield db

    async def cursor(self, db, user):
        return await db.scalar(select(Cursor.sequence).where(Cursor.user_id == user)) or 0

    async def notifications(self, user, before=None, limit=50):
        async with self.snapshot() as db:
            cursor = await self.cursor(db, user)
            where = [Notification.user_id == user, Document.deleted_at.is_(None)]
            unread = await db.scalar(
                select(func.count())
                .select_from(Notification)
                .join(Document, Document.id == Notification.document_id)
                .where(*where, Notification.read_sequence < Notification.last_sequence)
            )
            stmt = (
                select(Notification, Visitor.ordinal)
                .join(Document, Document.id == Notification.document_id)
                .join(Visitor, Visitor.id == Notification.visitor_id)
                .where(*where)
            )
            if before is not None:
                stmt = stmt.where(Notification.last_sequence < before)
            rows = (await db.execute(stmt.order_by(Notification.last_sequence.desc()).limit(limit + 1))).all()
            items = [
                {
                    "id": n.id,
                    "document_id": n.document_id,
                    "title": n.title,
                    "visitor_label": f"Посетитель {ordinal}",
                    "selected_count": n.selected_count,
                    "sequence": n.last_sequence,
                    "read_sequence": n.read_sequence,
                    "unread": n.read_sequence < n.last_sequence,
                    "updated_at": n.updated_at,
                    "available": True,
                }
                for n, ordinal in rows[:limit]
            ]
            return {
                "cursor": cursor,
                "unread_count": unread,
                "items": items,
                "next_before": items[-1]["sequence"] if len(rows) > limit else None,
            }

    async def mark_read(self, user, identifier, seen):
        async with self.selections.atomic() as db:
            # Lock ordering matches mutations: owner cursor before notification.
            await db.execute(
                text(
                    "INSERT INTO manager_event_cursors (user_id,sequence) VALUES (:user,0) ON CONFLICT (user_id) DO NOTHING"
                ),
                {"user": user},
            )
            await db.scalar(select(Cursor).where(Cursor.user_id == user).with_for_update())
            notification = await db.scalar(
                select(Notification)
                .where(Notification.id == identifier, Notification.user_id == user)
                .with_for_update()
            )
            if not notification:
                raise SelectionProblem("NOTIFICATION_NOT_FOUND", "Уведомление недоступно", 404)
            if seen > notification.last_sequence:
                raise SelectionProblem(
                    "INVALID_NOTIFICATION_VERSION", "Обновите список уведомлений перед чтением."
                )
            if seen > notification.read_sequence:
                notification.read_sequence = seen
                await next_owner_event(
                    db,
                    user,
                    # Read acknowledgements belong to the account. Inserting
                    # a document FK here takes an implicit KEY SHARE lock after
                    # the owner cursor, opposite to a guest's document->cursor
                    # order. It can deadlock with a concurrent guest mutation.
                    None,
                    "notification.read",
                    {"notification_id": identifier, "read_sequence": seen},
                )
        return await self.notifications(user)

    async def events(self, user, after):
        async with self.snapshot() as db:
            current = await self.cursor(db, user)
            rows = list(
                await db.scalars(
                    select(Event.sequence)
                    .where(Event.user_id == user, Event.sequence > after)
                    .order_by(Event.sequence)
                    .limit(100)
                )
            )
            # Includes deleted/retained-away history and invalid future cursors.
            reset = (
                after > current
                or (after < current and not rows)
                or any(value != after + n + 1 for n, value in enumerate(rows))
            )
            return {"cursor": current, "reset": reset, "events": rows}

    async def summaries(self, db, documents):
        if not documents:
            return {}
        shares = {
            s.document_id: s for s in await db.scalars(select(Share).where(Share.document_id.in_(documents)))
        }
        revision_ids = [s.published_revision_id for s in shares.values() if s.published_revision_id]
        revisions = (
            {r.id: r for r in await db.scalars(select(Revision).where(Revision.id.in_(revision_ids)))}
            if revision_ids
            else {}
        )
        values = {
            id: {"visitor_count": 0, "item_count": 0, "mark_count": 0, "historical_mark_count": 0}
            for id in documents
        }
        current_items = {
            id: {
                i["id"]
                for i in revisions[shares[id].published_revision_id]
                .result.get("client_snapshot", {})
                .get("items", [])
            }
            if id in shares and shares[id].published_revision_id in revisions
            else set()
            for id in documents
        }
        base = (
            select(Visitor.document_id, func.count())
            .join(Selection, Selection.visitor_id == Visitor.id)
            .where(Visitor.document_id.in_(documents), Selection.selected.is_(True))
            .group_by(Visitor.document_id)
        )
        totals = dict((await db.execute(base)).all())
        conditions = [
            and_(
                Visitor.document_id == id,
                Visitor.generation == shares[id].token_hash,
                Selection.item_id.in_(current_items[id]),
            )
            for id in documents
            if current_items[id]
        ]
        if conditions:
            active = (
                select(
                    Visitor.document_id,
                    func.count(distinct(Visitor.id)),
                    func.count(distinct(Selection.item_id)),
                    func.count(),
                )
                .join(Selection, Selection.visitor_id == Visitor.id)
                .where(Selection.selected.is_(True), or_(*conditions))
                .group_by(Visitor.document_id)
            )
            for id, visitors, items, marks in (await db.execute(active)).all():
                values[id].update(visitor_count=visitors, item_count=items, mark_count=marks)
        for id, value in values.items():
            value["historical_mark_count"] = totals.get(id, 0) - value["mark_count"]
        return values

    async def interest(self, user, identifiers):
        async with self.snapshot() as db:
            rows = list(
                await db.scalars(
                    select(Document).where(
                        Document.id.in_(identifiers),
                        Document.user_id == user,
                        Document.draft["kind"].as_string() == KIND,
                    )
                )
            )
            if {d.id for d in rows} != set(identifiers):
                raise SelectionProblem("OFFER_NOT_FOUND", "Предложение недоступно", 404)
            documents = [d.id for d in rows if d.deleted_at is None]
            return {
                "cursor": await self.cursor(db, user),
                "offers": await self.summaries(db, documents),
                "unavailable_ids": [d.id for d in rows if d.deleted_at is not None],
            }

    async def feedback(self, user, identifier, before=None, limit=20):
        async with self.snapshot() as db:
            document = await db.scalar(
                select(Document).where(
                    Document.id == identifier,
                    Document.user_id == user,
                    Document.deleted_at.is_(None),
                    Document.draft["kind"].as_string() == KIND,
                )
            )
            if not document:
                raise SelectionProblem("OFFER_NOT_FOUND", "Предложение недоступно", 404)
            share = await db.get(Share, identifier)
            current = (
                await db.get(Revision, share.published_revision_id)
                if share and share.published_revision_id
                else None
            )
            current_items = (
                {i["id"]: i for i in current.result.get("client_snapshot", {}).get("items", [])}
                if current
                else {}
            )
            stmt = select(Visitor).where(Visitor.document_id == identifier, Visitor.version > 0)
            if before is not None:
                stmt = stmt.where(Visitor.last_sequence < before)
            visitors = list(await db.scalars(stmt.order_by(Visitor.last_sequence.desc()).limit(limit + 1)))
            more = len(visitors) > limit
            visitors = visitors[:limit]
            rows = (
                list(
                    await db.scalars(
                        select(Selection).where(
                            Selection.visitor_id.in_([v.id for v in visitors]), Selection.selected.is_(True)
                        )
                    )
                )
                if visitors
                else []
            )
            history = list(
                await db.scalars(
                    select(Event)
                    .where(
                        Event.user_id == user,
                        Event.document_id == identifier,
                        Event.event_type == "selection.changed",
                    )
                    .order_by(Event.sequence.desc())
                    .limit(50)
                )
            )
            revision_ids = {r.published_revision_id for r in rows} | {
                r.payload.get("published_revision_id") for r in history
            }
            revisions = (
                {r.id: r for r in await db.scalars(select(Revision).where(Revision.id.in_(revision_ids)))}
                if revision_ids
                else {}
            )
            revision_items = {
                id: {i["id"]: i for i in r.result.get("client_snapshot", {}).get("items", [])}
                for id, r in revisions.items()
            }
            output = []
            for visitor in visitors:
                items = []
                for selection in rows:
                    if selection.visitor_id != visitor.id:
                        continue
                    revision = revisions.get(selection.published_revision_id)
                    item = revision_items.get(selection.published_revision_id, {}).get(selection.item_id)
                    if not revision or not item:
                        continue
                    published_item = current_items.get(item["id"])
                    image = item.get("main_image")
                    conditions_changed = published_item is not None and (
                        item.get("unit_price"),
                        item.get("quantity"),
                    ) != (published_item.get("unit_price"), published_item.get("quantity"))
                    items.append(
                        {
                            "item_id": item["id"],
                            "title": item["title"],
                            "sku": item["sku"],
                            "image_url": f"/api/offers/{identifier}/revisions/{revision.id}/assets/{image}"
                            if image
                            else None,
                            "published_revision_id": revision.id,
                            "source_version": revision.source_version,
                            "unit_price": item.get("unit_price"),
                            "quantity": item["quantity"],
                            "selected_at": selection.updated_at,
                            "conditions_changed": conditions_changed,
                            "removed": published_item is None,
                            "previous_revision": current is None or revision.id != current.id,
                        }
                    )
                output.append(
                    {
                        "id": visitor.id,
                        "label": f"Посетитель {visitor.ordinal}",
                        "current_round": bool(share and visitor.generation == share.token_hash),
                        "updated_at": visitor.updated_at,
                        "sequence": visitor.last_sequence,
                        "items": items,
                        "selected_count": len(items),
                    }
                )
            numbers = (
                {
                    v.id: v.ordinal
                    for v in await db.scalars(
                        select(Visitor).where(Visitor.id.in_([e.visitor_id for e in history]))
                    )
                }
                if history
                else {}
            )
            events = []
            for event in history:
                payload = event.payload
                item = revision_items.get(payload.get("published_revision_id"), {}).get(
                    payload.get("item_id")
                )
                events.append(
                    {
                        "sequence": event.sequence,
                        "visitor_label": f"Посетитель {numbers.get(event.visitor_id, '—')}",
                        "item_id": payload.get("item_id"),
                        "title": item.get("title", "Позиция предыдущей версии")
                        if item
                        else "Позиция предыдущей версии",
                        "selected": payload.get("selected"),
                        "published_revision_id": payload.get("published_revision_id"),
                        "created_at": event.created_at,
                    }
                )
            return {
                "document_id": identifier,
                "cursor": await self.cursor(db, user),
                "published_revision_id": current.id if current else None,
                "summary": (await self.summaries(db, [identifier]))[identifier],
                "visitors": output,
                "events": events,
                "next_before": visitors[-1].last_sequence if more else None,
                "identity_notice": "Посетители — браузерные сессии, а не подтверждённые личности клиентов.",
            }
