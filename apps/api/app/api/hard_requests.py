import asyncio
import json
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from app.api.research import response as research_response
from app.domain.taxonomy import UniversalCategoryResolver

router = APIRouter(tags=["project queries", "hard requests"])


class QueryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=2000)


class HardCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request: str = Field(min_length=1, max_length=5000)


class UniversalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=5000)


class CandidateSelect(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_id: UUID


def user_id(request):
    return getattr(getattr(request.state, "user", None), "id", None)


@router.get("/api/projects/{project_id}/queries")
async def query_history(project_id: UUID, request: Request):
    result = await request.app.state.hard_requests.list_queries(str(project_id), user_id(request))
    if result is None:
        raise HTTPException(404, "Project not found")
    return result


@router.post("/api/projects/{project_id}/queries", status_code=201)
async def create_query(project_id: UUID, body: QueryCreate, request: Request):
    result = await request.app.state.hard_requests.create_standard_query(
        str(project_id), body.query, user_id(request)
    )
    if result is None:
        raise HTTPException(404, "Project not found")
    return {"query": result["query"], "research": research_response(result["research"])}


@router.post("/api/projects/{project_id}/requests", status_code=202)
async def universal_request(project_id: UUID, body: UniversalRequest, request: Request):
    service = request.app.state.hard_requests
    owner = await request.app.state.conversations.projects(user_id(request))
    project = next((value for value in owner if value["id"] == str(project_id)), None)
    if not project:
        raise HTTPException(404, "Project not found")
    queries = await service.list_queries(str(project_id), user_id(request)) or []
    active = next((value for value in queries if value["is_active"]), None)
    current_research = None
    current_subjects = []
    if active and active["type"] == "STANDARD" and active["research_session_id"]:
        current_research = await request.app.state.research.get(active["research_session_id"])
        if current_research:
            current_subjects = list(current_research.intent.categories)
    elif active and active["type"] == "HARD":
        hard = await service.active_hard(str(project_id), user_id(request))
        if hard:
            resolver = UniversalCategoryResolver()
            current_subjects = list(dict.fromkeys(
                concept
                for category in hard["plan"].get("categories", [])
                for concept in resolver.resolve(category)
            ))
    route = await request.app.state.conversation_router.route(body.query, current_subjects)
    if current_research and route.action != "NEW_SEARCH":
        value, _decision, _answer = await request.app.state.orchestrator.execute_research(
            current_research.id, body.query
        )
        value.timings["conversation_router"] = route.model_dump(mode="json")
        await request.app.state.research.repository.save(value)
        return {
            "mode": "REFINEMENT",
            "route": route.model_dump(mode="json"),
            "research": research_response(value),
        }
    if active and active["type"] == "HARD" and route.action != "NEW_SEARCH":
        patched = await service.patch_active(str(project_id), body.query, user_id(request))
        if patched is not None:
            return {
                "mode": "COMPOSITE",
                "route": route.model_dump(mode="json"),
                "hard_request": patched,
            }
    plan = await service.parse(body.query)
    if len(plan.categories) >= 2:
        value = await service.create_hard(str(project_id), body.query, user_id(request), plan)
        return {"mode": "COMPOSITE", "route": route.model_dump(mode="json"), "hard_request": value}
    patched = await service.patch_active(str(project_id), body.query, user_id(request))
    if patched is not None:
        return {"mode": "COMPOSITE", "route": route.model_dump(mode="json"), "hard_request": patched}
    intent = await request.app.state.research.planner.parse(body.query)
    if plan.categories or intent.categories:
        value = await service.create_standard_query(str(project_id), body.query, user_id(request))
        return {
            "mode": "SIMPLE",
            "route": route.model_dump(mode="json"),
            "query": value["query"],
            "research": research_response(value["research"]),
        }
    current = await request.app.state.research.repository.latest(project["chat_id"])
    if current:
        value, _decision, _answer = await request.app.state.orchestrator.execute_research(
            current.id, body.query
        )
        return {"mode": "REFINEMENT", "route": route.model_dump(mode="json"), "research": research_response(value)}
    value = await service.create_standard_query(str(project_id), body.query, user_id(request))
    return {"mode": "SIMPLE", "route": route.model_dump(mode="json"), "query": value["query"], "research": research_response(value["research"])}


@router.get("/api/projects/{project_id}/messages")
async def project_messages(project_id: UUID, request: Request):
    result = await request.app.state.hard_requests.messages(str(project_id), user_id(request))
    if result is None:
        raise HTTPException(404, "Project not found")
    return result


@router.get("/api/projects/{project_id}/active-composite")
async def active_composite(project_id: UUID, request: Request):
    return await request.app.state.hard_requests.active_hard(str(project_id), user_id(request))


@router.post("/api/projects/{project_id}/queries/{query_id}/activate")
async def activate_query(project_id: UUID, query_id: UUID, request: Request):
    result = await request.app.state.hard_requests.activate(str(project_id), str(query_id), user_id(request))
    if result is None:
        raise HTTPException(404, "Project query not found")
    return result


@router.get("/api/projects/{project_id}/hard-requests")
async def hard_history(project_id: UUID, request: Request):
    result = await request.app.state.hard_requests.list_hard(str(project_id), user_id(request))
    if result is None:
        raise HTTPException(404, "Project not found")
    return result


@router.post("/api/projects/{project_id}/hard-requests", status_code=202)
async def create_hard(project_id: UUID, body: HardCreate, request: Request):
    result = await request.app.state.hard_requests.create_hard(
        str(project_id), body.request, user_id(request)
    )
    if result is None:
        raise HTTPException(404, "Project not found")
    return result


@router.get("/api/hard-requests/{request_id}")
async def get_hard(request_id: UUID, request: Request):
    result = await request.app.state.hard_requests.get(str(request_id), user_id(request))
    if result is None:
        raise HTTPException(404, "Hard request not found")
    return result


@router.get("/api/hard-requests/{request_id}/cases")
async def cases(
    request_id: UUID, request: Request, offset: int = Query(0, ge=0), limit: int = Query(10, ge=1, le=20)
):
    result = await request.app.state.hard_requests.cases(str(request_id), user_id(request), offset, limit)
    if result is None:
        raise HTTPException(404, "Hard request not found")
    return result


@router.post("/api/hard-requests/{request_id}/rebuild", status_code=202)
async def rebuild(request_id: UUID, request: Request):
    result = await request.app.state.hard_requests.rebuild(str(request_id), user_id(request))
    if result is None:
        raise HTTPException(404, "Hard request not found")
    return result


@router.post("/api/hard-requests/{request_id}/select")
async def select_candidate(request_id: UUID, body: CandidateSelect, request: Request):
    if not await request.app.state.hard_requests.select_candidate(
        str(request_id), str(body.candidate_id), user_id(request)
    ):
        raise HTTPException(404, "Candidate not found")
    return {"selected": str(body.candidate_id)}


@router.get("/api/hard-requests/{request_id}/events")
async def events(request_id: UUID, request: Request):
    if not await request.app.state.hard_requests.get(str(request_id), user_id(request)):
        raise HTTPException(404, "Hard request not found")

    async def stream():
        revision = -1
        original = await request.app.state.hard_requests.get(str(request_id), user_id(request))
        while True:
            value = await request.app.state.hard_requests.get(str(request_id), user_id(request))
            if not value:
                return
            active = await request.app.state.hard_requests.active_hard(
                original["project_id"], user_id(request)
            )
            if active and active["id"] != value["id"]:
                yield "event: hard-request.updated\ndata: " + json.dumps(active, ensure_ascii=False) + "\n\n"
                return
            if value["revision"] != revision:
                revision = value["revision"]
                yield "event: hard-request.updated\ndata: " + json.dumps(value, ensure_ascii=False) + "\n\n"
            if value["status"] in {"COMPLETED", "FAILED"} and not value["metrics"].get(
                "pending_availability_recovery"
            ):
                return
            await asyncio.sleep(0.75)

    return StreamingResponse(stream(), media_type="text/event-stream")
