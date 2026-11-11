"""Explicit opt-in HTTP/real PostgreSQL regression using the acceptance ledger.

Run prepare/setup first. Never creates a test database or truncates any table.
Default unit runs deselect this test; the main acceptance invokes it explicitly.
"""

import json
import os
from pathlib import Path

import httpx
import pytest


@pytest.mark.postgres
async def test_pg_concurrent_read_ack_and_guest_commit_keep_new_event_unread(monkeypatch):
    filename = os.getenv("CLIENT_SELECTION_LIVE_LEDGER")
    if not filename:
        pytest.skip("Explicit disposable acceptance ledger required")
    root = Path(__file__).resolve().parents[3]
    ledger = Path(filename).resolve()
    assert ledger == (root / ".local/client-selection-ledger.json").resolve()
    state = json.loads(ledger.read_text("utf-8"))
    assert all(u["name"].startswith("clean-start-" + state["run"]) for u in state["users"])
    async with httpx.AsyncClient(
        base_url="https://souvenir-studio-three.vercel.app", cookies=state["users"][0]["cookies"], timeout=60
    ) as client:
        me = await client.get("/api/auth/me")
        assert me.status_code == 200 and me.json()["id"] == state["users"][0]["id"]
    # This real HTTP test exercises PostgreSQL's implicit FK locks, which SQLite
    # cannot reproduce; the assertions check committed business state and errors.
    monkeypatch.syspath_prepend(str(root / "scripts"))
    from quality.client_selection_processes import run

    await run("race", state, root, ledger, root / "docs/screenshots/client-selection")
