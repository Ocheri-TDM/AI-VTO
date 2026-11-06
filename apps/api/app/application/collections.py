"""Atomic project collection operations, independent of search/filter history."""

import asyncio
from collections import defaultdict
from contextlib import asynccontextmanager

from sqlalchemy import select

from app.database.collections import initialize_collection
from app.database.models import (
    Chat,
    Project,
    ProjectCollectionItem,
    ResearchRecord,
    SupplierOffer,
)
from app.domain.models import utcnow


class CollectionService:
    def __init__(self, sessions, index):
        self.sessions, self.index = sessions, index
        self.locks = defaultdict(asyncio.Lock)

    @asynccontextmanager
    async def locked(self, project, owner):
        async with self.locks[project], self.sessions.begin() as db:
            p = await db.scalar(
                select(Project).where(Project.id == project, Project.user_id == owner).with_for_update()
            )
            if not p:
                yield None, db
                return
            collection = await db.run_sync(lambda sync: initialize_collection(sync, project))
            yield collection, db

    async def state(self, db, collection):
        items = (
            await db.scalars(
                select(ProjectCollectionItem)
                .where(ProjectCollectionItem.project_id == collection.project_id)
                .order_by(ProjectCollectionItem.display_order, ProjectCollectionItem.added_at)
            )
        ).all()
        return {
            "project_id": collection.project_id,
            "version": collection.version,
            "selected_ids": [i.offer_id for i in items],
            "items": [
                {
                    "offer_id": i.offer_id,
                    "added_at": i.added_at.isoformat(),
                    "source_research_id": i.source_research_id,
                    "last_known": i.last_known,
                    "selection_context": i.selection_context,
                }
                for i in items
            ],
        }

    async def for_chat(self, chat):
        async with self.sessions() as db:
            row = (
                await db.execute(
                    select(Project.id, Project.user_id)
                    .join(Chat, Chat.project_id == Project.id)
                    .where(Chat.id == chat)
                )
            ).first()
        if not row:
            return None
        return await self.read(row.id, row.user_id, hydrate=False)

    async def read(self, project, owner, hydrate=True):
        async with self.locked(project, owner) as (collection, db):
            if collection is None:
                return None
            state = await self.state(db, collection)
        return await self.hydrate(state) if hydrate else state

    async def hydrate(self, state):
        from app.api.research import product_view

        products = (
            await self.index.candidates(None, offer_ids=state["selected_ids"])
            if state["selected_ids"]
            else []
        )
        by_id = {p.id: product_view(p) for p in products}
        missing = []
        values = []
        for item in state.pop("items"):
            id = item["offer_id"]
            if id in by_id:
                value = by_id[id] | {"unavailable": False}
            else:
                missing.append(id)
                p = item["last_known"]
                value = {
                    "id": id,
                    "name": p.get("name", "Товар недоступен в каталоге"),
                    "category": p.get("category"),
                    "brand": p.get("brand"),
                    "colors": p.get("colors", []),
                    "images": p.get("images", []),
                    "primary_image": p.get("primary_image"),
                    "price_kzt": None,
                    "freshness": "STALE",
                    "unavailable": True,
                }
            values.append(value)
        return state | {"products": values, "unavailable_ids": missing}

    async def mutate(self, project, owner, operation, offer_id=None, source=None, version=None, ids=None):
        async with self.locked(project, owner) as (collection, db):
            if collection is None:
                return None
            if operation in ("clear", "replace") and version != collection.version:
                raise ValueError("STATE_CONFLICT")
            existing = (
                await db.scalars(
                    select(ProjectCollectionItem).where(ProjectCollectionItem.project_id == project)
                )
            ).all()
            known = {i.offer_id: i for i in existing}
            wanted = (
                list(dict.fromkeys(ids))
                if operation == "replace"
                else ([offer_id] if operation == "add" else [])
            )
            for id in wanted:
                if id in known:
                    continue
                offer = await db.get(SupplierOffer, id)
                if not offer or offer.supplier != "vto" or offer.lifecycle == "REMOVED":
                    raise ValueError("Вариант недоступен в каталоге")
                if source:
                    research = await db.scalar(
                        select(ResearchRecord)
                        .join(Chat)
                        .where(ResearchRecord.id == source, Chat.project_id == project)
                    )
                    if not research or id not in research.payload.get("pool_offer_ids", []):
                        raise ValueError("Вариант отсутствует в исходном исследовании")
            remove = (
                list(known)
                if operation == "clear"
                else (
                    [offer_id]
                    if operation == "remove"
                    else [i for i in known if i not in wanted]
                    if operation == "replace"
                    else []
                )
            )
            changed = False
            for id in remove:
                if id in known:
                    await db.delete(known[id])
                    changed = True
            position = max((i.display_order for i in existing), default=-1) + 1
            for id in wanted:
                if id not in known:
                    offer = await db.get(SupplierOffer, id)
                    from app.domain.selection_quantity import selection_quantity
                    source_row = await db.get(ResearchRecord, source) if source else None
                    db.add(
                        ProjectCollectionItem(
                            project_id=project,
                            offer_id=id,
                            display_order=position,
                            source_research_id=source,
                            last_known=offer.payload,
                            selection_context=selection_quantity(source_row.payload if source_row else None, id),
                        )
                    )
                    position += 1
                    changed = True
            if changed:
                collection.version += 1
                collection.updated_at = utcnow()
            await db.flush()
            state = await self.state(db, collection)
        return await self.hydrate(state)
