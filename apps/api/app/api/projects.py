from contextlib import AsyncExitStack
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

router = APIRouter(prefix="/api/projects", tags=["projects"])


class ProjectUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=300)
    client_name: str | None = Field(default=None, max_length=300)

    @field_validator("name")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Project name required")
        return value.strip()


@router.get("")
async def projects(request: Request):
    user_id = getattr(getattr(request.state, "user", None), "id", None)
    return await request.app.state.conversations.projects(user_id)


@router.patch("/{project_id}")
async def update(project_id: UUID, body: ProjectUpdate, request: Request):
    user_id = getattr(getattr(request.state, "user", None), "id", None)
    if not await request.app.state.conversations.update_project(str(project_id), body.model_dump(), user_id):
        raise HTTPException(404, "Project not found")
    return {"id": str(project_id), **body.model_dump()}


@router.delete("/{project_id}", status_code=204)
async def delete(project_id: UUID, request: Request):
    agent = request.app.state.orchestrator
    user_id = getattr(getattr(request.state, "user", None), "id", None)
    projects = await agent.conversations.projects(user_id)
    chats = [p["chat_id"] for p in projects if p["id"] == str(project_id)]
    if any(agent.locks[c].locked() for c in chats):
        raise HTTPException(409, "Project busy")
    async with AsyncExitStack() as stack:
        for chat in sorted(chats):
            await stack.enter_async_context(agent.locks[chat])
        if not await agent.conversations.delete_project(str(project_id), user_id):
            raise HTTPException(404, "Project not found")
    return Response(status_code=204)
