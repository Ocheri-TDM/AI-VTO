"""Opt-in asynchronous stack diagnostics: pytest -p quality.watchdog -s."""

import asyncio
import sys

import pytest


@pytest.fixture(autouse=True)
async def task_watchdog(request):
    async def diagnose():
        await asyncio.sleep(15)
        print("QUALITY WATCHDOG", request.node.nodeid, file=sys.stderr, flush=True)
        for task in asyncio.all_tasks():
            coroutine = task.get_coro()
            chain = []
            for _ in range(20):
                frame = getattr(coroutine, "cr_frame", None) or getattr(coroutine, "gi_frame", None)
                if frame:
                    chain.append(f"{frame.f_code.co_filename}:{frame.f_lineno}:{frame.f_code.co_name}")
                coroutine = getattr(coroutine, "cr_await", None) or getattr(coroutine, "gi_yieldfrom", None)
                if coroutine is None:
                    break
            print(task.get_name(), " -> ".join(chain), file=sys.stderr, flush=True)

    probe = asyncio.create_task(diagnose())
    try:
        yield
    finally:
        probe.cancel()
        await asyncio.gather(probe, return_exceptions=True)
