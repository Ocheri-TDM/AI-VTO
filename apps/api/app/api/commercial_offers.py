"""Owner-only editing and tightly scoped, read-only publication endpoints."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse

from app.application.presentation_assets import MAX_BYTES, safe_path
from app.domain.commercial_offer import OfferAction, OfferComposition, OfferCreate, OfferEdit, ShareAction

router = APIRouter(prefix="/api", tags=["commercial offers"])
PUBLIC_HEADERS = {
    "Cache-Control": "private, no-store, max-age=0",
    "Pragma": "no-cache",
    "Referrer-Policy": "no-referrer",
    "X-Robots-Tag": "noindex, nofollow, noarchive",
    "X-Content-Type-Options": "nosniff",
}


def owner(request):
    user = getattr(getattr(request.state, "user", None), "id", None)
    if not user:
        raise HTTPException(401, "Authentication required")
    return request.app.state.offers, user


def required(value):
    if value is None:
        raise HTTPException(404, "Предложение не найдено")
    return value


async def action(call):
    try:
        return required(await call)
    except ValueError as exc:
        code = str(exc)
        status = (
            409
            if code in ("STATE_CONFLICT", "REVISION_CHANGED", "ARCHIVED_OFFER")
            else 410
            if code == "OFFER_LINK_EXPIRED"
            else 404
            if code == "PROJECT_NOT_FOUND"
            else 429
            if code == "QUEUE_FULL"
            else 422
        )
        detail = (
            "Документ или публикация изменились. Обновите состояние."
            if code == "STATE_CONFLICT"
            else "Версия обновилась. Обновите страницу предложения."
            if code == "REVISION_CHANGED"
            else "Очередь занята. Повторите позже."
            if status == 429
            else "Срок действия ссылки истёк"
            if code == "OFFER_LINK_EXPIRED"
            else "Это предыдущее предложение. Откройте основное предложение проекта."
            if code == "ARCHIVED_OFFER"
            else code
        )
        raise HTTPException(status, {"code": code, "message": detail}, headers=PUBLIC_HEADERS) from None


@router.get("/projects/{project_id}/offers")
async def listing(project_id: UUID, request: Request):
    service, user = owner(request)
    return required(await service.listing(str(project_id), user))


@router.post("/projects/{project_id}/offers", status_code=201)
async def create(project_id: UUID, body: OfferCreate, request: Request, response: Response):
    service, user = owner(request)
    value = await action(service.create(str(project_id), user, body))
    response.status_code = 201 if value.pop("created") else 200
    return value


@router.get("/projects/{project_id}/offer")
async def primary(project_id: UUID, request: Request):
    service, user = owner(request)
    try:
        return await service.primary(str(project_id), user)
    except ValueError:
        raise HTTPException(404, "Проект не найден") from None


@router.get("/branding/vto-logo.png")
async def branding():
    from app.application.presentation_logo import OFFER_LOGO

    return FileResponse(
        OFFER_LOGO,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=3600", "X-Content-Type-Options": "nosniff"},
    )


@router.get("/offers/{identifier}")
async def get(identifier: UUID, request: Request):
    service, user = owner(request)
    return await action(service.get(str(identifier), user))


@router.patch("/offers/{identifier}")
async def save(identifier: UUID, body: OfferEdit, request: Request):
    service, user = owner(request)
    return await action(service.save(str(identifier), user, body))


@router.post("/offers/{identifier}/refresh")
async def refresh(identifier: UUID, body: OfferAction, request: Request):
    service, user = owner(request)
    return await action(service.refresh(str(identifier), user, body.version))


@router.get("/offers/{identifier}/composition")
async def composition_preview(identifier: UUID, request: Request):
    service, user = owner(request)
    return await action(service.composition(str(identifier), user))


@router.post("/offers/{identifier}/composition")
async def composition_update(identifier: UUID, request: Request, body: OfferComposition):
    service, user = owner(request)
    return await action(service.composition(str(identifier), user, body))


@router.post("/offers/{identifier}/publish", status_code=202)
@router.post("/offers/{identifier}/exports", status_code=202)
async def publish(identifier: UUID, body: OfferAction, request: Request):
    service, user = owner(request)
    return await action(
        service.publish(str(identifier), user, body, publish=request.url.path.endswith("/publish"))
    )


@router.post("/offers/{identifier}/revoke")
@router.post("/offers/{identifier}/rotate")
async def share_action(identifier: UUID, body: ShareAction, request: Request):
    service, user = owner(request)
    return await action(service.share_action(str(identifier), user, body, request.url.path.rsplit("/", 1)[1]))


@router.post("/offers/{identifier}/logo")
@router.post("/offers/{identifier}/banner", status_code=202)
async def upload(identifier: UUID, request: Request, version: int = Query(ge=1)):
    service, user = owner(request)
    row = required(await service.owned(str(identifier), user))
    try:
        service.editable(row)
    except ValueError:
        raise HTTPException(409, "Это предыдущее предложение проекта") from None
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > MAX_BYTES:
            raise HTTPException(413, "Максимальный размер изображения — 12 МБ")
    if request.url.path.endswith("/banner"):
        row = await service.owned(str(identifier), user)
        previous = next(
            (
                m
                for m in row.draft.get("materials", [])
                if m["role"] == "COVER_PHOTO" and m["status"] == "READY"
            ),
            None,
        )
        await action(
            service.documents.upload_material(
                str(identifier),
                user,
                bytes(data),
                version,
                "COVER_PHOTO",
                "Баннер",
                previous["id"] if previous else None,
            )
        )
    else:
        await action(service.documents.upload(str(identifier), user, bytes(data), version))
    return required(await service.get(str(identifier), user))


@router.get("/offers/{identifier}/logo")
@router.get("/offers/{identifier}/banner")
async def header_asset(identifier: UUID, request: Request):
    service, user = owner(request)
    row = required(await service.owned(str(identifier), user))
    if request.url.path.endswith("/logo"):
        from app.application.presentation_logo import OFFER_LOGO

        key = row.draft["settings"].get("logo") if row.draft["settings"].get("use_logo") else None
        path = safe_path(service.root, key) if key else OFFER_LOGO
    else:
        material = next(
            (
                m
                for m in row.draft.get("materials", [])
                if m["role"] == "COVER_PHOTO" and m["status"] == "READY" and m.get("enabled")
            ),
            None,
        )
        if not material:
            raise HTTPException(404)
        path = safe_path(service.root, material["key"])
    if not path.is_file():
        raise HTTPException(404, "Изображение недоступно")
    return FileResponse(path, media_type="image/png", headers=PUBLIC_HEADERS)


def file(service, row, kind):
    key = row.result.get(kind)
    if not key:
        raise HTTPException(404, "Файл недоступен")
    path = safe_path(service.root, key)
    if not path.is_file() or path.stat().st_size > 100 * 1024 * 1024:
        raise HTTPException(404, "Файл недоступен")
    mime = (
        "application/pdf"
        if kind == "pdf"
        else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    return FileResponse(
        path,
        media_type=mime,
        filename=f"commercial-offer-v{row.source_version}.{kind}",
        headers=PUBLIC_HEADERS,
    )


@router.get("/offers/{identifier}/draft-images/{asset_id}")
async def draft_image(identifier: UUID, asset_id: str, request: Request):
    service, user = owner(request)
    row = required(await service.owned(str(identifier), user))
    photo = next((p for i in row.draft["items"] for p in i["photos"] if p["id"] == asset_id), None)
    if not photo or not photo.get("cached_key"):
        raise HTTPException(404)
    path = safe_path(service.root, photo["cached_key"])
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, media_type="image/png", headers=PUBLIC_HEADERS)


@router.get("/offers/{identifier}/revisions/{revision}/{kind}")
async def download(identifier: UUID, revision: UUID, kind: str, request: Request):
    service, user = owner(request)
    if kind not in ("pdf", "xlsx"):
        raise HTTPException(404)
    row = required(await service.revision(str(identifier), str(revision), user))
    return file(service, row, kind)


@router.get("/offers/{identifier}/preview")
async def preview(identifier: UUID, request: Request, revision: UUID):
    service, user = owner(request)
    row = required(await service.revision(str(identifier), str(revision), user))
    return service.client_view(row, f"/api/offers/{identifier}/revisions/{revision}")


@router.get("/offers/{identifier}/revisions/{revision}/assets/{asset_id}")
async def private_image(identifier: UUID, revision: UUID, asset_id: str, request: Request):
    service, user = owner(request)
    row = required(await service.revision(str(identifier), str(revision), user))
    return image(service, row, asset_id)


async def public_rate(request: Request):
    # Public GET only reads already prepared snapshots/files. Its separate
    # bounded limiter never consumes search slots or trusts forwarded headers.
    limiter = request.app.state.offer_public_admission
    try:
        async with limiter.enter(request.client.host if request.client else "local", per_user_limit=8):
            yield
    except Exception as exc:
        from app.api.request_safety import RequestProblem

        if isinstance(exc, RequestProblem):
            raise HTTPException(429, "Повторите запрос позже", headers=PUBLIC_HEADERS) from None
        raise


public_router = APIRouter(
    prefix="/api/public/offers", tags=["published offers"], dependencies=[Depends(public_rate)]
)


async def public_value(request, token, revision=None):
    service = request.app.state.offers
    result = await action(service.public(token, revision))
    return service, *result


@public_router.get("/{token}")
async def public_get(token: str, request: Request):
    _, value, _ = await public_value(request, token)
    return value


@public_router.get("/{token}/revisions/{revision}/{kind}")
async def public_file(token: str, revision: UUID, kind: str, request: Request):
    if kind not in ("pdf", "xlsx"):
        raise HTTPException(404)
    service, _, row = await public_value(request, token, str(revision))
    return file(service, row, kind)


def image(service, row, id):
    key = row.result.get("asset_keys", {}).get(id)
    if not key:
        raise HTTPException(404)
    path = safe_path(service.root, key)
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, media_type="image/png", headers=PUBLIC_HEADERS)


@public_router.get("/{token}/revisions/{revision}/assets/{asset_id}")
async def public_image(token: str, revision: UUID, asset_id: str, request: Request):
    service, _, row = await public_value(request, token, str(revision))
    return image(service, row, asset_id)
