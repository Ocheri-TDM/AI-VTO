"""Conversation metadata persists; product observations and view history expire."""

from uuid import uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database.models import (
    AgentExecution,
    Chat,
    Message,
    Project,
    SearchSession,
    SearchStateHistory,
    User,
)
from app.domain.errors import SearchExpiredError
from app.domain.models import SearchIntent, SearchSnapshot, utcnow
from app.domain.search_state import SearchState


class ConversationRepository:
    def __init__(self, sessions: async_sessionmaker):
        self.sessions = sessions

    async def create(self, name: str = "Новый проект", user_id: str | None = None) -> str:
        async with self.sessions.begin() as db:
            if user_id is None:
                user_id = "00000000-0000-0000-0000-000000000001"
                if await db.get(User, user_id) is None:
                    db.add(User(id=user_id, display_name="legacy", normalized_name="__legacy_test__", password_hash="!", created_at=utcnow(), updated_at=utcnow()))
                    await db.flush()
            project = Project(id=str(uuid4()), name=name, user_id=user_id)
            db.add(project)
            await db.flush()
            chat = Chat(id=str(uuid4()), project_id=project.id, user_id=user_id)
            db.add(chat)
        return chat.id

    async def get(self, chat_id: str, user_id: str | None = None) -> dict | None:
        async with self.sessions() as db:
            chat = await db.get(Chat, chat_id)
            if chat is None or user_id is not None and chat.user_id != user_id:
                return None
            project = await db.get(Project, chat.project_id) if chat.project_id else None
            messages = list(
                reversed(
                    (
                        await db.scalars(
                            select(Message)
                            .where(Message.chat_id == chat_id)
                            .order_by(Message.created_at.desc(), Message.id.desc())
                            .limit(50)
                        )
                    ).all()
                )
            )
            executions = (
                await db.scalars(
                    select(AgentExecution)
                    .where(AgentExecution.chat_id == chat_id)
                    .order_by(AgentExecution.started_at.desc())
                    .limit(3)
                )
            ).all()
            return {
                "id": chat.id,
                "project": {"id": project.id, "name": project.name, "client_name": project.client_name}
                if project
                else None,
                "search_session_id": chat.active_search_session_id,
                "last_intent": chat.last_intent,
                "messages": [
                    {"id": m.id, "role": m.role, "content": m.content, "created_at": m.created_at.isoformat()}
                    for m in messages
                ],
                "decisions": [e.decision for e in executions if e.decision],
            }

    async def begin(self, chat_id: str, content: str) -> str:
        async with self.sessions.begin() as db:
            message = Message(id=str(uuid4()), chat_id=chat_id, role="user", content=content)
            db.add(message)
            await db.flush()
            execution = AgentExecution(id=str(uuid4()), chat_id=chat_id, user_message_id=message.id)
            db.add(execution)
        return execution.id

    async def can_undo(self, session_id: str | None) -> bool:
        if not session_id:
            return False
        async with self.sessions() as db:
            return bool(
                await db.scalar(
                    select(func.count())
                    .select_from(SearchStateHistory)
                    .where(SearchStateHistory.search_session_id == session_id)
                )
            )

    async def projects(self, user_id: str | None = None) -> list[dict]:
        async with self.sessions() as db:
            rows = (
                await db.execute(
                    select(Project, Chat, func.max(Message.created_at))
                    .join(Chat, Chat.project_id == Project.id)
                    .outerjoin(Message, Message.chat_id == Chat.id)
                    .where(Project.user_id == user_id if user_id is not None else Project.user_id.is_not(None))
                    .group_by(Project.id, Chat.id)
                )
            ).all()
            result = [
                {
                    "id": p.id,
                    "name": p.name,
                    "client_name": p.client_name,
                    "chat_id": c.id,
                    "updated_at": (activity or p.created_at).isoformat(),
                    "summary": (c.last_intent or {}).get("raw_query", "")[:160],
                }
                for p, c, activity in rows
            ]
            return sorted(result, key=lambda p: p["updated_at"], reverse=True)

    async def update_project(self, project_id: str, values: dict, user_id: str | None = None) -> bool:
        async with self.sessions.begin() as db:
            project = await db.get(Project, project_id)
            if project is None or user_id is not None and project.user_id != user_id:
                return False
            for key, value in values.items():
                setattr(project, key, value)
            return True

    async def delete_project(self, project_id: str, user_id: str | None = None) -> bool:
        async with self.sessions.begin() as db:
            project = await db.get(Project, project_id)
            if project is None or user_id is not None and project.user_id != user_id:
                return False
            await db.execute(delete(Chat).where(Chat.project_id == project_id))
            await db.delete(project)
            return True

    async def bind(self, chat_id: str, snapshot: SearchSnapshot) -> None:
        async with self.sessions.begin() as db:
            chat = await db.get(Chat, chat_id)
            chat.active_search_session_id = snapshot.id
            chat.last_intent = snapshot.intent.model_dump(mode="json")

    async def finish(
        self,
        execution_id: str,
        content: str,
        decision: dict,
        calls: list[dict],
        session_id: str | None,
        *,
        status: str = "completed",
    ) -> None:
        async with self.sessions.begin() as db:
            execution = await db.get(AgentExecution, execution_id)
            execution.decision, execution.tool_calls = decision, calls
            execution.status, execution.completed_at = status, utcnow()
            db.add(
                Message(
                    chat_id=execution.chat_id, role="assistant", content=content, search_session_id=session_id
                )
            )

    async def change(
        self, snapshot: SearchSnapshot, state: SearchState, *, undo: bool = False
    ) -> SearchState:
        """Atomic view version + history update. Pool is not rewritten for filters."""
        state = SearchState.model_validate(state.model_dump())
        async with self.sessions.begin() as db:
            record = await db.get(SearchSession, snapshot.id, with_for_update=True)
            if record is None:
                raise SearchExpiredError(snapshot.id)
            current = SearchState.model_validate(record.state)
            if current.version != SearchState.model_validate(snapshot.state).version:
                raise ValueError("STATE_CONFLICT")
            if undo:
                previous = await db.scalar(
                    select(SearchStateHistory)
                    .where(SearchStateHistory.search_session_id == snapshot.id)
                    .order_by(SearchStateHistory.version.desc())
                    .limit(1)
                )
                if previous is None:
                    return current
                state = SearchState.model_validate(previous.snapshot["state"])
                snapshot.intent = SearchIntent.model_validate(previous.snapshot["intent"])
                await db.delete(previous)
            else:
                db.add(
                    SearchStateHistory(
                        search_session_id=snapshot.id,
                        version=current.version,
                        snapshot={"state": current.model_dump(mode="json"), "intent": record.intent},
                    )
                )
            state.version = current.version + 1
            record.state, record.intent = (
                state.model_dump(mode="json"),
                snapshot.intent.model_dump(mode="json"),
            )
            await db.flush()
            keep = list(
                (
                    await db.scalars(
                        select(SearchStateHistory.version)
                        .where(SearchStateHistory.search_session_id == snapshot.id)
                        .order_by(SearchStateHistory.version.desc())
                        .limit(20)
                    )
                ).all()
            )
            if keep:
                await db.execute(
                    delete(SearchStateHistory).where(
                        SearchStateHistory.search_session_id == snapshot.id,
                        SearchStateHistory.version.not_in(keep),
                    )
                )
        snapshot.state = state.model_dump(mode="json")
        return state

    async def recover_interrupted(self) -> None:
        async with self.sessions.begin() as db:
            for e in (
                await db.scalars(select(AgentExecution).where(AgentExecution.status == "running"))
            ).all():
                e.status, e.completed_at = "interrupted", utcnow()

    async def collapse_history(self, session_id: str, starting_version: int) -> None:
        # Several tools can form one user action; undo returns to its initial state.
        async with self.sessions.begin() as db:
            await db.execute(
                delete(SearchStateHistory).where(
                    SearchStateHistory.search_session_id == session_id,
                    SearchStateHistory.version > starting_version,
                )
            )
