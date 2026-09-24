import asyncio
import base64
import hashlib
import json
import time
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from app.application.availability import ProcurementAvailabilityService
from app.application.research_analysis import FacetService, ProductGroupingService, current_products, stale
from app.domain.research import ResearchBudget, ResearchFilters
from app.domain.source_urls import validated_product_url

router = APIRouter(prefix='/api/researches', tags=['research'])


class StartResearch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    chat_id: UUID
    query: str = Field(min_length=1, max_length=2000)
    budget: ResearchBudget | None = None


class ChangeResearch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version: int = Field(ge=0)
    filters: ResearchFilters | None = None
    sorting: Literal['relevance', 'price_asc', 'price_desc'] | None = None
    selected_ids: list[str] | None = None
    restore: Literal['undo', 'reset'] | None = None


class ResearchMessage(BaseModel):
    model_config = ConfigDict(extra='forbid')
    message: str = Field(min_length=1, max_length=2000)


def product_view(p):
    # Explicit user-facing contract keeps supplier, stock, article and provenance secondary.
    return p.model_dump(mode='json', include={'id', 'name', 'category', 'description', 'price_kzt',
        'colors', 'primary_image', 'images', 'material', 'brand', 'dimensions', 'capacity', 'features'}) | {
        'freshness': 'STALE' if stale(p) or p.metadata.get('research_invalid') else 'FRESH',
        'match': p.metadata.get('color_match', 'exact'),
        'availability': ProcurementAvailabilityService().presentation(p.availability_records),
    }


def product_details_view(research, product):
    group = next(
        (g for g in ProductGroupingService().groups(research.products) if product.id in g["offer_ids"]),
        {"offer_ids": [product.id]},
    )
    by_id = {item.id: item for item in research.products}
    offers = []
    for offer_id in group["offer_ids"]:
        item = by_id.get(offer_id)
        if item and (url := validated_product_url(item.supplier, item.source_url)):
            offers.append({"supplier": item.supplier, "product_url": url})
    result = product_view(product)
    result.update({
        "supplier": product.supplier,
        "stock_quantity": product.stock_quantity,
        "incoming_quantity": product.incoming_quantity,
        "incoming_date": product.incoming_date.isoformat() if product.incoming_date else None,
        "original_price": str(product.original_price) if product.original_price is not None else None,
        "original_currency": product.original_currency,
        "fetched_at": product.fetched_at.isoformat(),
        "availability": ProcurementAvailabilityService().presentation(product.availability_records),
    })
    if offers:
        result["source_url"] = offers[0]["product_url"]
        result["offers"] = offers
    return result


def page_products(research, products, cursor=None, limit=50):
    fingerprint = hashlib.sha256((str(research.view.version) + '|'.join(p.id for p in products)).encode()).hexdigest()[:16]
    offset = 0
    if cursor:
        try:
            token, offset = json.loads(base64.urlsafe_b64decode(cursor.encode()))
            if token != fingerprint or not isinstance(offset, int) or offset < 0:
                raise ValueError()
        except (ValueError, TypeError, UnicodeError):
            raise HTTPException(409, 'Подборка изменилась. Загрузите первую страницу.')
    page = products[offset:offset+limit]
    next_cursor = base64.urlsafe_b64encode(json.dumps([fingerprint, offset+limit]).encode()).decode() if offset+limit < len(products) else None
    return {'products': [product_view(p) for p in page], 'matching_count': len(products), 'next_cursor': next_cursor}


