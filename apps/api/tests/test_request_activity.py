import asyncio
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.request_activity import ActivityJournal, RequestActivity
from app.database.models import Base
from app.main import create_app


def records(path):
    return [json.loads(line) for line in path.read_text("utf-8").splitlines()]


async def test_authenticated_names_ids_login_logout_and_concurrent_user_isolation(settings, tmp_path):
    path = tmp_path / "requests.jsonl"
    app = create_app(settings.model_copy(update={"auth_required": True, "request_activity_logging": True,
                                                 "request_activity_log": path}), [])
    async with app.state.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as alice, AsyncClient(transport=transport, base_url="http://test") as bob:
            users = []
            for client, name in [(alice, "Alice"), (bob, "Борис")]:
                response = await client.post("/api/auth/register", json={"name": name, "password": "private-password"})
                assert response.status_code == 201
                users.append(response.json())
            responses = await asyncio.gather(alice.get("/api/projects"), bob.get("/api/projects"))
            assert all(response.status_code == 200 for response in responses)
            assert (await alice.get("/api/auth/me")).status_code == 200
            assert (await alice.post("/api/auth/logout")).status_code == 204
            assert (await alice.get("/api/projects")).status_code == 401
            assert (await alice.post("/api/auth/login", json={"name": "Alice", "password": "private-password"})).status_code == 200
            assert (await alice.post("/api/auth/login", json={"name": "Alice", "password": "wrong-private-password"})).status_code == 401
            saved = records(path)
            for user in users:
                assert any(row["route"] == "/api/projects" and row["user_id"] == user["id"] and row["name"] == user["name"] for row in saved)
            assert any(row["route"] == "/api/auth/logout" and row["user_id"] == users[0]["id"] for row in saved)
            assert any(row["route"] == "/api/auth/login" and row["user_id"] == users[0]["id"] and row["status"] == 200 for row in saved)
            assert saved[-1]["user_id"] is None and saved[-1]["status"] == 401
            assert all(row["duration_ms"] >= 0 for row in saved)
            content = path.read_text("utf8")
            assert "private-password" not in content and "research_session" not in content
            assert all(value not in content for value in dict(bob.cookies).values())


@pytest.mark.parametrize("matched", [True, False])
async def test_capabilities_queries_headers_and_bodies_never_enter_log(tmp_path, matched):
    path = tmp_path / "requests.jsonl"
    journal = ActivityJournal(path)

    async def app(scope, receive, send):
        if matched:
            scope["route"] = SimpleNamespace(path="/api/public/offers/{key}")
        request = await receive()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": request.get("body", b"")})

    async with AsyncClient(transport=ASGITransport(app=RequestActivity(app, journal)), base_url="http://test") as client:
        response = await client.post("/api/public/offers/secret-capability?secret=private-query",
                                     content="private-body", headers={"Cookie": "private-cookie", "Authorization": "private-header"})
        assert response.text == "private-body"
    journal.close()
    content = path.read_text("utf8")
    for value in ("secret-capability", "private-query", "private-body", "private-cookie", "private-header"):
        assert value not in content
    assert records(path)[0]["route"] == ("/api/public/offers/{key}" if matched else "/api/public/<unmatched>")


async def test_stream_open_is_immediate_and_close_keeps_body_unchanged(tmp_path):
    path = tmp_path / "requests.jsonl"
    journal = ActivityJournal(path)

    async def app(scope, receive, send):
        scope["route"] = SimpleNamespace(path="/api/notifications/events")
        scope["state"] = {"user": SimpleNamespace(id="one", display_name="Первый")}
        await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/event-stream")]})
        assert records(path)[0]["phase"] == "stream_open"
        await asyncio.sleep(.01)
        await send({"type": "http.response.body", "body": b"event: ready\ndata: {}\n\n"})

    async with AsyncClient(transport=ASGITransport(app=RequestActivity(app, journal)), base_url="http://test") as client:
        response = await client.get("/api/notifications/events")
        assert response.content == b"event: ready\ndata: {}\n\n"
    journal.close()
    rows = records(path)
    assert [row["phase"] for row in rows] == ["stream_open", "stream_closed"]
    assert rows[1]["duration_ms"] >= rows[0]["duration_ms"]
    assert all(row["user_id"] == "one" for row in rows)


@pytest.mark.parametrize("cancelled", [False, True])
async def test_failures_and_cancellation_keep_original_exception_without_exception_text(tmp_path, cancelled):
    path = tmp_path / "requests.jsonl"
    journal = ActivityJournal(path)

    async def failing(scope, receive, send):
        raise asyncio.CancelledError() if cancelled else RuntimeError("secret-exception-message")

    app = RequestActivity(failing, journal)
    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        pass

    with pytest.raises(asyncio.CancelledError if cancelled else RuntimeError):
        await app({"type": "http", "path": "/api/secret-target", "method": "GET"}, receive, send)
    journal.close()
    assert "secret-exception-message" not in path.read_text("utf8")
    assert records(path)[0]["phase"] == ("cancelled" if cancelled else "complete")


def test_journal_rotation_is_bounded(tmp_path):
    path = tmp_path / "requests.jsonl"
    journal = ActivityJournal(path)
    journal.handler.maxBytes = 1024
    for _ in range(100):
        journal.record({"method": "GET", "path": "/api/projects"}, 200, 1, "complete")
    journal.close()
    files = list(tmp_path.glob("requests.jsonl*"))
    assert len(files) == 4
    assert all(file.stat().st_size < 1024 for file in files)


def load_launcher():
    spec = importlib.util.spec_from_file_location("project_launcher", Path(__file__).parents[3] / "scripts/run_project.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_live_tail_handles_partial_utf8_records_and_rotation(tmp_path):
    launcher = load_launcher()
    path = tmp_path / "activity.jsonl"
    path.write_bytes(b"")
    tail = launcher.JournalTail(path)
    line = (json.dumps({"name": "Пользователь"}, ensure_ascii=False) + "\n").encode()
    path.write_bytes(line[:15])
    assert tail.read() == []
    with path.open("ab") as file:
        file.write(line[15:])
    assert tail.read() == [{"name": "Пользователь"}]
    replacement = tmp_path / "replacement"
    replacement.write_bytes(line)
    replacement.replace(path)
    assert tail.read() == [{"name": "Пользователь"}]


def test_console_format_has_local_time_duration_identity_and_no_terminal_controls():
    launcher = load_launcher()
    line = launcher.format_activity({"timestamp": "2026-11-25T07:30:00+00:00", "method": "GET",
                                     "route": "/api/projects", "status": 200, "duration_ms": 12.5,
                                     "name": "Alice\n\x1b[31m", "user_id": "user-1", "phase": "complete"})
    assert "12:30:00 UTC+5" in line and "12.5 ms" in line
    assert 'Id="user-1"' in line and "Name=" in line
    assert "\n" not in line and "\x1b" not in line


def test_foreign_service_port_is_rejected_without_process_changes(monkeypatch):
    launcher = load_launcher()
    monkeypatch.setattr(launcher, "listening", lambda port: True)
    monkeypatch.setattr(launcher, "owned_listener", lambda port, kind: False)
    with pytest.raises(RuntimeError, match="8000"):
        launcher.guard_port(8000, "api")
