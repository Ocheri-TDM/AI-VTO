"""Opt-in HTTP activity journal. Never record bodies, credentials or raw URLs."""

import asyncio
import json
import logging
import re
import time
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path


def route_label(scope):
    route = getattr(scope.get("route"), "path", None)
    if route:
        return route
    # Auth/body/origin rejection can happen before routing. An arbitrary path
    # can contain a capability or a secret even when no route matches it.
    family = scope.get("path", "").split("/")[2:3]
    allowed = {"auth", "chats", "projects", "researches", "searches", "hard-requests",
               "presentations", "offers", "notifications", "public", "health", "dev"}
    return f"/api/{family[0]}/<unmatched>" if family and family[0] in allowed else "/api/<unmatched>"


class ActivityJournal:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.logger = logging.Logger("request_activity", level=logging.INFO)
        self.handler = RotatingFileHandler(path, maxBytes=5 * 1024 * 1024, backupCount=3,
                                           encoding="utf-8", delay=True)
        self.logger.addHandler(self.handler)

    def record(self, scope, status, duration_ms, phase):
        user = scope.get("state", {}).get("user")
        method = scope.get("method", "")
        payload = {
            "timestamp": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "method": method if re.fullmatch(r"[A-Z-]{1,20}", method) else "OTHER",
            "route": route_label(scope),
            "status": status,
            "duration_ms": round(duration_ms, 1),
            "name": getattr(user, "display_name", None),
            "user_id": getattr(user, "id", None),
            "phase": phase,
        }
        try:
            self.logger.info(json.dumps(payload, ensure_ascii=False))
        except (OSError, ValueError):
            # Diagnostics must not change the response or release of user locks.
            pass

    def close(self):
        self.handler.close()


class RequestActivity:
    def __init__(self, app, journal):
        self.app, self.journal = app, journal

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope.get("path", "").startswith("/api/"):
            return await self.app(scope, receive, send)
        started = time.perf_counter()
        status, streaming, cancelled, responded = 500, False, False, False

        def record(phase):
            self.journal.record(scope, status, (time.perf_counter() - started) * 1000, phase)

        async def tracked(message):
            nonlocal status, streaming, responded
            if message["type"] == "http.response.start":
                responded = True
                status = message["status"]
                headers = dict(message.get("headers", []))
                streaming = headers.get(b"content-type", b"").startswith(b"text/event-stream")
                if streaming:
                    record("stream_open")
            await send(message)

        try:
            await self.app(scope, receive, tracked)
        except asyncio.CancelledError:
            cancelled = True
            if not responded:
                status = 499
            raise
        finally:
            record("cancelled" if cancelled else "stream_closed" if streaming else "complete")
