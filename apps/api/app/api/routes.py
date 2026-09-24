from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response

from app.api.schemas import FilterRequest, SearchRequest, SearchResponse
from app.application.search import SearchBusyError
from app.domain.errors import SearchExpiredError
from app.domain.models import BrowserAction, Product, SearchSnapshot

router = APIRouter(prefix="/api")


async def get_snapshot(request: Request, session_id: UUID) -> SearchSnapshot:
    try:
        snapshot = await request.app.state.repository.get(str(session_id))
    except SearchExpiredError:
        raise HTTPException(410, "Срок хранения подборки истёк. Запустите поиск заново.") from None
    if snapshot is None:
        raise HTTPException(404, "Подборка не найдена или уже удалена по TTL.")
    return snapshot


@router.post("/searches", response_model=SearchResponse, status_code=202, tags=["search"])
async def create_search(payload: SearchRequest, request: Request, response: Response):
    try:
        snapshot = await request.app.state.search.start(payload.query, refresh=payload.refresh)
    except SearchBusyError as exc:
        raise HTTPException(429, str(exc), headers={"Retry-After": "10"}) from None
    if snapshot.status not in ("queued", "running"):
        response.status_code = 200
    return SearchResponse.from_snapshot(snapshot)


@router.get("/searches/{session_id}", response_model=SearchResponse, tags=["search"])
async def read_search(session_id: UUID, request: Request):
    return SearchResponse.from_snapshot(await get_snapshot(request, session_id))


@router.post(
    "/searches/{session_id}/refresh", response_model=SearchResponse, status_code=202, tags=["search"]
)
async def refresh_search(session_id: UUID, request: Request):
    previous = await get_snapshot(request, session_id)
    try:
        snapshot = await request.app.state.search.start(
            previous.intent.raw_query, refresh=True, intent=previous.intent
        )
    except SearchBusyError as exc:
        raise HTTPException(429, str(exc), headers={"Retry-After": "10"}) from None
    return SearchResponse.from_snapshot(snapshot)


@router.post("/searches/{session_id}/filter", response_model=SearchResponse, tags=["search"])
async def filter_search(session_id: UUID, payload: FilterRequest, request: Request):
    snapshot = await get_snapshot(request, session_id)
    if snapshot.status in ("queued", "running"):
        raise HTTPException(409, "Дождитесь завершения поиска перед фильтрацией.")
    snapshot.products = [
        product
        for product in snapshot.products
        if (
            payload.max_price_kzt is None
            or product.price_kzt is not None
            and product.price_kzt <= payload.max_price_kzt
        )
        and (
            payload.colors is None or request.app.state.search.colors.matches(product.colors, payload.colors)
        )
    ]
    snapshot.cache_hit = True
    return SearchResponse.from_snapshot(snapshot)


@router.get("/searches/{session_id}/products/{product_id}", response_model=Product, tags=["products"])
async def product_details(session_id: UUID, product_id: str, request: Request):
    snapshot = await get_snapshot(request, session_id)
    product = next((p for p in snapshot.products if p.id == product_id), None)
    if product is None:
        raise HTTPException(404, "Товар не найден в этой подборке.")
    return product


@router.get("/searches/{session_id}/trace", response_model=list[BrowserAction], tags=["diagnostics"])
async def search_trace(session_id: UUID, request: Request):
    return (await get_snapshot(request, session_id)).traces
