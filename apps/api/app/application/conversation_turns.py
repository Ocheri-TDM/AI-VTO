"""Durable accepted turns are independent of the currently visible search."""

import asyncio
from uuid import uuid4

from sqlalchemy import func, select, update

from app.database.models import ConversationTurn, Message, Project, ResearchRecord
from app.domain.models import utcnow
from app.domain.query_branches import branch_label


def title_for(intent):
    if intent.discovery:
        raw = intent.original_query or intent.raw_query
        import re

        purpose = re.search(r"(?:мерч|подарк\w*|что[- ]?(?:то|нибудь))?.*?\bдля\s+([^.;]+)", raw, re.I)
        if purpose:
            tail = re.split(r"\s+(?:до|от|тираж|бюджет|цвет)|,", purpose[1], maxsplit=1, flags=re.I)[
                0
            ].strip()
            return (("Мерч" if re.search("мерч", raw, re.I) else "Подарки") + " для " + tail)[:100]
        return "Подборка подарков"
    branches = [
        b for b in sorted(intent.branches, key=lambda b: b.order) if not b.category.startswith("dynamic:")
    ]
    if not branches:
        return None
    labels = list(dict.fromkeys(branch_label(b) for b in branches))
    labels = [label if i == 0 else label[:1].lower() + label[1:] for i, label in enumerate(labels)]
    value = (", ".join(labels[:-1]) + " и " + labels[-1]) if 1 < len(labels) <= 3 else ", ".join(labels[:3])
    if len(labels) > 3:
        value += " и ещё " + str(len(labels) - 3)
    return value[:100]


async def maybe_title(db, project, chat_id):
    if project.name_source != "default":
        return
    turn = await db.scalar(
        select(ConversationTurn)
        .where(ConversationTurn.chat_id == chat_id, ConversationTurn.title_candidate.is_not(None))
        .order_by(ConversationTurn.request_epoch)
        .limit(1)
    )
    if turn is None:
        return
    pending = await db.scalar(
        select(func.min(ConversationTurn.request_epoch)).where(
            ConversationTurn.chat_id == chat_id, ConversationTurn.status == "ACCEPTED"
        )
    )
    if pending is not None and pending < turn.request_epoch:
        return
    await db.execute(
        update(Project)
        .where(
            Project.id == project.id,
            Project.name_source == "default",
            Project.name_version == project.name_version,
        )
        .values(name=turn.title_candidate, name_source="auto", name_version=Project.name_version + 1)
    )


async def complete(service, project_id, identifier, result, context, status, error=None):
    async with service.lock(project_id) as db:
        turn = await db.get(ConversationTurn, identifier)
        if turn.assistant_message_id:
            return
        research = result.get("research") if result else context.get("research")
        if research:
            # A stale new search was persisted as an archived query, never active.
            row = await db.get(ResearchRecord, research.id)
            turn.research_id = research.id if row else None
            turn.title_candidate = title_for(research.intent)
        content = (
            research.messages[-1]["content"]
            if research and research.messages and research.messages[-1]["role"] == "assistant"
            else {
                "CANCELLED": "Поиск прерван. Сообщение сохранено.",
                "INTERRUPTED": "Выполнение запроса было прервано. Сообщение сохранено; отправьте новый запрос для повторного поиска.",
                "SUPERSEDED": "Принят более поздний запрос. Активная выдача сохранена.",
                "FAILED": "Не удалось выполнить поиск. Сообщение сохранено; проверьте условия или повторите действие.",
            }.get(status, "Запрос обработан.")
        )
        if status == "SUPERSEDED" and research:
            content += " Активной остаётся выдача более позднего запроса."
        answer = Message(
            id=str(uuid4()),
            chat_id=turn.chat_id,
            role="assistant",
            content=content,
            operation_id=turn.id,
            project_query_id=context.get("query_id"),
            result_revision=research.revision if research else None,
            structured_operation={
                "type": "CONVERSATION_ANSWER",
                "status": status,
                "research_id": turn.research_id,
            },
        )
        db.add(answer)
        await db.flush()
        user_message = await db.get(Message, turn.user_message_id)
        user_message.project_query_id = context.get("query_id")
        user_message.result_revision = research.revision if research else None
        turn.assistant_message_id = answer.id
        turn.status, turn.error_code, turn.completed_at = status, error, utcnow()
        turn.action = result.get("action") if result else None
        await maybe_title(db, await db.get(Project, project_id), turn.chat_id)


async def run_request(service, project_id, message, owner, request_id=None):
    from app.application.project_queries import _request_epoch, _turn_result

    project = await service.project(project_id, owner)
    if not project:
        return None
    chat_id = project[1].id
    key = str(request_id or uuid4())
    async with service.lock(project_id) as db:
        turn = await db.scalar(
            select(ConversationTurn).where(
                ConversationTurn.chat_id == chat_id, ConversationTurn.request_key == key
            )
        )
        if turn:
            original = await db.get(Message, turn.user_message_id)
            if original.content != message:
                raise ValueError("IDEMPOTENCY_CONFLICT")
            replay = {
                "id": turn.id,
                "status": turn.status,
                "request_id": key,
                "user_message_id": turn.user_message_id,
            }
            research_id = turn.research_id
            status = turn.status
            error = turn.error_code
            active = turn.request_epoch == (await db.get(Project, project_id)).request_epoch
        else:
            p = await db.get(Project, project_id)
            p.request_epoch += 1
            epoch = p.request_epoch
            identifier = str(uuid4())
            user_message = Message(
                id=str(uuid4()),
                chat_id=chat_id,
                role="user",
                content=message,
                operation_id=identifier,
                structured_operation={"type": "CONVERSATION_REQUEST", "request_id": key},
            )
            db.add(user_message)
            await db.flush()
            turn = ConversationTurn(
                id=identifier,
                chat_id=chat_id,
                request_key=key,
                request_epoch=epoch,
                user_message_id=user_message.id,
                status="ACCEPTED",
            )
            db.add(turn)
            replay = None
    if replay:
        if status in ("FAILED", "SUPERSEDED", "CANCELLED", "INTERRUPTED"):
            raise ValueError(error or "REQUEST_FAILED")
        research = await service.research.repository.get(research_id, hydrate=False) if research_id else None
        if research:
            await service.research.grid_prepare_response(research)
        return {"research": research, "turn": replay, "action": "REPLAY", "active": active}
    token = _request_epoch.set(epoch)
    context = {}
    ctx = _turn_result.set(context)
    try:
        result = await service._request(project_id, message, owner)
        await complete(service, project_id, identifier, result, context, "COMPLETED")
        return result | {
            "turn": {
                "id": identifier,
                "request_id": key,
                "status": "COMPLETED",
                "user_message_id": user_message.id,
            },
            "active": True,
        }
    except asyncio.CancelledError:
        await asyncio.shield(
            complete(service, project_id, identifier, None, context, "CANCELLED", "REQUEST_CANCELLED")
        )
        raise
    except Exception as exc:
        superseded = isinstance(exc, ValueError) and str(exc) == "STATE_CONFLICT"
        await complete(
            service,
            project_id,
            identifier,
            None,
            context,
            "SUPERSEDED" if superseded else "FAILED",
            str(exc) if isinstance(exc, ValueError) else type(exc).__name__,
        )
        raise
    finally:
        _request_epoch.reset(token)
        _turn_result.reset(ctx)
