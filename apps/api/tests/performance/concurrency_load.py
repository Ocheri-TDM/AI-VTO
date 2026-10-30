"""Bounded, destructive-only-to-prefixed-data concurrency acceptance harness.

Run from apps/api with the API already listening::
    python tests/performance/concurrency_load.py --base-url http://127.0.0.1:8000

The script creates ten isolated users/projects, runs staged requests, writes one JSON
result, and removes only projects/users carrying its random ``codex-load-*`` prefix.
It never mutates catalog, observations, crawler checkpoints, or background jobs.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import httpx

SIMPLE = [
    "ручки", "карандаши", "термокружки", "ежедневники", "повербанки",
    "рюкзаки", "зонты", "флешки", "футболки", "шоперы",
]
COMPOSITE = [
    "Собери 2 набора из термокружек и ручек",
    "Собери 3 набора из ежедневников и карандашей",
    "Собери 2 набора из рюкзаков и зонтов",
    "Собери 4 набора из флешек и повербанков",
    "Собери 2 набора из футболок и шоперов",
]
PURPOSE = (
    "Мне нужно что-нибудь небанальное для сотрудников, кто постоянно в дороге. "
    "Без кружек и футболок. Бюджет 7000 тенге на предмет, штук 80"
)
MULTI_ITEM = (
    "Собери ровно 30 разных наборов из зонтов, дождевиков, носков и термосов. "
    "В каждом по 3 разных товара каждой категории, от 300 штук. "
    "Не повторяй товары между наборами"
)


@dataclass
class Sample:
    scenario: str
    user: int
    operation: str
    status: int
    latency_ms: float
    ok: bool
    detail: str = ""


@dataclass
class Actor:
    number: int
    name: str
    client: httpx.AsyncClient
    user_id: str
    chat_id: str
    project_id: str
    research_id: str | None = None
    hard_id: str | None = None


def percentile(values: list[float], percentile_value: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(percentile_value * len(ordered)) - 1))
    return round(ordered[index], 2)


async def measured(
    samples: list[Sample], scenario: str, actor: Actor, operation: str,
    call: Callable[[], Awaitable[httpx.Response]], expected: set[int] = {200, 201, 202},
) -> httpx.Response:
    started = time.perf_counter()
    try:
        response = await call()
        latency = (time.perf_counter() - started) * 1000
        ok = response.status_code in expected
        samples.append(Sample(scenario, actor.number, operation, response.status_code, latency, ok,
                              "" if ok else response.text[:300]))
        return response
    except Exception as exc:
        latency = (time.perf_counter() - started) * 1000
        samples.append(Sample(scenario, actor.number, operation, 0, latency, False, type(exc).__name__))
        raise


async def setup_actor(base_url: str, prefix: str, number: int) -> Actor:
    client = httpx.AsyncClient(base_url=base_url, timeout=httpx.Timeout(180), trust_env=False)
    name = f"{prefix}-u{number:02d}"
    password = f"Load-{uuid.uuid4()}-Aa1!"
    response = await client.post("/api/auth/register", json={"name": name, "password": password})
    response.raise_for_status()
    user_id = response.json()["id"]
    response = await client.post("/api/chats", json={"project_name": name})
    response.raise_for_status()
    chat_id = response.json()["id"]
    projects = (await client.get("/api/projects")).json()
    project = next(item for item in projects if item["chat_id"] == chat_id)
    return Actor(number, name, client, user_id, chat_id, project["id"])


async def simple(samples: list[Sample], scenario: str, actor: Actor, query: str) -> httpx.Response:
    response = await measured(samples, scenario, actor, "simple", lambda: actor.client.post(
        f"/api/projects/{actor.project_id}/requests", json={"query": query}))
    if response.status_code < 300:
        body = response.json()
        research = body.get("research") or {}
        actor.research_id = research.get("id", actor.research_id)
        if body.get("mode") not in {"SIMPLE", "REFINEMENT"}:
            samples[-1].ok = False
            samples[-1].detail = f"unexpected mode {body.get('mode')}"
    return response


async def composite(samples: list[Sample], scenario: str, actor: Actor, query: str) -> httpx.Response:
    response = await measured(samples, scenario, actor, "composite", lambda: actor.client.post(
        f"/api/projects/{actor.project_id}/requests", json={"query": query}))
    if response.status_code < 300:
        body = response.json()
        hard = body.get("hard_request") or {}
        actor.hard_id = hard.get("id", actor.hard_id)
        if body.get("mode") != "COMPOSITE":
            samples[-1].ok = False
            samples[-1].detail = f"unexpected mode {body.get('mode')}"
    return response


async def await_hard_terminal(
    samples: list[Sample], scenario: str, actor: Actor, started: float,
    timeout_seconds: float = 90,
) -> None:
    deadline = time.perf_counter() + timeout_seconds
    terminal = None
    while actor.hard_id and time.perf_counter() < deadline:
        response = await actor.client.get(f"/api/hard-requests/{actor.hard_id}")
        terminal = response.json() if response.status_code == 200 else None
        if terminal and terminal.get("status") in {
            "COMPLETED_FULL", "COMPLETED_PARTIAL", "NO_MATCH",
            "UNSUPPORTED_CONSTRAINT", "FAILED",
        }:
            break
        await asyncio.sleep(.25)
    status = (terminal or {}).get("status", "TIMEOUT")
    ok = status in {"COMPLETED_FULL", "COMPLETED_PARTIAL", "NO_MATCH"}
    if scenario == "multi-item-terminal":
        plan = (terminal or {}).get("plan", {})
        ok = ok and plan.get("items_per_position") == 3 and not plan.get("unsupported_constraints")
    samples.append(Sample(
        scenario, actor.number, "terminal", 200 if terminal else 0,
        (time.perf_counter() - started) * 1000, ok, status,
    ))


async def purpose(samples: list[Sample], scenario: str, actor: Actor) -> httpx.Response:
    return await simple(samples, scenario, actor, PURPOSE)


async def required_multi_item(samples: list[Sample], actor: Actor) -> None:
    started = time.perf_counter()
    await composite(samples, "multi-item-http", actor, MULTI_ITEM)
    await await_hard_terminal(samples, "multi-item-terminal", actor, started)


async def validate_simple_isolation(samples: list[Sample], actors: list[Actor]) -> None:
    for actor in actors:
        response = await measured(samples, "simple-isolation", actor, "history", lambda a=actor:
                                  a.client.get(f"/api/projects/{a.project_id}/queries"))
        if response.status_code == 200:
            rows = response.json()
            foreign = [row for row in rows if row.get("project_id") not in {None, actor.project_id}]
            if foreign:
                samples[-1].ok = False
                samples[-1].detail = "foreign project query returned"
        if actor.research_id:
            page = await measured(samples, "simple-isolation", actor, "pagination", lambda a=actor:
                                  a.client.get(f"/api/researches/{a.research_id}/products?limit=100"))
            if page.status_code == 200 and len(page.json().get("products", [])) > 100:
                samples[-1].ok = False
                samples[-1].detail = "page exceeds 100"


async def security_checks(samples: list[Sample], actors: list[Actor]) -> None:
    owner, intruder = actors[0], actors[1]
    for operation, path in [
        ("foreign-project", f"/api/projects/{owner.project_id}/queries"),
        ("foreign-research", f"/api/researches/{owner.research_id}" if owner.research_id else "/api/researches/00000000-0000-0000-0000-000000000000"),
    ]:
        await measured(samples, "auth-ownership", intruder, operation,
                       lambda p=path: intruder.client.get(p), expected={403, 404})
    if owner.hard_id:
        await measured(samples, "auth-ownership", intruder, "foreign-hard",
                       lambda: intruder.client.get(f"/api/hard-requests/{owner.hard_id}"), expected={403, 404})
        await measured(samples, "auth-ownership", intruder, "foreign-sse",
                       lambda: intruder.client.get(f"/api/hard-requests/{owner.hard_id}/events"), expected={403, 404})
    isolated = httpx.AsyncClient(base_url=str(owner.client.base_url), timeout=20, trust_env=False)
    try:
        dummy = Actor(99, "logged-out", isolated, "", owner.chat_id, owner.project_id)
        await measured(samples, "auth-ownership", dummy, "no-cookie", lambda: isolated.get(
            f"/api/projects/{owner.project_id}/queries"), expected={401})
    finally:
        await isolated.aclose()


async def sequential_workspace(samples: list[Sample], actor: Actor) -> None:
    chain = [
        "термокружка", "оставь черные", "ежедневник",
        "Собери 5 наборов из термокружек, ручек и ежедневников",
        "покажи только термокружки",
    ]
    expected = ["SIMPLE", "REFINEMENT", "SIMPLE", "COMPOSITE", "SIMPLE"]
    for query, mode in zip(chain, expected, strict=True):
        response = await measured(samples, "same-project-sequential", actor, query,
                                  lambda q=query: actor.client.post(
                                      f"/api/projects/{actor.project_id}/requests", json={"query": q}))
        if response.status_code < 300:
            body = response.json()
            if body.get("mode") != mode:
                samples[-1].ok = False
                samples[-1].detail = f"expected {mode}, got {body.get('mode')}"
            actor.research_id = (body.get("research") or {}).get("id", actor.research_id)
            actor.hard_id = (body.get("hard_request") or {}).get("id", actor.hard_id)


async def mixed(samples: list[Sample], actors: list[Actor]) -> None:
    tasks: list[Awaitable[httpx.Response]] = []
    tasks.extend(simple(samples, "mixed", actors[i], SIMPLE[i]) for i in range(3))
    tasks.extend(composite(samples, "mixed", actors[i + 3], COMPOSITE[i]) for i in range(3))
    tasks.extend(simple(samples, "mixed", actors[i + 6], "оставь черные") for i in range(2))
    if actors[8].hard_id:
        tasks.append(measured(samples, "mixed", actors[8], "rebuild", lambda: actors[8].client.post(
            f"/api/hard-requests/{actors[8].hard_id}/rebuild")))
    else:
        tasks.append(composite(samples, "mixed", actors[8], COMPOSITE[3]))
    tasks.append(measured(samples, "mixed", actors[9], "history", lambda: actors[9].client.get(
        f"/api/projects/{actors[9].project_id}/queries")))
    await asyncio.gather(*tasks, return_exceptions=True)


async def purpose_mixed(samples: list[Sample], actors: list[Actor]) -> None:
    """One purpose request must not block nine unrelated project operations."""
    await simple(samples, "purpose-mixed-setup", actors[9], "ручки")
    tasks: list[Awaitable[httpx.Response]] = [purpose(samples, "purpose-mixed", actors[0])]
    tasks.extend(simple(samples, "purpose-mixed", actors[i], SIMPLE[i]) for i in range(1, 6))
    tasks.extend(composite(samples, "purpose-mixed", actors[i], COMPOSITE[i - 6]) for i in range(6, 9))
    tasks.append(simple(samples, "purpose-mixed", actors[9], "оставь черные"))
    await asyncio.gather(*tasks, return_exceptions=True)


async def recovery(samples: list[Sample], actor: Actor) -> None:
    await measured(samples, "recovery", actor, "reload-chat", lambda: actor.client.get(f"/api/chats/{actor.chat_id}"))
    await measured(samples, "recovery", actor, "history", lambda: actor.client.get(
        f"/api/projects/{actor.project_id}/queries"))
    if actor.research_id:
        response = await actor.client.get(f"/api/researches/{actor.research_id}")
        if response.status_code == 200:
            view = response.json().get("view", {})
            await measured(samples, "recovery", actor, "undo", lambda: actor.client.patch(
                f"/api/researches/{actor.research_id}/view",
                json={"version": view.get("version", 0), "restore": "undo"}), expected={200, 409})


async def cleanup(actors: list[Actor]) -> list[str]:
    notes = []
    for actor in actors:
        try:
            response = await actor.client.delete(f"/api/projects/{actor.project_id}")
            notes.append(f"project {actor.project_id}: {response.status_code}")
            await actor.client.post("/api/auth/logout")
        finally:
            await actor.client.aclose()
    return notes


async def purge_prefixed_users(prefix: str) -> int:
    # Projects have already been deleted through the ownership-checked API. This exact
    # prefix predicate removes only disposable users and their authentication sessions.
    from sqlalchemy import delete

    from app.config import Settings
    from app.database.connection import create_database
    from app.database.models import User

    engine, sessions = create_database(Settings().database_url)
    try:
        async with sessions.begin() as db:
            result = await db.execute(delete(User).where(User.normalized_name.like(prefix.casefold() + "%")))
            return result.rowcount or 0
    finally:
        await engine.dispose()


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", default="tests/performance/concurrency-load-results.json")
    args = parser.parse_args()
    prefix = "codex-load-" + uuid.uuid4().hex[:10]
    samples: list[Sample] = []
    actors: list[Actor] = []
    metadata: dict[str, Any] = {"prefix": prefix, "base_url": args.base_url, "started_at": time.time()}
    try:
        health = httpx.get(args.base_url + "/api/health", timeout=10, trust_env=False)
        health.raise_for_status()
        actors = list(await asyncio.gather(*(setup_actor(args.base_url, prefix, i + 1) for i in range(10))))
        # Single-user baseline, then required bounded stages 1, 5, and 10.
        await simple(samples, "simple-baseline", actors[0], SIMPLE[0])
        for count in (1, 5, 10):
            started = time.perf_counter()
            await asyncio.gather(*(simple(samples, f"simple-c{count}", actors[i], SIMPLE[i]) for i in range(count)),
                                 return_exceptions=True)
            metadata[f"simple_c{count}_wall_ms"] = round((time.perf_counter() - started) * 1000, 2)
        for count in (1, 5):
            started = time.perf_counter()
            await asyncio.gather(*(purpose(samples, f"purpose-c{count}", actors[i]) for i in range(count)),
                                 return_exceptions=True)
            metadata[f"purpose_c{count}_wall_ms"] = round((time.perf_counter() - started) * 1000, 2)
        await validate_simple_isolation(samples, actors)
        await asyncio.gather(*(composite(samples, "composite-c5", actors[i], COMPOSITE[i]) for i in range(5)),
                             return_exceptions=True)
        await mixed(samples, actors)
        await purpose_mixed(samples, actors)
        await required_multi_item(samples, actors[0])
        await sequential_workspace(samples, actors[0])
        await security_checks(samples, actors)
        await recovery(samples, actors[0])
    finally:
        metadata["cleanup"] = await cleanup(actors)
        metadata["users_deleted"] = await purge_prefixed_users(prefix)
        metadata["finished_at"] = time.time()
        values = [sample.latency_ms for sample in samples]
        result = {
            "metadata": metadata,
            "summary": {
                "requests": len(samples), "passed": sum(s.ok for s in samples),
                "failed": sum(not s.ok for s in samples), "p50_ms": percentile(values, .50),
                "p95_ms": percentile(values, .95), "p99_ms": percentile(values, .99),
            },
            "scenarios": {}, "samples": [asdict(sample) for sample in samples],
        }
        for scenario in sorted({sample.scenario for sample in samples}):
            group = [sample for sample in samples if sample.scenario == scenario]
            latencies = [sample.latency_ms for sample in group]
            result["scenarios"][scenario] = {
                "requests": len(group), "passed": sum(s.ok for s in group),
                "failed": sum(not s.ok for s in group), "p50_ms": percentile(latencies, .5),
                "p95_ms": percentile(latencies, .95), "p99_ms": percentile(latencies, .99),
            }
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result["summary"], ensure_ascii=False))
    return 0 if all(sample.ok for sample in samples) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
