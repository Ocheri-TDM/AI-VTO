"""Main populated-database acceptance, task-owned resources only."""

import asyncio
import json
import os
import subprocess
import sys

import httpx
import psutil
from sqlalchemy import text

from app.config import Settings
from app.database.connection import create_database


async def run(phase, root, ledger, public):
    from check_chat_workflow import save

    state = json.loads(ledger.read_text("utf-8"))
    if phase == "web":
        listeners = {
            c.pid
            for c in psutil.net_connections(kind="tcp")
            if c.laddr and c.laddr.port == 3000 and c.status == "LISTEN"
        }
        assert len(listeners) == 1
        process = psutil.Process(listeners.pop())
        assert process.exe().lower().endswith("node.exe")
        from pathlib import Path

        Path(process.cwd()).resolve().relative_to(root.resolve())
        owned = [process, *process.children(recursive=True)]
        for p in reversed(owned):
            try:
                p.terminate()
            except psutil.NoSuchProcess:
                pass
        await asyncio.to_thread(psutil.wait_procs, owned, timeout=5)
        with (root / ".local/web-chat-workflow.log").open("ab") as log:
            web = subprocess.Popen(
                [
                    "C:/Program Files/nodejs/node.exe",
                    str(root / "apps/web/node_modules/next/dist/bin/next"),
                    "start",
                    str(root / "apps/web"),
                    "--hostname",
                    "127.0.0.1",
                    "--port",
                    "3000",
                ],
                cwd=root,
                stdout=log,
                stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        state["web_pid"] = web.pid
        save(ledger, state)
        print("Frontend restarted with final build")
        return
    if phase in ("deploy", "restart"):
        engine, sessions = create_database(Settings().database_url)
        try:
            async with sessions() as db:
                await db.execute(text("SET TRANSACTION READ ONLY"))
                if phase == "deploy" and "columns" not in state:
                    state["columns"] = {}
                    for table in [
                        "users",
                        "projects",
                        "messages",
                        "project_collection_items",
                        "presentation_documents",
                        "presentation_revisions",
                    ]:
                        state["columns"][table] = list(
                            await db.scalars(
                                text(
                                    "SELECT column_name FROM information_schema.columns WHERE table_name=:table AND table_schema='public' ORDER BY ordinal_position"
                                ),
                                {"table": table},
                            )
                        )
                    save(ledger, state)
        finally:
            await engine.dispose()
        listeners = {
            c.pid
            for c in psutil.net_connections(kind="tcp")
            if c.laddr and c.laddr.port == 8000 and c.status == "LISTEN"
        }
        assert len(listeners) == 1
        process = psutil.Process(listeners.pop())
        assert (
            "-m app" in " ".join(process.cmdline())
            and process.cwd().casefold() == str(root / "apps/api").casefold()
        )
        parent = process.parent()
        if (
            parent
            and "-m app" in " ".join(parent.cmdline())
            and parent.cwd().casefold() == str(root / "apps/api").casefold()
        ):
            process = parent
        owned = [process, *process.children(recursive=True)]
        for p in reversed(owned):
            try:
                p.terminate()
            except psutil.NoSuchProcess:
                pass
        await asyncio.to_thread(psutil.wait_procs, owned, timeout=5)
        if phase == "deploy":
            result = await asyncio.to_thread(
                subprocess.run,
                [sys.executable, "-m", "alembic", "-c", "apps/api/alembic.ini", "upgrade", "head"],
                cwd=root,
                capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
                timeout=60,
            )
            assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")[-2000:]
        with (root / ".local/api-chat-workflow.log").open("ab") as log:
            api = subprocess.Popen(
                [sys.executable, "-m", "app"],
                cwd=root / "apps/api",
                env=dict(os.environ),
                stdout=log,
                stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        state["api_pid"] = api.pid
        save(ledger, state)
        async with httpx.AsyncClient() as client:
            for _ in range(100):
                try:
                    if (await client.get("http://127.0.0.1:8000/api/health")).status_code == 200:
                        print("API healthy; migration", phase)
                        return
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.2)
        raise RuntimeError("API health timeout")
    if phase == "run":
        from quality.chat_workflow_http import run as http_run

        return await http_run(state, root, ledger, public)
    if phase == "browser":
        from quality.chat_workflow_browser import run as browser_run

        return await browser_run(state, root, ledger, public)
    if phase == "race":
        from quality.chat_workflow_race import run as race_run

        return await race_run(state, ledger, public)
    if phase in ("verify", "cleanup"):
        from quality.chat_workflow_cleanup import run as cleanup_run

        return await cleanup_run(state, root, ledger, public, phase == "cleanup")
    raise ValueError(phase)
