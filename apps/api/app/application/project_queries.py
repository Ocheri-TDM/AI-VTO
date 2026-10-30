"""One active product research per project. Historical assemblies are read-only."""

import asyncio
import re
from collections import defaultdict
from contextlib import asynccontextmanager
from uuid import uuid4

from sqlalchemy import select, text, update

from app.application.product_grid import brand_key
from app.database.models import Chat, HardRequestRecord, Message, Project, ProjectQuery
from app.domain.models import ColorGroup, utcnow
from app.domain.services import ColorNormalizer
from app.domain.taxonomy import UniversalCategoryResolver


class ProjectQueryService:
    def __init__(self, sessions, research, router):
        self.sessions, self.research, self.router = sessions, research, router
        self.locks = defaultdict(asyncio.Lock)

    @asynccontextmanager
    async def lock(self, project_id):
        async with self.locks[project_id], self.sessions() as db:
            postgres = db.bind.dialect.name == "postgresql"
            if postgres:
                await db.execute(text("SELECT pg_advisory_lock(hashtext(:key))"), {"key": project_id})
            try:
                yield
            finally:
                if postgres:
                    await db.execute(text("SELECT pg_advisory_unlock(hashtext(:key))"), {"key": project_id})

    async def project(self, id, owner):
        if owner is None and not self.research.settings.auth_required:
            owner = "00000000-0000-0000-0000-000000000001"
        async with self.sessions() as db:
            row = (
                await db.execute(
                    select(Project, Chat)
                    .join(Chat, Chat.project_id == Project.id)
                    .where(Project.id == id, Project.user_id == owner)
                )
            ).first()
            return row

    @staticmethod
    def view(row, active=None):
        return {
            "id": row.id,
            "project_id": row.project_id,
            "type": row.type,
            "research_session_id": row.research_session_id,
            "query_text": row.query_text,
            "status": row.status,
            "is_active": row.id == active,
            "created_at": row.created_at.isoformat(),
            "updated_at": row.updated_at.isoformat(),
        }

    async def list_queries(self, project_id, owner):
        project = await self.project(project_id, owner)
        if not project:
            return None
        async with self.sessions() as db:
            rows = (
                await db.scalars(
                    select(ProjectQuery)
                    .where(ProjectQuery.project_id == project_id)
                    .order_by(ProjectQuery.created_at.desc())
                )
            ).all()
            return [self.view(row, project[0].active_query_id) for row in rows]

    async def active(self, project_id, owner):
        project = await self.project(project_id, owner)
        if not project:
            return None
        async with self.sessions() as db:
            row = (
                await db.get(ProjectQuery, project[0].active_query_id) if project[0].active_query_id else None
            )
            if row and row.research_session_id:
                r = await self.research.repository.get(row.research_session_id, hydrate=False)
                return await self.research.grid_upgrade(r) if r else None
            return None

    async def create(self, project_id, query, owner):
        project = await self.project(project_id, owner)
        if not project:
            return None
        r = await self.research.start(project[1].id, query)
        now = utcnow()
        async with self.sessions.begin() as db:
            await db.execute(
                update(ProjectQuery)
                .where(ProjectQuery.project_id == project_id, ProjectQuery.status == "ACTIVE")
                .values(status="ARCHIVED", updated_at=now)
            )
            row = ProjectQuery(
                id=str(uuid4()),
                project_id=project_id,
                research_session_id=r.id,
                type="STANDARD",
                query_text=query,
                normalized_query=r.intent.normalized_query,
                query_understanding=r.intent.query_understanding,
                status="ACTIVE",
            )
            db.add(row)
            p = await db.get(Project, project_id)
            p.active_query_id = row.id
            for message in r.messages:
                db.add(
                    Message(
                        id=str(uuid4()), chat_id=r.chat_id, role=message["role"], content=message["content"]
                    )
                )
            await db.flush()
            result = self.view(row, row.id)
        return {"query": result, "research": r, "action": "NEW_SEARCH"}

    async def activate(self, project_id, query_id, owner):
        async with self.lock(project_id):
            if not await self.project(project_id, owner):
                return None
            async with self.sessions() as db:
                row = await db.scalar(
                    select(ProjectQuery).where(
                        ProjectQuery.id == query_id, ProjectQuery.project_id == project_id
                    )
                )
                if not row:
                    return None
                if row.type != "STANDARD" or not row.research_session_id:
                    # Explicit replay of an archive runs product retrieval only.
                    return await self.create(project_id, row.query_text, owner)
                id = row.research_session_id
            async with self.sessions.begin() as db:
                await db.execute(
                    update(ProjectQuery)
                    .where(ProjectQuery.project_id == project_id)
                    .values(status="ARCHIVED")
                )
                row = await db.get(ProjectQuery, query_id)
                row.status = "ACTIVE"
                p = await db.get(Project, project_id)
                p.active_query_id = query_id
            return {
                "query": self.view(row, query_id),
                "research": await self.research.grid_upgrade(
                    await self.research.repository.get(id, hydrate=False)
                ),
                "action": "COMMAND",
            }

    async def request(self, project_id, message, owner):
        async with self.lock(project_id):
            if not await self.project(project_id, owner):
                return None
            current = await self.active(project_id, owner)
            if not current or not current.unified:
                return await self.create(project_id, message, owner)
            # View-only commands are deterministic and must never call Qwen.
            lower = message.casefold()
            brands = [
                v
                for v in await self.research.grid_brands()
                if re.search(r"(?<!\w)" + re.escape(v) + r"(?!\w)", message, re.I)
            ]
            categories = UniversalCategoryResolver().resolve(message)
            if re.search(r"^(?:теперь\s+)?(?:найди|ищи|подбери)|новый поиск|забудь", lower) or (
                categories
                and not re.search(r"оставь|сделай|убери|добав|только|теперь|сначала|больше не", lower)
            ):
                return await self.create(project_id, message, owner)
            changes = {}
            filters = current.view.filters.model_copy(deep=True)
            action = "REFINE_CURRENT"
            intent_change = False
            if re.search(r"сброс|без фильтров|покажи все", lower):
                changes["restore"] = "reset"
            elif re.search(r"отмен|назад|верни", lower):
                changes["restore"] = "undo"
                action = "COMMAND"
            elif brands:
                filters.brands = list(dict.fromkeys(brand_key(v) for v in brands))
            elif categories and re.search(r"убери|исключи", lower):
                current.intent.branches = [b for b in current.intent.branches if b.category not in categories]
                intent_change = True
                action = "REMOVE_CONSTRAINT"
            elif categories and re.search(r"добав", lower):
                extra = await self.research.grid_plan(message)
                current.intent.branches.extend(
                    b for b in extra.branches if b.category not in current.intent.categories
                )
                intent_change = True
                action = "EXPAND_CURRENT"
            elif re.search(r"(?:цвет|тираж|количество|материал|цена|бюджет).*не важ", lower):
                if "цвет" in lower:
                    current.intent.colors = []
                if "тираж" in lower or "количество" in lower:
                    current.intent.quantity = None
                if "материал" in lower:
                    current.intent.material = None
                if "цен" in lower or "бюджет" in lower:
                    current.intent.budget = None
                for branch in current.intent.branches:
                    if "цвет" in lower:
                        branch.colors = []
                    if "тираж" in lower or "количество" in lower:
                        branch.quantity = None
                    if "материал" in lower:
                        branch.materials = []
                    if "цен" in lower or "бюджет" in lower:
                        branch.max_price = branch.min_price = None
                if "цвет" in lower:
                    filters.colors = []
                intent_change = True
            else:
                parsed = await self.research.grid_plan(message) if categories else None
                color = ColorNormalizer().normalize(message).normalized_color
                price = re.search(r"(?:до|дороже)\s*(\d[\d ]*)", lower)
                materials = next(
                    (
                        v
                        for token, v in [("пластик", "пластик"), ("металл", "металл"), ("дерев", "дерев")]
                        if token in lower
                    ),
                    None,
                )
                if re.search(r"сначала|желательно|предпочтительно|лучше", lower):
                    filters.origins["preference"] = "chat"
                    current.view.preferences = [materials or color.value]
                    changes["sorting"] = "relevance"
                elif color != ColorGroup.UNKNOWN:
                    targets = [
                        b for b in current.intent.branches if not categories or b.category in categories
                    ]
                    # Replace a conflicting source color rather than AND it.
                    if categories or re.search(r"нет|теперь", lower) or any(b.colors for b in targets):
                        for branch in targets:
                            branch.colors = [color.value]
                            branch.origins["colors"] = "chat"
                        filters.colors = []
                        intent_change = True
                        action = "MODIFY_CURRENT"
                    else:
                        filters.colors = [color]
                elif price:
                    filters.max_price = int(price[1].replace(" ", ""))
                elif re.search(r"(?:от|минимум)\s*(\d+)\s*шт", lower):
                    filters.minimum_quantity = int(re.search(r"(?:от|минимум)\s*(\d+)\s*шт", lower)[1])
                elif materials:
                    if re.search(r"убери|без|исключ", lower):
                        filters.excluded_materials = [materials]
                    else:
                        filters.required_terms = [materials]
                elif parsed:
                    return await self.create(project_id, message, owner)
            filters.origins.update({key: "chat" for key in filters.model_fields_set if key != "origins"})
            if intent_change:
                current.intent.categories = [b.category for b in current.intent.branches]
                changes["intent"] = current.intent
            r = await self.research.grid_change(
                current.id,
                version=current.view.version,
                filters=filters if not changes.get("restore") else None,
                preferences=current.view.preferences
                if re.search(r"сначала|желательно|предпочтительно|лучше", lower)
                else None,
                message=message,
                origin="chat",
                **changes,
            )
            async with self.sessions.begin() as db:
                for message in r.messages[-2:]:
                    db.add(
                        Message(
                            id=str(uuid4()),
                            chat_id=r.chat_id,
                            role=message["role"],
                            content=message["content"],
                        )
                    )
            return {"research": r, "action": action}

    async def recover(self):
        # Retire assembly jobs only; leave the VTO catalog queue and data intact.
        async with self.sessions.begin() as db:
            await db.execute(
                update(HardRequestRecord)
                .where(HardRequestRecord.status.in_(["RUNNING", "PENDING", "QUEUED", "STALE_ASSEMBLY"]))
                .values(status="ARCHIVED")
            )