def response(research):
    started = time.perf_counter()
    products = current_products(research)
    research.timings['ranking_ms'] = round((time.perf_counter()-started)*1000,2)
    valid_pool = [p for p in research.products if not p.metadata.get('research_invalid')]
    suppliers = sorted({b.supplier for b in research.coverage})
    completed = sum(all(b.status == 'COMPLETE' for b in research.coverage if b.supplier == supplier)
                    for supplier in suppliers)
    facet_started = time.perf_counter()
    facets = FacetService().build(valid_pool)
    research.timings['facet_ms'] = round((time.perf_counter()-facet_started)*1000,2)
    research.timings['facet_ranking_response_ms'] = round((time.perf_counter()-started)*1000,2)
    if products and research.job_status in ('QUEUED', 'RUNNING'):
        result_state = 'PARTIAL_RESULTS'
    elif products:
        result_state = 'INDEX_RESULTS'
    elif research.job_status == 'QUEUED':
        result_state = 'NO_RESULTS_YET'
    elif research.job_status == 'RUNNING':
        result_state = 'TARGETED_SEARCHING'
    elif research.job_status in ('PARTIAL', 'FAILED'):
        result_state = 'SUPPLIER_ERROR'
    else:
        result_state = 'COMPLETED_NO_MATCHES'
    return {'id': research.id, 'chat_id': research.chat_id, 'intent': research.intent.model_dump(mode='json'),
            'source_mode':research.source_mode, 'timings':research.timings,
            **page_products(research, products), 'pool_count': len(valid_pool),
            'new_matches_count': len(research.pending_offer_ids),
            'selected_products': [product_view(p) for p in research.products if p.id in research.view.selected_ids],
            'view': research.view.model_dump(mode='json'), 'status': research.job_status,
            'result_state': result_state,
            'coverage': [b.model_dump(mode='json', exclude={'cursor'}) for b in research.coverage],
            'completed_sources': completed, 'source_count': len(suppliers),
            'facets': facets,
            'groups': ProductGroupingService().groups(products[:50]),
            'stale_count': sum(stale(p) for p in research.products),
            'messages': research.messages, 'can_undo': bool(research.history), 'revision': research.revision}


async def require(request, identifier):
    service = request.app.state.research
    value = await service.sync(str(identifier)) if hasattr(service,'sync') else await service.get(str(identifier))
    if value is None:
        raise HTTPException(404, 'Исследование не найдено.')
    user_id = getattr(getattr(request.state, "user", None), "id", None)
    if user_id is not None and not await request.app.state.conversations.get(value.chat_id, user_id):
        raise HTTPException(404, "Research not found")
    return value


@router.post('', status_code=202)
async def start(body: StartResearch, request: Request):
    user_id = getattr(getattr(request.state, "user", None), "id", None)
    if not await request.app.state.conversations.get(str(body.chat_id), user_id):
        raise HTTPException(404, 'Проект не найден.')
    if sum(not task.done() for task in request.app.state.research.tasks.values()) >= request.app.state.research.settings.max_active_searches:
        raise HTTPException(429, 'Исследования уже выполняются. Попробуйте позже.')
    value = await request.app.state.research.start(str(body.chat_id), body.query, body.budget)
    execution = await request.app.state.conversations.begin(str(body.chat_id), body.query)
    await request.app.state.conversations.finish(execution, 'Исследование каталогов запущено. Результаты появятся по мере проверки.',
        {'action': 'START_RESEARCH', 'intent': value.intent.model_dump(mode='json')},
        [{'tool': 'start_research', 'research_id': value.id}], None)
    return response(value)


@router.get('/chat/{chat_id}')
async def latest(chat_id: UUID, request: Request):
    user_id = getattr(getattr(request.state, "user", None), "id", None)
    if not await request.app.state.conversations.get(str(chat_id), user_id):
        raise HTTPException(404, "Research not found")
    value = await request.app.state.research.repository.latest(str(chat_id))
    if value and value.source_mode=='index':
        value = await request.app.state.research.sync(value.id)
    return response(value) if value else None


@router.get('/{identifier}')
async def get(identifier: UUID, request: Request):
    return response(await require(request, identifier))


