"""Bounded JSON/search requests. No query text or credentials enter telemetry."""

import asyncio
import json
import time
from collections import OrderedDict
from contextlib import asynccontextmanager
from contextvars import ContextVar
from urllib.parse import parse_qsl, urlsplit
from uuid import uuid4

from sqlalchemy.exc import SQLAlchemyError
from starlette.responses import JSONResponse

search_transaction = ContextVar("search_transaction", default=False)


class RequestProblem(Exception):
    def __init__(self, code, detail, status=422, stage="validation"):
        self.code, self.detail, self.status, self.stage = code, detail, status, stage
        super().__init__(detail)


def problem_response(exc, trace):
    return JSONResponse(
        {"detail": exc.detail, "code": exc.code, "stage": exc.stage, "trace_id": trace},
        status_code=exc.status,
        headers={"X-Request-ID": trace},
    )


def validate_query(value):
    import re
    import unicodedata

    if not isinstance(value, str):
        raise ValueError("Запрос должен быть строкой.")
    value = unicodedata.normalize("NFKC", value).strip()
    if not value or not any(c.isalnum() for c in value):
        raise ValueError("Укажите товар, бренд или назначение.")
    if any(unicodedata.category(c) in ("Cs", "Cc", "Cf") and c not in "\n\r\t" for c in value):
        raise ValueError("Запрос содержит недопустимые управляющие символы.")
    if len(value) > 2000 or len(value.encode("utf-8")) > 8000:
        raise ValueError("Слишком длинный запрос.")
    if sum(value.count(c) for c in ",;\n") > 48:
        raise ValueError("Слишком много условий. Разделите запрос.")
    if any(len(word) > 256 for word in re.findall(r"\w+", value)):
        raise ValueError("Слишком длинное слово в запросе.")
    return value


class Admission:
    """Fairness by authenticated user, never by shared IP or forwarded headers.

    Process-local limits bound this deployment; database locks remain responsible
    for correctness between workers. Bounded inactive LRU prevents user-key growth.
    """

    def __init__(self, settings):
        self.settings = settings
        self.global_slots = asyncio.Semaphore(settings.search_concurrency)
        self.users = OrderedDict()
        self.active = self.queued = self.completed = self.rejected = 0

    @asynccontextmanager
    async def enter(self, user, *, per_user_limit=2):
        now = time.monotonic()
        key = user or "anonymous"
        state = self.users.setdefault(key, {"active": 0, "start": now, "count": 0})
        self.users.move_to_end(key)
        if now - state["start"] >= 60:
            state.update(start=now, count=0)
        if state["active"] >= per_user_limit or state["count"] >= self.settings.search_requests_per_minute:
            self.rejected += 1
            raise RequestProblem(
                "USER_REQUEST_LIMIT", "Слишком много запросов. Повторите позже.", 429, "admission"
            )
        if self.global_slots.locked() and self.queued >= self.settings.search_queue_limit:
            raise RequestProblem("SEARCH_QUEUE_FULL", "Поиск занят. Повторите позже.", 429, "admission")
        state["count"] += 1
        state["active"] += 1
        self.queued += 1
        acquired = False
        try:
            try:
                async with asyncio.timeout(self.settings.search_queue_timeout_seconds):
                    await self.global_slots.acquire()
                acquired = True
            except TimeoutError as exc:
                raise RequestProblem(
                    "SEARCH_QUEUE_TIMEOUT", "Поиск занят. Повторите позже.", 503, "queue"
                ) from exc
            finally:
                self.queued -= 1
            self.active += 1
            yield
            self.completed += 1
        finally:
            state["active"] -= 1
            if acquired:
                self.active -= 1
                self.global_slots.release()
            while len(self.users) > 4096:
                inactive = next((k for k, s in self.users.items() if not s["active"]), None)
                if inactive is None:
                    break
                self.users.pop(inactive)


