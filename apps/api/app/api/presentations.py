"""Authenticated project document API. Storage paths are never client-controlled."""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse

from app.application.presentation_assets import MAX_BYTES, safe_path
from app.domain.presentation import Approve, DocumentCreate, DocumentEdit, Generate

router = APIRouter(prefix="/api", tags=["presentations"])


def context(request):
    user = getattr(getattr(request.state, "user", None), "id", None)
    if not user:
        raise HTTPException(401, "Authentication required")
    return request.app.state.presentations, user


def required(value):
    if value is None:
        raise HTTPException(404, "Документ не найден")
    return value


async def action(call):
    try:
        return required(await call)
    except ValueError as exc:
        status = 409 if str(exc) == "STATE_CONFLICT" else 429 if str(exc) == "QUEUE_FULL" else 422
        raise HTTPException(
            status,
            "Черновик изменился. Обновите документ."
            if status == 409
            else "Очередь занята. Повторите позже."
            if status == 429
            else str(exc),
        ) from exc


@router.get("/projects/{project_id}/presentations")
async def listing(project_id: UUID, request: Request):
    service, user = context(request)
    return required(await service.listing(str(project_id), user))


@router.post("/projects/{project_id}/presentations", status_code=201)
async def create(project_id: UUID, body: DocumentCreate, request: Request):
    service, user = context(request)
    return await action(service.create(str(project_id), user, body))


@router.get("/presentations/{identifier}")
async def get(identifier: UUID, request: Request):
    service, user = context(request)
    return required(await service.get(str(identifier), user))


@router.patch("/presentations/{identifier}")
async def save(identifier: UUID, body: DocumentEdit, request: Request):
    service, user = context(request)
    return await action(service.save(str(identifier), user, body))


@router.post("/presentations/{identifier}/refresh")
async def refresh(identifier: UUID, body: Generate, request: Request):
    service, user = context(request)
    return await action(service.refresh(str(identifier), user, body.version))


@router.post("/presentations/{identifier}/generate", status_code=202)
async def generate(identifier: UUID, body: Generate, request: Request):
    service, user = context(request)
    return await action(service.enqueue(str(identifier), user, body.version))


@router.post("/presentations/{identifier}/revisions/{revision}/cancel")
async def cancel(identifier: UUID, revision: UUID, request: Request):
    service, user = context(request)
    return required(await service.cancel(str(identifier), str(revision), user))


@router.post("/presentations/{identifier}/revisions/{revision}/approve")
async def approve(identifier: UUID, revision: UUID, body: Approve, request: Request):
    service, user = context(request)
    return await action(service.approve(str(identifier), str(revision), user, body.asset_hashes))


@router.post("/presentations/{identifier}/logo")
@router.post("/presentations/{identifier}/items/{offer_id}/image")
async def upload(identifier: UUID, request: Request, version: int = Query(ge=1), offer_id: str | None = None):
    service, user = context(request)
    required(await service.owned(str(identifier), user))
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > MAX_BYTES:
            raise HTTPException(413, "Максимальный размер изображения — 12 МБ")
    return await action(service.upload(str(identifier), user, bytes(data), version, offer_id))


@router.get("/presentations/{identifier}/logo")
async def logo(identifier: UUID, request: Request):
    service, user = context(request)
    row = required(await service.owned(str(identifier), user))
    key = row.draft["settings"].get("logo")
    if not key:
        raise HTTPException(404, "Логотип не загружен")
    return FileResponse(
        safe_path(service.root, key), media_type="image/png", headers={"Cache-Control": "private, no-store"}
    )


@router.get("/presentations/{identifier}/revisions/{revision}/pages/{page}")
async def page(identifier: UUID, revision: UUID, page: int, request: Request, thumbnail: bool = False):
    service, user = context(request)
    row = required(await service.revision(str(identifier), str(revision), user))
    value = next((p for p in row.result.get("pages", []) if p["page"] == page), None)
    if not value:
        raise HTTPException(404, "Страница не готова")
    return FileResponse(
        safe_path(service.root, value["thumbnail" if thumbnail else "preview"]),
        media_type="image/png",
        headers={"Cache-Control": "private, no-store"},
    )


@router.get("/presentations/{identifier}/revisions/{revision}/assets/{offer_id}")
async def asset(identifier: UUID, revision: UUID, offer_id: str, request: Request):
    service, user = context(request)
    row = required(await service.revision(str(identifier), str(revision), user))
    value = row.result.get("assets", {}).get(offer_id)
    if not value:
        raise HTTPException(404, "Фото не готово")
    return FileResponse(
        safe_path(service.root, value["key"]),
        media_type="image/png",
        headers={"Cache-Control": "private, no-store"},
    )


@router.get("/presentations/{identifier}/revisions/{revision}/pdf")
async def pdf(identifier: UUID, revision: UUID, request: Request, historical: bool = False):
    service, user = context(request)
    doc = required(await service.owned(str(identifier), user))
    row = required(await service.revision(str(identifier), str(revision), user))
    if row.status != "READY" or not row.result.get("pdf"):
        raise HTTPException(409, "PDF ещё не проверен и не готов к скачиванию")
    if row.source_version != doc.version and not historical:
        raise HTTPException(409, "Настройки изменились. Эта PDF относится к предыдущей версии.")
    return FileResponse(
        safe_path(service.root, row.result["pdf"]),
        media_type="application/pdf",
        filename=f"presentation-v{row.source_version}.pdf",
        headers={"Cache-Control": "private, no-store"},
    )
