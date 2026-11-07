"""Stable, bounded chat history. Active research and model context are unrelated."""

import base64
import json
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import and_, or_, select

from app.database.models import Chat, ConversationTurn, Message
from app.domain.models import utcnow


def cursor(chat_id, message):
    return base64.urlsafe_b64encode(
        json.dumps([chat_id, message.created_at.isoformat(), message.id]).encode()
    ).decode()


def position(value, chat_id):
    try:
        if len(value) > 256:
            raise ValueError()
        chat, stamp, identifier = json.loads(base64.urlsafe_b64decode(value.encode()))
        if chat != chat_id or not isinstance(stamp, str) or not isinstance(identifier, str):
            raise ValueError()
        return datetime.fromisoformat(stamp), str(UUID(identifier))
    except (ValueError, TypeError, UnicodeError, KeyError) as exc:
        raise ValueError("INVALID_MESSAGE_CURSOR") from exc


async def page(service, chat_id, owner, before=None, after=None, limit=50):
    if before and after:
        raise ValueError("INVALID_MESSAGE_CURSOR")
    async with service.sessions() as db:
        chat = await db.get(Chat, chat_id)
        if not chat or chat.user_id != owner:
            return None
        project_id = chat.project_id
        stale = (
            await db.scalars(
                select(ConversationTurn)
                .where(
                    ConversationTurn.chat_id == chat_id,
                    ConversationTurn.status == "ACCEPTED",
                    ConversationTurn.created_at < utcnow() - timedelta(minutes=5),
                )
                .order_by(ConversationTurn.created_at, ConversationTurn.id)
                .limit(50)
            )
        ).all()
    if stale:
        from app.application.conversation_turns import complete

        for turn in stale:
            await complete(service, project_id, turn.id, None, {}, "INTERRUPTED", "REQUEST_INTERRUPTED")
    async with service.sessions() as db:
        stmt = (
            select(Message, ConversationTurn)
            .outerjoin(ConversationTurn, ConversationTurn.id == Message.operation_id)
            .where(Message.chat_id == chat_id)
        )
        if before or after:
            stamp, identifier = position(before or after, chat_id)
            compare = (
                or_(Message.created_at > stamp, and_(Message.created_at == stamp, Message.id > identifier))
                if after
                else or_(
                    Message.created_at < stamp, and_(Message.created_at == stamp, Message.id < identifier)
                )
            )
            stmt = stmt.where(compare)
        stmt = (
            stmt.order_by(Message.created_at.asc(), Message.id.asc())
            if after
            else stmt.order_by(Message.created_at.desc(), Message.id.desc())
        )
        rows = (await db.execute(stmt.limit(limit + 1))).all()
        more = len(rows) > limit
        rows = rows[:limit]
        if not after:
            rows = list(reversed(rows))
        messages = [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "created_at": m.created_at.isoformat(),
                "operation_id": m.operation_id,
                "request_id": t.request_key if t else None,
                "status": t.status if t else "COMPLETED",
                "research_id": t.research_id if t else None,
                "project_query_id": m.project_query_id,
                "result_revision": m.result_revision,
                "origin": (m.structured_operation or {}).get("type", "LEGACY_CONVERSATION"),
            }
            for m, t in rows
        ]
        return {
            "chat_id": chat_id,
            "messages": messages,
            "next_before": cursor(chat_id, rows[0][0]) if more and not after and rows else None,
            "next_after": cursor(chat_id, rows[-1][0]) if rows else after,
            "has_more_after": bool(after and more),
        }
