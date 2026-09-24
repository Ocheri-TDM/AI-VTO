import asyncio
import json
from contextlib import suppress
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.application.orchestrator import ChatResponse, no_event
from app.application.session_tools import SessionTools
from app.application.tools import SelectInput
from app.domain.errors import SearchExpiredError
from app.domain.search_state import ViewFilters

router = APIRouter(prefix="/api/chats", tags=["conversations"])


class CreateChat(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_name: str = Field(default="Новый проект", min_length=1, max_length=300)

    @field_validator("project_name")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Project name required")
        return value.strip()


class MessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=2000)

    @field_validator("message")
    @classmethod
    def not_blank(cls, value):
        if not value.strip():
            raise ValueError("Empty message")
        return value.strip()


@router.post("", status_code=201)
async def create_chat(body: CreateChat, request: Request):
    user_id = getattr(getattr(request.state, "user", None), "id", None)
    return {"id": await request.app.state.conversations.create(body.project_name, user_id)}


@router.get("/{chat_id}")
async def get_chat(chat_id: UUID, request: Request):
    agent = request.app.state.orchestrator
    user_id = getattr(getattr(request.state, "user", None), "id", None)
    chat = await agent.conversations.get(str(chat_id), user_id)
    if chat is None:
        raise HTTPException(404, "Chat not found")
    snapshot = await agent.snapshot(chat)
    tools = SessionTools(str(chat_id), snapshot, agent.search, agent.conversations, no_event)
    chat.pop("decisions", None)
    result = agent.response(tools, "SHOW_RESULTS", "")
    result.can_undo = await agent.conversations.can_undo(result.search_session_id)
    chat["expired"] = snapshot is None and bool(chat.get("last_intent"))
    chat["current"] = result.model_dump(mode="json", exclude_none=True)
    return chat


class WorkspaceChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    state_version: int = Field(ge=0)
    filters: ViewFilters | None = None
    sorting: Literal["relevance", "price_asc", "price_desc", "darker"] | None = None
    restore: Literal["undo", "reset"] | None = None


@router.patch("/{chat_id}/workspace", response_model=ChatResponse)
async def workspace(chat_id: UUID, body: WorkspaceChange, request: Request):
    agent = request.app.state.orchestrator
    key = str(chat_id)
    await check_chat(agent, key, getattr(getattr(request.state, "user", None), "id", None))
    async with agent.locks[key]:
        chat = await agent.conversations.get(key)
        snapshot = await agent.snapshot(chat)
        if snapshot is None:
            raise HTTPException(410, "Search expired")
        tools = SessionTools(key, snapshot, agent.search, agent.conversations, no_event)
        if tools.state().version != body.state_version:
            raise HTTPException(409, "State changed")
        try:
            if body.restore:
                await tools.registry.call("restore_products", {"mode": body.restore})
            else:
                if body.filters is not None:
                    await tools.registry.call(
                        "filter_products",
                        {"filters": body.filters.model_dump(mode="json"), "mode": "replace"},
                    )
                if body.sorting is not None:
                    await tools.registry.call("sort_products", {"sorting": body.sorting})
            await agent.conversations.collapse_history(snapshot.id, body.state_version)
            await agent.conversations.bind(key, snapshot)
        except SearchExpiredError:
            raise HTTPException(410, "Search expired") from None
        except ValueError:
            raise HTTPException(422, "Invalid workspace state") from None
        result = agent.response(tools, "UPDATE_WORKSPACE", "Подборка обновлена.")
        result.can_undo = await agent.conversations.can_undo(snapshot.id)
        return result


async def check_chat(agent, chat_id, user_id=None):
    if agent.locks[chat_id].locked():
        raise HTTPException(409, "Chat busy")
    if await agent.conversations.get(chat_id, user_id) is None:
        raise HTTPException(404, "Chat not found")


@router.post("/{chat_id}/messages")
async def message(chat_id: UUID, body: MessageRequest, request: Request, legacy: bool = False):
    agent = request.app.state.orchestrator
    await check_chat(agent, str(chat_id), getattr(getattr(request.state, "user", None), "id", None))
    if not legacy:
        from app.api.research import ResearchMessage, StartResearch, start
        from app.api.research import message as refine
        current = await request.app.state.research.repository.latest(str(chat_id))
        if current is None or current.source_mode!='index':
            return await start(StartResearch(chat_id=chat_id,query=body.message),request)
        return await refine(UUID(current.id),ResearchMessage(message=body.message),request)
    return await agent.execute(str(chat_id), body.message)


@router.post("/{chat_id}/selection", response_model=ChatResponse, response_model_exclude_none=True)
async def selection(chat_id: UUID, body: SelectInput, request: Request):
    agent = request.app.state.orchestrator
    await check_chat(agent, str(chat_id), getattr(getattr(request.state, "user", None), "id", None))
    if await agent.snapshot(await agent.conversations.get(str(chat_id))) is None:
        raise HTTPException(410, "Search expired")
    return await agent.execute(str(chat_id), "Изменить выбор товаров", selection=body)


@router.post("/{chat_id}/messages/stream")
async def stream(chat_id: UUID, body: MessageRequest, request: Request, legacy: bool = False):
    agent = request.app.state.orchestrator
    await check_chat(agent, str(chat_id), getattr(getattr(request.state, "user", None), "id", None))
    if not legacy:
        async def indexed_events():
            yield 'event: agent.started\ndata: {}\n\n'
            result = await message(chat_id,body,request)
            yield 'event: agent.completed\ndata: '+json.dumps(result,ensure_ascii=False)+'\n\n'
        return StreamingResponse(indexed_events(),media_type='text/event-stream')

    async def events():
        queue = asyncio.Queue(maxsize=100)

        async def emit(name, data):
            await queue.put((name, data))

        task = asyncio.create_task(agent.execute(str(chat_id), body.message, emit=emit))
        try:
            while not task.done() or not queue.empty():
                try:
                    name, data = await asyncio.wait_for(queue.get(), timeout=10)
                    yield f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
                except TimeoutError:
                    yield ": heartbeat\n\n"
            await task
        finally:
            if not task.done():
                task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
