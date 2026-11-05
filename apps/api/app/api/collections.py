from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.api.presentations import action, context, required

router = APIRouter(prefix="/api/projects", tags=["collection"])


class AddItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    offer_id: str = Field(min_length=1, max_length=200)
    source_research_id: UUID | None = None


class ClearCollection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=0)
    confirmed: bool


@router.get("/{project}/collection")
async def get(project: UUID, request: Request):
    _, user = context(request)
    return required(await request.app.state.collections.read(str(project), user))


@router.post("/{project}/collection/items")
async def add(project: UUID, body: AddItem, request: Request):
    _, user = context(request)
    return await action(
        request.app.state.collections.mutate(
            str(project),
            user,
            "add",
            body.offer_id,
            str(body.source_research_id) if body.source_research_id else None,
        )
    )


@router.delete("/{project}/collection/items/{offer_id}")
async def remove(project: UUID, offer_id: str, request: Request):
    _, user = context(request)
    return await action(request.app.state.collections.mutate(str(project), user, "remove", offer_id))


@router.post("/{project}/collection/clear")
async def clear(project: UUID, body: ClearCollection, request: Request):
    _, user = context(request)
    if not body.confirmed:
        raise HTTPException(422, "Подтвердите очистку подборки")
    return await action(
        request.app.state.collections.mutate(str(project), user, "clear", version=body.version)
    )
