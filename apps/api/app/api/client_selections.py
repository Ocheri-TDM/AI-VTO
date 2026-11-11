"""Guest-owned marks and account-owned durable notifications; no guest document edits."""

import asyncio
import json
import time
from uuid import UUID

import anyio
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse

from app.api.auth import current_user
from app.api.commercial_offers import PUBLIC_HEADERS, public_rate
from app.domain.client_selection import ClientChoice, NotificationRead, SelectionProblem, VisitorStart

public_router = APIRouter(
    prefix="/api/public/offers", tags=["client selection"], dependencies=[Depends(public_rate)]
)
router = APIRouter(prefix="/api", tags=["client selection and notifications"])


def owner(request):
    user = getattr(getattr(request.state, "user", None), "id", None)
    if not user:
        raise HTTPException(401, "Authentication required")
    return user


def guest_origin(request: Request):
    origin = request.headers.get("origin")
    same = {f"http://{request.headers.get('host', '')}", f"https://{request.headers.get('host', '')}"}
    trusted = set(request.app.state.settings.trusted_frontend_origins)
    if (
        not origin
        or origin not in same | trusted
        or request.headers.get("sec-fetch-site") == "cross-site"
        or request.headers.get("x-offer-selection") != "1"
    ):
        raise SelectionProblem("ORIGIN_REJECTED", "Источник запроса не разрешён.", 403)
    if request.headers.get("content-type", "").split(";")[0] != "application/json":
        raise SelectionProblem("JSON_REQUIRED", "Ожидается JSON-запрос.", 415)


@public_router.get("/{token}/selection")
async def own_selection(token: str, request: Request):
    return await request.app.state.client_selections.get(token, request.cookies)


@public_router.post("/{token}/selection/session", dependencies=[Depends(guest_origin)])
async def visitor_start(token: str, body: VisitorStart, request: Request, response: Response):
    value, cookie, _ = await request.app.state.client_selections.start(
        token, request.cookies, body.published_revision_id
    )
    if cookie:
        response.set_cookie(
            **cookie,
            httponly=True,
            samesite="lax",
            secure=request.app.state.settings.auth_cookie_secure
            or request.headers.get("origin", "").startswith("https://"),
        )
    response.headers.update(PUBLIC_HEADERS)
    return value


@public_router.put("/{token}/selection", dependencies=[Depends(guest_origin)])
async def choose(
    token: str, body: ClientChoice, request: Request, x_offer_csrf: str | None = Header(default=None)
):
    return await request.app.state.client_selections.mutate(token, request.cookies, x_offer_csrf, body)


@router.get("/offers/{identifier}/client-selection")
async def feedback(
    identifier: UUID,
    request: Request,
    before: int | None = Query(default=None, ge=1),
    limit: int = Query(default=20, ge=1, le=50),
):
    return await request.app.state.client_notifications.feedback(
        owner(request), str(identifier), before, limit
    )


@router.get("/notifications/interest")
async def interest(request: Request, ids: str = Query(max_length=4000)):
    try:
        identifiers = list(dict.fromkeys(str(UUID(value)) for value in ids.split(",") if value))
        if not 1 <= len(identifiers) <= 100:
            raise ValueError()
    except ValueError as exc:
        raise HTTPException(422, "Некорректный список предложений") from exc
    return await request.app.state.client_notifications.interest(owner(request), identifiers)


@router.get("/notifications")
async def notifications(request: Request, before: int | None = Query(default=None, ge=1)):
    return await request.app.state.client_notifications.notifications(owner(request), before)


@router.post("/notifications/{identifier}/read")
async def read_notification(identifier: UUID, body: NotificationRead, request: Request):
    return await request.app.state.client_notifications.mark_read(
        owner(request), str(identifier), body.sequence
    )


@router.get("/notifications/events")
async def events(request: Request, cursor: int | None = Query(default=None, ge=0, le=2**63 - 1)):
    user = owner(request)
    header = request.headers.get("last-event-id")
    if header is not None:
        try:
            if len(header) > 20:
                raise ValueError()
            cursor = int(header)
            if not 0 <= cursor <= 2**63 - 1:
                raise ValueError()
        except ValueError as exc:
            raise HTTPException(400, "Некорректный cursor") from exc
    service = request.app.state.client_notifications
    settings = request.app.state.settings

    def message(name, value=None, identifier=None):
        return (
            (f"id: {identifier}\n" if identifier is not None else "")
            + f"event: {name}\ndata: "
            + json.dumps(value or {})
            + "\n\n"
        )

    async def stream():
        from app.api.request_safety import RequestProblem

        try:
            async with request.app.state.notification_stream_admission.enter(user, per_user_limit=4):
                started = heartbeat = checked = time.monotonic()
                after = cursor
                ready = False
                while (
                    not await request.is_disconnected()
                    and time.monotonic() - started < settings.notification_stream_seconds
                ):
                    now = time.monotonic()
                    if now - checked >= 5:
                        # ASGI disconnect cancellation must not interrupt the
                        # rollback/return of a short-lived pooled connection.
                        with anyio.CancelScope(shield=True):
                            authenticated = await current_user(request)
                        if not authenticated or authenticated.id != user:
                            yield message("auth.required")
                            return
                        checked = now
                    with anyio.CancelScope(shield=True):
                        batch = await service.events(user, after if after is not None else 0)
                    if after is None or batch["reset"]:
                        after = batch["cursor"]
                        yield message("state.sync", {"cursor": after}, after)
                    else:
                        for sequence in batch["events"]:
                            after = sequence
                            yield message("account.changed", {"cursor": after}, after)
                    if not ready:
                        yield "retry: 3000\n" + message("stream.ready")
                        ready = True
                    if now - heartbeat >= 10:
                        yield message("stream.heartbeat", {"cursor": after})
                        heartbeat = now
                    await asyncio.sleep(settings.notification_poll_seconds)
                yield message("stream.renew")
        except RequestProblem:
            yield message("stream.unavailable")

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers=PUBLIC_HEADERS
        | {
            "X-Accel-Buffering": "no",
            "Cache-Control": "private, no-store, max-age=0, no-transform",
        },
    )
