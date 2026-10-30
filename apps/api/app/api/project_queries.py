from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.research import render

router = APIRouter(tags=["project queries"])


class QueryCreate(BaseModel):
    query: str = Field(min_length=1, max_length=2000)


def owner(request):
    return getattr(getattr(request.state, "user", None), "id", None)


@router.get("/api/projects/{project_id}/queries")
async def history(project_id: UUID, request: Request):
    value = await request.app.state.project_queries.list_queries(str(project_id), owner(request))
    if value is None:
        raise HTTPException(404, "Project not found")
    return value


@router.post("/api/projects/{project_id}/requests", status_code=202)
@router.post("/api/projects/{project_id}/queries", status_code=201)
async def submit(project_id: UUID, body: QueryCreate, request: Request):
    try:
        result = await request.app.state.project_queries.request(str(project_id), body.query, owner(request))
    except ValueError as exc:
        if str(exc) == "STATE_CONFLICT":
            raise HTTPException(409, "Подборка изменилась. Повторите уточнение.") from exc
        raise
    if result is None:
        raise HTTPException(404, "Project not found")
    return {
        "mode": "GRID",
        "route": {"action": result["action"]},
        "query": result.get("query"),
        "research": await render(request, result["research"]),
    }


@router.post("/api/projects/{project_id}/queries/{query_id}/activate")
async def activate(project_id: UUID, query_id: UUID, request: Request):
    result = await request.app.state.project_queries.activate(str(project_id), str(query_id), owner(request))
    if result is None:
        raise HTTPException(404, "Query not found")
    return {"mode": "GRID", "query": result["query"], "research": await render(request, result["research"])}


@router.post("/api/hard-requests/{path:path}", status_code=410)
@router.post("/api/projects/{project_id}/hard-requests", status_code=410)
async def retired(request: Request, path: str = "", project_id: str = ""):
    raise HTTPException(410, "Автоматическая сборка удалена. Используйте товарный поиск проекта.")