@router.patch('/{identifier}/view')
async def change(identifier: UUID, body: ChangeResearch, request: Request):
    await require(request, identifier)
    try:
        value = await request.app.state.research.change(str(identifier), **body.model_dump(exclude_none=True,
            exclude={'filters'}), filters=body.filters)
    except ValueError as exc:
        raise HTTPException(409 if str(exc) == 'STATE_CONFLICT' else 422,
                            'Подборка изменилась. Обновите состояние.' if str(exc) == 'STATE_CONFLICT'
                            else 'Товар отсутствует в исследовании.') from exc
    return response(value)


@router.post('/{identifier}/messages')
async def message(identifier: UUID, body: ResearchMessage, request: Request):
    current = await require(request, identifier)
    service = request.app.state.research
    if hasattr(service,'independent_query') and service.independent_query(body.message,current):
        result = await start(StartResearch(chat_id=UUID(current.chat_id),query=body.message),request)
        return result | {'assistant_message':f'Найдено {result["pool_count"]} вариантов.', 'action':'START_RESEARCH'}
    try:
        value, decision, answer = await request.app.state.orchestrator.execute_research(str(identifier), body.message)
    except TimeoutError as exc:
        raise HTTPException(503, 'Не удалось завершить уточнение. Подборка сохранена.') from exc
    return response(value) | {'assistant_message': answer, 'action': decision.action}


@router.post('/{identifier}/continue', status_code=202)
async def resume(identifier: UUID, request: Request):
    await require(request, identifier)
    return response(await request.app.state.research.resume(str(identifier)))


@router.post('/{identifier}/cancel')
async def cancel(identifier: UUID, request: Request):
    await require(request, identifier)
    return response(await request.app.state.research.cancel(str(identifier)))


@router.get('/{identifier}/products/{product_id:path}')
async def details(identifier: UUID, product_id: str, request: Request):
    research = await require(request, identifier)
    product = next((p for p in research.products if p.id == product_id), None)
    if not product:
        raise HTTPException(404, 'Товар не найден.')
    await request.app.state.index.mark_usage([product.id], "opened")
    if request.app.state.research.settings.debug_agent:
        return product.model_dump(mode='json')
    return product_details_view(research, product)


@router.get('/{identifier}/products')
async def products_page(identifier: UUID, request: Request, cursor: str | None = None, limit: int = Query(50, ge=1, le=100)):
    value = await require(request, identifier)
    return page_products(value, current_products(value), cursor, limit)


@router.post('/{identifier}/new-matches')
async def accept_matches(identifier: UUID, request: Request):
    await require(request, identifier)
    return response(await request.app.state.research.accept_matches(str(identifier)))


@router.get('/{identifier}/events')
async def events(identifier: UUID, request: Request):
    await require(request, identifier)
    async def stream():
        previous = None
        while not await request.is_disconnected():
            value = await require(request, identifier)
            state = {'id': value.id, 'version': value.view.version, 'status': value.job_status,
                     'matching_count': len(current_products(value)), 'new_matches_count': len(value.pending_offer_ids),
                     'fresh_ids_hash': hashlib.sha256('|'.join(value.indexed_offer_ids).encode()).hexdigest()[:16]}
            state['observation_hash'] = hashlib.sha256('|'.join(f'{p.id}:{p.price_kzt}:{p.fetched_at}' for p in value.products).encode()).hexdigest()[:16]
            if state != previous:
                yield 'event: research.updated\ndata: ' + json.dumps(state, ensure_ascii=False) + '\n\n'
                if state['new_matches_count'] and (not previous or previous['new_matches_count'] != state['new_matches_count']):
                    yield 'event: new_matches.available\ndata: ' + json.dumps({'count': state['new_matches_count']}) + '\n\n'
                previous = state
            if value.job_status not in ('QUEUED', 'RUNNING'):
                yield 'event: research.completed\ndata: {}\n\n'
                break
            await asyncio.sleep(1)
    return StreamingResponse(stream(), media_type='text/event-stream', headers={'Cache-Control': 'no-cache'})