class RequestBoundary:
    def __init__(self, app, settings):
        self.app, self.settings = app, settings

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/api/"):
            return await self.app(scope, receive, send)
        trace = uuid4().hex
        scope.setdefault("state", {})["trace_id"] = trace
        headers = {k.decode().lower(): v.decode("latin1") for k, v in scope["headers"]}
        body = bytearray()
        started = False
        is_search = scope["path"].startswith(("/api/projects", "/api/researches", "/api/chats"))
        body_limit = (
            262144 if scope["path"].startswith(("/api/presentations", "/api/offers")) else self.settings.search_json_max_bytes
        )
        token = search_transaction.set(is_search)

        async def tracked(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                message.setdefault("headers", []).append((b"x-request-id", trace.encode()))
            await send(message)

        async def run():
            if scope["method"] in ("POST", "PUT", "PATCH", "DELETE"):
                origin = headers.get("origin")
                if origin and headers.get("cookie"):
                    try:
                        parsed = urlsplit(origin)
                    except ValueError as exc:
                        raise RequestProblem(
                            "ORIGIN_REJECTED", "Источник запроса не разрешён.", 403, "origin"
                        ) from exc
                    same = origin in (
                        "http://" + headers.get("host", ""),
                        "https://" + headers.get("host", ""),
                    )
                    if not same and origin not in self.settings.trusted_frontend_origins:
                        raise RequestProblem(
                            "ORIGIN_REJECTED", "Источник запроса не разрешён.", 403, "origin"
                        )
                    if parsed.username or parsed.password:
                        raise RequestProblem(
                            "ORIGIN_REJECTED", "Источник запроса не разрешён.", 403, "origin"
                        )
            try:
                if len(scope.get("query_string", b"")) > 8192:
                    raise RequestProblem("QUERY_TOO_LARGE", "Слишком длинные параметры запроса.", 413)
                params = parse_qsl(
                    scope.get("query_string", b"").decode("utf-8", "strict"), keep_blank_values=True
                )
            except UnicodeError as exc:
                raise RequestProblem("INVALID_QUERY", "Некорректная кодировка параметров.", 400) from exc
            if len(params) != len({k for k, _ in params}):
                raise RequestProblem("DUPLICATE_PARAMETER", "Параметр запроса повторяется.", 400)
            if (
                "application/json" in headers.get("content-type", "")
                and scope["method"] in ("POST", "PUT", "PATCH", "DELETE")
                and not (scope["method"] == "DELETE" and headers.get("content-length", "0") == "0")
            ):
                try:
                    declared = int(headers.get("content-length", "0"))
                except ValueError as exc:
                    raise RequestProblem("INVALID_BODY", "Некорректный размер запроса.", 400) from exc
                if declared > body_limit:
                    raise RequestProblem("BODY_TOO_LARGE", "Запрос слишком большой.", 413)
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    body.extend(message.get("body", b""))
                    if len(body) > body_limit:
                        raise RequestProblem("BODY_TOO_LARGE", "Запрос слишком большой.", 413)
                    if not message.get("more_body"):
                        break
                # Reject structural depth before the recursive JSON decoder.
                # Brackets inside quoted/escaped strings are ordinary data.
                depth, quoted, escaped = 0, False, False
                for byte in body:
                    if quoted:
                        if escaped:
                            escaped = False
                        elif byte == 92:
                            escaped = True
                        elif byte == 34:
                            quoted = False
                    elif byte == 34:
                        quoted = True
                    elif byte in (91, 123):
                        depth += 1
                        if depth > 16:
                            raise RequestProblem("JSON_COMPLEXITY_LIMIT", "Слишком сложный запрос.", 413)
                    elif byte in (93, 125):
                        depth -= 1
                try:

                    def unique_object(pairs):
                        result = {}
                        for k, v in pairs:
                            if k in result:
                                raise ValueError("Duplicate JSON field")
                            result[k] = v
                        return result

                    def invalid_constant(value):
                        raise ValueError("Non-finite JSON number")

                    value = json.loads(body, object_pairs_hook=unique_object, parse_constant=invalid_constant)
                except (ValueError, UnicodeError, RecursionError) as exc:
                    raise RequestProblem("INVALID_JSON", "Некорректный JSON.", 400) from exc
                stack, nodes = [(value, 0)], 0
                while stack:
                    item, depth = stack.pop()
                    nodes += 1
                    if depth > 16 or nodes > 4096:
                        raise RequestProblem("JSON_COMPLEXITY_LIMIT", "Слишком сложный запрос.", 413)
                    if isinstance(item, dict):
                        stack.extend((v, depth + 1) for v in item.values())
                    elif isinstance(item, list):
                        stack.extend((v, depth + 1) for v in item)
                consumed = False
                original_receive = receive

                async def replay():
                    nonlocal consumed
                    if not consumed:
                        consumed = True
                        return {"type": "http.request", "body": bytes(body), "more_body": False}
                    return await original_receive()

                await self.app(scope, replay, tracked)
            else:
                await self.app(scope, receive, tracked)

        try:
            # SSE has its own disconnect loop; never turn an open stream into JSON.
            if scope["path"].endswith("/events"):
                await run()
            elif is_search or scope["path"] == "/api/health":
                async with asyncio.timeout(
                    3 if scope["path"] == "/api/health" else self.settings.search_deadline_seconds
                ):
                    await run()
            else:
                await run()
        except TimeoutError:
            if not started:
                await problem_response(
                    RequestProblem(
                        "REQUEST_DEADLINE", "Время поиска истекло. Повторите запрос.", 504, "deadline"
                    ),
                    trace,
                )(scope, receive, send)
        except RequestProblem as exc:
            if not started:
                await problem_response(exc, trace)(scope, receive, send)
        except (SQLAlchemyError, ConnectionError):
            if not started:
                await problem_response(
                    RequestProblem(
                        "DATABASE_UNAVAILABLE", "База данных временно недоступна.", 503, "database"
                    ),
                    trace,
                )(scope, receive, send)
        except Exception as exc:
            from app.observability import log_event

            log_event("request_failed", error_type=type(exc).__name__, trace_id=trace)
            if not started:
                await problem_response(
                    RequestProblem(
                        "EXECUTION_FAILED",
                        "Не удалось выполнить запрос. Повторите попытку.",
                        500,
                        "execution",
                    ),
                    trace,
                )(scope, receive, send)
        finally:
            search_transaction.reset(token)
