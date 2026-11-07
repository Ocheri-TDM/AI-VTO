"""Bounded isolated PostgreSQL/API harness. No reset of the main application DB."""

import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import os
import platform
import random
import subprocess
import sys
import time
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
import psutil
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "apps/api"), str(ROOT / "apps/api/tests")]
from app.config import Settings
from app.database.connection import create_database
from app.domain.models import AvailabilityRecord, Product, ProductColor
from quality.corpus import SEED, VERSION, cases, holdout, matching, products

LEDGER = ROOT / ".local/search-quality-safety-ledger.json"
PUBLIC = ROOT / "apps/api/tests/quality/results"
PG_BIN = (
    ROOT / ".local/postgres-bin/node_modules/@embedded-postgres/windows-x64/native/bin"
)
CAPS = {
    "http_requests": 5000,
    "seconds": 2400,
    "concurrency": 10,
    "min_free_memory_mb": 1024,
    "max_owned_rss_mb": 1800,
    "parser_examples": 5000,
    "stateful_sequences": 200,
    "live_queries": 60,
}


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str), "utf-8"
    )


def fixture_products():
    values = []
    colors = {
        "BLACK": "чёрный",
        "RED": "красный",
        "BLUE": "синий",
        "WHITE": "белый",
        "GREEN": "зелёный",
        "ORANGE": "оранжевый",
        "GREY": "серый",
        "NAVY": "navy blue",
        "LIGHT_BLUE": "голубой",
    }
    for p in products():
        metadata = {
            "quality_fixture": p["key"],
            "parent_name": p["title"],
            "sku": p.get("sku"),
            "size": p.get("size"),
        }
        for key in ["capacity_ml", "capacity_mah", "storage_gb", "subtype"]:
            if key in p:
                metadata[key] = p[key]
        records = []
        if p.get("parent_shared"):
            metadata.update(
                stock_scope="PARENT_SHARED", parent_shared_stock=p["parent_shared"]
            )
        if p.get("previous_stock"):
            records = [
                AvailabilityRecord(
                    state="ON_HAND",
                    location_code="TEST",
                    free_quantity=p["previous_stock"],
                    observed_at=datetime(2020, 1, 1, tzinfo=UTC),
                    parser_version="quality-fixture",
                    source_label="Synthetic historical fixture",
                ),
                AvailabilityRecord(
                    state="ON_HAND",
                    location_code="TEST",
                    free_quantity=p["stock"],
                    parser_version="quality-fixture",
                    source_label="Synthetic newest zero fixture",
                ),
            ]
        if p.get("incoming"):
            records = [
                AvailabilityRecord(
                    state="INCOMING",
                    total_quantity=p["incoming"],
                    parser_version="quality-fixture",
                    source_label="Synthetic isolated fixture",
                )
            ]
        values.append(
            Product(
                id=p["id"],
                supplier="vto",
                source_url="https://vto.kz/product/quality-" + p["key"] + "/",
                name=p["title"],
                brand=p["brand"],
                material=p["material"],
                original_price=p["price"],
                original_currency="KZT",
                stock_quantity=p["stock"],
                colors=[ProductColor(original_color=colors[p["color"]])],
                metadata=metadata,
                capacity=str(p.get("capacity_ml") or p.get("capacity_mah") or "")
                or None,
                availability_records=records,
                fetched_at=datetime(2020, 1, 1, tzinfo=UTC)
                if p.get("stale")
                else datetime.now(UTC),
            )
        )
    return values


async def audit():
    settings = Settings()
    engine, sessions = create_database(settings.database_url)
    try:
        async with sessions() as db:
            version = await db.scalar(text("SHOW server_version"))
            revision = await db.scalar(text("SELECT version_num FROM alembic_version"))
            counts = (
                (
                    await db.execute(
                        text(
                            "SELECT count(*) offers,count(DISTINCT o.product_id) families,max(p.observed_at) latest_update FROM supplier_offers o LEFT JOIN product_observations p ON p.offer_id=o.id"
                        )
                    )
                )
                .mappings()
                .one()
            )
            jobs = [
                dict(r)
                for r in (
                    await db.execute(
                        text(
                            "SELECT id,status,updated_at FROM catalog_sync_jobs WHERE status IN ('RUNNING','QUEUED','PENDING','PAUSED') ORDER BY updated_at DESC LIMIT 8"
                        )
                    )
                )
                .mappings()
                .all()
            ]
            commercial = [
                dict(r)
                for r in (
                    await db.execute(
                        text(
                            "SELECT o.id,md5(coalesce(o.payload::text,'')||coalesce(p.payload::text,'')) fingerprint,p.observed_at FROM supplier_offers o LEFT JOIN product_observations p ON p.offer_id=o.id ORDER BY o.id"
                        )
                    )
                )
                .mappings()
                .all()
            ]
        return {
            "commit": (
                await asyncio.to_thread(
                    subprocess.check_output,
                    ["git", "rev-parse", "HEAD"],
                    cwd=ROOT,
                    text=True,
                )
            ).strip(),
            "dirty_paths": (
                await asyncio.to_thread(
                    subprocess.check_output,
                    ["git", "status", "--porcelain"],
                    cwd=ROOT,
                    text=True,
                )
            ).splitlines(),
            "diff_sha256": hashlib.sha256(
                await asyncio.to_thread(
                    subprocess.check_output,
                    ["git", "diff", "--binary"],
                    cwd=ROOT,
                    stderr=subprocess.DEVNULL,
                )
            ).hexdigest(),
            "python": platform.python_version(),
            "packages": {
                key: importlib.metadata.version(key)
                for key in [
                    "sqlalchemy",
                    "asyncpg",
                    "fastapi",
                    "pydantic",
                    "httpx",
                    "pytest",
                ]
            },
            "postgres": version,
            "alembic": revision,
            "index": dict(counts),
            "jobs": jobs,
            "commercial": commercial,
            "machine": {
                "platform": platform.platform(),
                "cpu": platform.processor(),
                "logical_cpus": psutil.cpu_count(),
                "physical_cpus": psutil.cpu_count(logical=False),
                "ram_bytes": psutil.virtual_memory().total,
                "available_bytes": psutil.virtual_memory().available,
            },
            "config": {
                key: getattr(settings, key)
                for key in [
                    "llm_backend",
                    "llm_model",
                    "llm_timeout_seconds",
                    "llm_concurrency",
                    "llm_queue_limit",
                    "max_active_searches",
                    "background_research_enabled",
                    "presentation_worker_enabled",
                    "api_host",
                    "api_port",
                ]
            },
        }
    finally:
        await engine.dispose()


async def prepare(state):
    ledger = Path(state.get("ledger", LEDGER))
    public = Path(state.get("public", PUBLIC))
    state["audit"] = await audit()
    directory = ROOT / ".local" / ("search-safety-" + state["run"])
    assert (
        directory.resolve().parent == (ROOT / ".local").resolve()
        and not directory.exists()
    )
    directory.mkdir()
    state.update(
        directory=str(directory),
        pg_data=str(directory / "postgres"),
        pg_port=55433,
        api_port=18000,
        users=[],
        projects=[],
        caps=CAPS,
    )
    save(ledger, state)
    env = dict(os.environ)
    init = await asyncio.to_thread(
        subprocess.run,
        [
            str(PG_BIN / "initdb.exe"),
            "-D",
            state["pg_data"],
            "-U",
            "quality_owner",
            "-A",
            "trust",
            "--encoding=UTF8",
            "--locale=C",
        ],
        check=False,
        capture_output=True,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if init.returncode:
        raise RuntimeError("Isolated initdb failed: " + init.stderr[-800:])
    with (directory / "postgres.log").open("ab") as log:
        process = await asyncio.to_thread(
            subprocess.Popen,
            [
                str(PG_BIN / "postgres.exe"),
                "-D",
                state["pg_data"],
                "-p",
                "55433",
                "-h",
                "127.0.0.1",
                "-c",
                "shared_buffers=64MB",
                "-c",
                "max_connections=40",
            ],
            stdout=log,
            stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    state["pg_pid"] = process.pid
    state["database_url"] = (
        "postgresql+asyncpg://quality_owner@127.0.0.1:55433/postgres"
    )
    save(ledger, state)
    for _ in range(40):
        try:
            e, _s = create_database(state["database_url"])
            async with e.connect() as d:
                await d.execute(text("SELECT 1"))
            await e.dispose()
            break
        except (ConnectionError, OSError):
            await asyncio.sleep(0.2)
    env["DATABASE_URL"] = state["database_url"]
    migration = await asyncio.to_thread(
        subprocess.run,
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            "apps/api/alembic.ini",
            "upgrade",
            "head",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if migration.returncode:
        raise RuntimeError("Isolated migrations failed: " + migration.stderr[-1000:])
    from app.database.index import IndexRepository

    engine, sessions = create_database(state["database_url"])
    settings = Settings(
        database_url=state["database_url"],
        llm_backend="basic",
        background_research_enabled=False,
        presentation_worker_enabled=False,
        presentation_storage=directory / "presentations",
    )
    await IndexRepository(sessions, settings).ingest(fixture_products())
    await engine.dispose()
    corpus = {
        "version": VERSION,
        "seed": SEED,
        "products": products(),
        "cases": cases(),
    }
    if not state.get("regression_extension"):
        save(ROOT / "apps/api/tests/quality/dataset.json", corpus)
        save(ROOT / "apps/api/tests/quality/holdout.json", {"seed": 927651, "cases": holdout()})
    sanitized = {
        key: value for key, value in state["audit"].items() if key != "commercial"
    }
    sanitized.update(
        dataset=VERSION,
        seed=SEED,
        caps=CAPS,
        fixture_offers=len(products()),
        isolated_pg_port=55433,
    )
    save(public / "environment.json", sanitized)
    save(ledger, state)
    print(
        "Prepared isolated PostgreSQL :55433, migrated schema, fixture offers:",
        len(products()),
    )


async def corpus_run(state, phase):
    from app.application.index_research import IndexedResearchService
    from app.database.conversations import ConversationRepository
    from app.database.index import IndexRepository
    from app.database.research import ResearchRepository
    from app.domain.query_understanding import QueryUnderstandingService

    settings = Settings(
        database_url=state["database_url"],
        llm_backend="basic",
        background_research_enabled=False,
        presentation_worker_enabled=False,
    )
    engine, sessions = create_database(state["database_url"])
    index = IndexRepository(sessions, settings)
    service = IndexedResearchService(
        ResearchRepository(sessions),
        [],
        settings,
        None,
        index,
        QueryUnderstandingService(sessions),
    )
    chat = await ConversationRepository(sessions).create("Quality isolated corpus")
    results = []
    started = time.monotonic()
    for case in holdout(seed=927652, count=100) if phase == "holdout" else cases():
        if time.monotonic() - started > CAPS["seconds"]:
            raise RuntimeError("Corpus budget exhausted")
        before = time.perf_counter()
        try:
            r = await asyncio.wait_for(service.start(chat, case["query"]), 12)
            value = await service.grid_response(r)
            actual = {p["id"] for p in value["products"]}
            while value["next_cursor"]:
                value = await service.grid_response(r, value["next_cursor"])
                actual.update(p["id"] for p in value["products"])
            expected = (
                matching(products(), case["oracle"])
                if not case.get("reject")
                else set()
            )
            success = not case.get("reject") and actual == expected
            if case.get("concepts") is not None:
                success = success and r.intent.categories == case["concepts"]
            results.append(
                {
                    "id": case["id"],
                    "query": case["query"],
                    "family": case.get("family"),
                    "pass": success,
                    "expected_ids": sorted(expected),
                    "actual_ids": sorted(actual),
                    "intent": r.intent.model_dump(mode="json"),
                    "seconds": round(time.perf_counter() - before, 4),
                    "missing": sorted(expected - actual),
                    "unrelated": sorted(actual - expected),
                }
            )
        except Exception as exc:  # noqa: BLE001 - record every failed oracle evaluation
            success = bool(case.get("reject")) and isinstance(
                exc, (ValueError, TypeError)
            )
            results.append(
                {
                    "id": case["id"],
                    "query": case["query"],
                    "family": case.get("family"),
                    "pass": success,
                    "error": type(exc).__name__,
                    "message": str(exc)[:300],
                }
            )
    save(
        PUBLIC / (phase + ".json"),
        {
            "dataset": VERSION,
            "seed": 927652 if phase == "holdout" else SEED,
            "results": results,
            "passed": sum(r["pass"] for r in results),
            "total": len(results),
        },
    )
    await engine.dispose()
    print("Corpus", phase, "passed", sum(r["pass"] for r in results), "/", len(results))
    for item in [r for r in results if not r["pass"]][:12]:
        print(
            item["id"],
            item.get("family"),
            item.get("error")
            or f"missing={len(item['missing'])} extra={len(item['unrelated'])}",
        )
    if phase in ("corpus", "holdout") and any(not r["pass"] for r in results):
        raise AssertionError("Labelled acceptance failed; sanitized evidence saved")


async def serve(state):
    import socket

    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", 18000)) == 0:
            raise RuntimeError(
                "Isolated API port is occupied; verify ledger before reuse"
            )
    env = dict(
        os.environ,
        DATABASE_URL=state["database_url"],
        API_PORT="18000",
        API_HOST="127.0.0.1",
        LLM_BACKEND="basic",
        BACKGROUND_RESEARCH_ENABLED="false",
        PRESENTATION_WORKER_ENABLED="false",
        AUTH_REQUIRED="true",
        PRESENTATION_STORAGE=str(Path(state["directory"]) / "presentations"),
        SEARCH_REQUESTS_PER_MINUTE="10000",
        DEBUG_AGENT="true",
    )
    with (Path(state["directory"]) / "api.log").open("ab") as log:
        process = await asyncio.to_thread(
            subprocess.Popen,
            [sys.executable, "-m", "app"],
            cwd=ROOT / "apps/api",
            env=env,
            stdout=log,
            stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    state["api_pid"] = process.pid
    save(Path(state.get("ledger", LEDGER)), state)
    async with httpx.AsyncClient() as c:
        for _ in range(60):
            try:
                r = await c.get("http://127.0.0.1:18000/api/health")
                if r.status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.2)
        else:
            raise RuntimeError("Isolated API did not become healthy")
    print("Isolated API running :18000")


def stop_isolated_api(state):
    parent = psutil.Process(state["api_pid"])
    assert (
        "-m app" in " ".join(parent.cmdline())
        and Path(parent.cwd()).resolve() == (ROOT / "apps/api").resolve()
    )
    processes = [parent, *parent.children(recursive=True)]
    listeners = [
        c.pid
        for c in psutil.net_connections(kind="tcp")
        if c.laddr and c.laddr.port == 18000 and c.status == "LISTEN"
    ]
    assert listeners and set(listeners) <= {p.pid for p in processes}
    for process in reversed(processes):
        process.terminate()
    psutil.wait_procs(processes, timeout=5)


async def http_run(state, phase):
    from quality.acceptance import (
        BudgetClient,
        load,
        mixed,
        mutation_control,
        register,
        stateful,
    )

    BudgetClient.counter = state.get("isolated_http_count", 0)
    BudgetClient.deadline = time.monotonic() + CAPS["seconds"]
    clients = [
        BudgetClient(base_url="http://127.0.0.1:18000", timeout=50) for _ in range(10)
    ]
    projects = []
    try:
        if not state.get("http_users"):
            state["http_users"] = []
            for n, c in enumerate(clients):
                p = await register(c, "quality-" + uuid4().hex[:14])
                state["http_users"].append({"project": p, "cookies": dict(c.cookies)})
                save(LEDGER, state)
        for c, u in zip(clients, state["http_users"]):
            c.cookies.update(u["cookies"])
            projects.append(u["project"])
        before = BudgetClient.counter
        worker = None
        if phase == "load":
            stop = asyncio.Event()
            worker = asyncio.create_task(controlled_refresh(state, stop))
            pdf = asyncio.create_task(controlled_pdf(state, clients[0], projects[0]))
        try:
            result = (
                await stateful(clients[:2], projects[:2], 200)
                if phase == "stateful"
                else await mixed(clients, projects)
                if phase == "mixed"
                else await load(clients, projects)
            )
            result["runtime"] = (await clients[0].get("/api/dev/runtime")).json()
        finally:
            if worker:
                stop.set()
                result_background = await worker
        if worker:
            result["background"] = result_background
            result["pdf_during_load"] = await pdf
        result.update(
            http_requests=BudgetClient.counter - before,
            mutation_control=mutation_control(),
            status="PASS",
        )
        save(PUBLIC / (phase + ".json"), result)
    finally:
        state["isolated_http_count"] = BudgetClient.counter
        save(LEDGER, state)
        for c in clients:
            await c.aclose()


async def controlled_refresh(state, stop):
    from app.application.background_index import BackgroundIndex
    from app.database.index import IndexRepository

    assert ":55433/" in state["database_url"]
    settings = Settings(
        _env_file=None,
        database_url=state["database_url"],
        background_research_enabled=False,
        presentation_worker_enabled=False,
        research_request_delay_seconds=0.2,
    )
    e, ss = create_database(state["database_url"])
    index = IndexRepository(ss, settings)
    fixtures = fixture_products()[:8]
    by_url = {p.source_url: p for p in fixtures}

    class Source:
        supplier = "vto"

        def observation_group_key(self, url):
            return url

        async def research_products(self, url):
            await asyncio.sleep(0.01)
            return [by_url[url]]

    worker = BackgroundIndex(index, [Source()], settings)
    completed = 0
    statuses = []
    try:
        while not stop.is_set():
            job_id = await index.enqueue(
                "vto",
                "TARGETED_REFRESH",
                checkpoint={
                    "pending": list(by_url),
                    "query": "isolated-load-refresh-" + str(completed),
                },
            )
            job = await index.claim(worker.owner, job_ids=[job_id])
            if job:
                await worker.run(job)
                completed += 1
                async with ss() as db:
                    statuses.append(
                        await db.scalar(
                            text("SELECT status FROM catalog_sync_jobs WHERE id=:id"),
                            {"id": job_id},
                        )
                    )
            try:
                await asyncio.wait_for(stop.wait(), timeout=1)
            except TimeoutError:
                pass
        return {
            "jobs": completed,
            "statuses": statuses,
            "source_http_requests": 0,
            "source": "fixture provider through existing BackgroundIndex queue/lease/staging/checkpoints",
        }
    finally:
        await e.dispose()


async def controlled_pdf(state, c, project):
    import io

    from app.main import create_app
    from PIL import Image, ImageDraw

    before = time.perf_counter()
    base = "/api/projects/" + project["id"] + "/collection"
    (
        await c.post(base + "/items", json={"offer_id": products()[0]["id"]})
    ).raise_for_status()
    col = (await c.get(base)).json()
    response = await c.post(
        "/api/projects/" + project["id"] + "/presentations",
        json={"collection_version": col["version"]},
    )
    response.raise_for_status()
    doc = response.json()
    picture = Image.new("RGBA", (320, 320), (0, 0, 0, 0))
    ImageDraw.Draw(picture).rectangle((145, 35, 175, 285), fill="#163E52")
    data = io.BytesIO()
    picture.save(data, format="PNG")
    # Only one fixture item needs an asset; other selected fixtures use the same
    # explicit synthetic control image, never a substituted live VTO photograph.
    for item in doc["draft"]["items"]:
        response = await c.post(
            "/api/presentations/" + doc["id"] + "/items/" + item["offer_id"] + "/image",
            params={"version": doc["version"]},
            content=data.getvalue(),
            headers={"Content-Type": "image/png"},
        )
        response.raise_for_status()
        doc = response.json()
    response = await c.post(
        "/api/presentations/" + doc["id"] + "/generate",
        json={"version": doc["version"]},
    )
    response.raise_for_status()
    settings = Settings(
        _env_file=None,
        database_url=state["database_url"],
        llm_backend="basic",
        auth_required=True,
        background_research_enabled=False,
        presentation_worker_enabled=False,
        presentation_storage=Path(state["directory"]) / "presentations",
    )
    app = create_app(settings, [])
    try:
        job = await app.state.presentations.claim_next()
        assert job
        await app.state.presentations.execute(job)
        value = (await c.get("/api/presentations/" + doc["id"])).json()["revisions"][0]
        assert value["result"].get("pages")
        return {
            "status": value["status"],
            "pages": len(value["result"]["pages"]),
            "seconds": time.perf_counter() - before,
            "peak_worker_rss_bytes": value["result"].get("peak_rss_bytes"),
            "source": "synthetic isolated snapshot, existing real PDF worker",
        }
    finally:
        await app.state.engine.dispose()


def properties():
    from app.domain.catalog_identity import BrandRegistry, resolve_identity
    from app.domain.query_constraints import parse_constraints

    rng = random.Random(SEED + 2)
    started = time.monotonic()
    failures = []
    unique = set()
    for n in range(5000):
        amount = rng.randint(1, 100000)
        price = rng.randint(1, 1000000)
        ml = rng.choice([250, 330, 500, 750, 1000])
        op = rng.choice(["от", "не меньше", "больше"])
        word = rng.choice(["Бутылки", "бутылки", "Найди бутылки"])
        query = rng.choice(
            [
                f"{word} {ml} мл, {op} {amount} штук, до {price} тенге",
                f"{word} {ml}мл {op} {amount}шт. Не дороже {price} ₸",
            ]
        )
        c, _mask = parse_constraints(query)
        unique.add(query)
        ok = (
            c.quantity_min == amount
            and c.price_max == price
            and c.capacity_ml == ml
            and c.quantity_min_exclusive == (op == "больше")
        )
        i = resolve_identity(query, BrandRegistry([]))
        ok = ok and not i.models and not i.brands
        if not ok:
            failures.append(
                {
                    "seed_index": n,
                    "query": query,
                    "constraints": c.model_dump(),
                    "models": i.models,
                }
            )
        if time.monotonic() - started > 120:
            raise RuntimeError("Offline watchdog budget exceeded")
    result = {
        "seed": SEED + 2,
        "examples": 5000,
        "unique_inputs": len(unique),
        "duplicates": 5000 - len(unique),
        "scope": "numeric role / schema / identity masking; not arbitrary semantic fuzz",
        "failures": failures,
        "seconds": time.monotonic() - started,
        "status": "FAIL" if failures else "PASS",
    }
    save(PUBLIC / "properties.json", result)
    print("Properties", 5000 - len(failures), "/5000")
    if failures:
        raise AssertionError("Property acceptance failed; sanitized evidence saved")


async def http_baseline(state):
    from app.main import create_app

    settings = Settings(
        database_url=state["database_url"],
        llm_backend="basic",
        background_research_enabled=False,
        presentation_worker_enabled=False,
        presentation_storage=Path(state["directory"]) / "presentations",
    )
    app = create_app(settings, [])
    clients = [
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://quality.test",
            timeout=15,
        )
        for _ in range(2)
    ]
    projects = []
    results = []
    try:
        for n, c in enumerate(clients):
            u = (
                await c.post(
                    "/api/auth/register",
                    json={
                        "name": "quality-" + uuid4().hex[:12] + "-" + str(n),
                        "password": "Synthetic-test-password-123",
                    },
                )
            ).json()
            await c.post(
                "/api/chats", json={"project_name": "Quality isolated " + str(n)}
            )
            projects.append((await c.get("/api/projects")).json()[0]["id"])
            state.setdefault("isolated_users", []).append(
                {"id": u["id"], "cookies": dict(c.cookies)}
            )
        state["isolated_projects"] = projects
        save(LEDGER, state)
        c = clients[0]
        endpoint = "/api/projects/" + projects[0] + "/requests"
        initial = (await c.post(endpoint, json={"query": "ручки"})).json()["research"]
        render = app.state.research.grid_response

        async def broken(r, *args, **kwargs):
            if r.intent.raw_query == "бутылки":
                raise RuntimeError("synthetic serialization fault")
            return await render(r, *args, **kwargs)

        app.state.research.grid_response = broken
        response = await c.post(endpoint, json={"query": "бутылки"})
        app.state.research.grid_response = render
        history = (await c.get("/api/projects/" + projects[0] + "/queries")).json()
        active = next(r for r in history if r["is_active"])
        results.append(
            {
                "test": "serialization_failure_preserves_workspace",
                "status": response.status_code,
                "pass": active["research_session_id"] == initial["id"],
            }
        )
        await clients[1].post(
            "/api/projects/" + projects[1] + "/requests", json={"query": "Molti"}
        )
        await c.post(endpoint, json={"query": "ручки"})
        for label, query, extras, headers in [
            ("origin_rejection", "ручки", {}, {"Origin": "http://127.0.0.1:3999"}),
            (
                "unknown_server_fields",
                "ручки",
                {"owner_id": "SYNTHETIC-FOREIGN", "admin": True},
                {},
            ),
            ("body_limit", "ручки", {"padding": "x" * 70000}, {}),
        ]:
            response = await c.post(
                endpoint, json={"query": query, **extras}, headers=headers
            )
            results.append(
                {
                    "test": label,
                    "status": response.status_code,
                    "pass": response.status_code in (400, 403, 413, 422),
                }
            )
        old = app.state.research.grid_plan
        entered = asyncio.Event()
        release = asyncio.Event()

        async def slow(*args, **kwargs):
            entered.set()
            await release.wait()
            return await old(*args, **kwargs)

        app.state.research.grid_plan = slow
        pending = asyncio.create_task(c.post(endpoint, json={"query": "ручки"}))
        await entered.wait()
        async with app.state.sessions() as db:
            count = await db.scalar(
                text(
                    "SELECT count(*) FROM pg_stat_activity WHERE pid<>pg_backend_pid() AND state='idle in transaction' AND query LIKE '%pg_advisory_lock%'"
                )
            )
        results.append(
            {
                "test": "no_project_transaction_during_planning",
                "idle_transactions": count,
                "pass": count == 0,
            }
        )
        release.set()
        await pending
        app.state.research.grid_plan = old
        save(PUBLIC / "baseline-http.json", {"results": results})
        print(json.dumps(results))
    finally:
        for c in clients:
            await c.aclose()
        await app.state.engine.dispose()


async def deploy(state):
    settings = Settings()
    assert ":55432/" in settings.database_url
    e, ss = create_database(settings.database_url)
    async with ss() as db:
        assert not await db.scalar(
            text(
                "SELECT count(*) FROM supplier_sync_states WHERE lease_owner IS NOT NULL AND lease_until>now()"
            )
        ), "Wait for ordinary supplier slice to finish"
        assert not await db.scalar(
            text(
                "SELECT count(*) FROM presentation_revisions WHERE status IN ('PREPARING_ASSETS','PLANNING_DESIGN','RENDERING','VALIDATING')"
            )
        ), "Do not interrupt a real presentation"
        head = await db.scalar(text("SELECT version_num FROM alembic_version"))
    await e.dispose()
    migration = await asyncio.to_thread(
        subprocess.run,
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            "apps/api/alembic.ini",
            "upgrade",
            "head",
        ],
        cwd=ROOT,
        env=dict(os.environ, DATABASE_URL=settings.database_url),
        capture_output=True,
        text=True,
        check=False,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if migration.returncode:
        raise RuntimeError(
            "Main additive migration failed: " + migration.stderr[-1000:]
        )
    listeners = [
        c.pid
        for c in psutil.net_connections(kind="tcp")
        if c.laddr and c.laddr.port == 8000 and c.status == "LISTEN"
    ]
    assert len(listeners) == 1
    child = psutil.Process(listeners[0])
    parent = child.parent()
    assert Path(child.cwd()).resolve() == (
        ROOT / "apps/api"
    ).resolve() and "-m app" in " ".join(child.cmdline())
    assert parent and "-m app" in " ".join(parent.cmdline()) and not child.children()
    child.terminate()
    parent.terminate()
    psutil.wait_procs([child, parent], timeout=5)
    logfile = ROOT / ".local" / ("api-search-safety-" + state["run"] + ".log")
    with logfile.open("ab") as log:
        process = await asyncio.to_thread(
            subprocess.Popen,
            [sys.executable, "-m", "app"],
            cwd=ROOT / "apps/api",
            env=dict(os.environ, DATABASE_URL=settings.database_url),
            stdout=log,
            stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    state["main_api_pid"] = process.pid
    state["main_api_log"] = str(logfile)
    save(LEDGER, state)
    async with httpx.AsyncClient() as c:
        for _ in range(60):
            try:
                response = await c.get("http://127.0.0.1:8000/api/health")
                if response.status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.2)
        else:
            raise RuntimeError("API health did not recover after normal deployment")
    save(
        PUBLIC / "deployment.json",
        {
            "migration_before": head,
            "migration_after": "0023_request_epoch",
            "api_health": 200,
            "supplier_lease_active_at_restart": False,
            "postgres_stopped": False,
            "ollama_stopped": False,
        },
    )
    print("Main API deployed with additive migration; health HTTP 200")


async def restore_stateful(state):
    from quality.acceptance import BudgetClient

    result = json.loads((PUBLIC / "stateful.json").read_text("utf-8"))
    cutoff = datetime.fromtimestamp((PUBLIC / "stateful.json").stat().st_mtime, UTC)
    by_case = {c["id"]: c for c in cases()}
    engine, sessions = create_database(state["database_url"])
    BudgetClient.counter = state["isolated_http_count"]
    BudgetClient.deadline = time.monotonic() + 120
    checks = 0
    try:
        for who in (0, 1):
            user = state["http_users"][who]
            async with sessions() as db:
                rows = (
                    (
                        await db.execute(
                            text(
                                "SELECT q.query_text,r.id,r.payload FROM project_queries q JOIN research_sessions r ON r.id=q.research_session_id WHERE q.project_id=:project AND q.created_at<=:cutoff ORDER BY q.created_at DESC LIMIT 100"
                            ),
                            {"project": user["project"]["id"], "cutoff": cutoff},
                        )
                    )
                    .mappings()
                    .all()[::-1]
                )
            assert len(rows) == 100
            async with BudgetClient(
                base_url="http://127.0.0.1:18000", cookies=user["cookies"], timeout=20
            ) as c:
                for n, row in enumerate(rows):
                    entry = result["results"][n * 2 + who]
                    case = by_case[entry["case"]]
                    assert (
                        unicodedata.normalize("NFKC", row["query_text"]).strip()
                        == unicodedata.normalize("NFKC", case["query"]).strip()
                    ), (who, n, ascii(row["query_text"]), ascii(case["query"]))
                    response = await c.get("/api/researches/" + row["id"])
                    response.raise_for_status()
                    value = response.json()
                    expected = matching(products(), case["oracle"])
                    assert value["matching_count"] == len(expected)
                    assert {p["id"] for p in value["products"]} <= expected
                    branches = [
                        b
                        for b in row["payload"]["intent"]["branches"]
                        if b["category"] != "*"
                    ]
                    entry["actions"] = 4 + bool(expected) + (4 if branches else 0)
                    entry["persisted_history_readback"] = "PASS"
                    checks += 1
        result["persisted_history_reads"] = checks
        result["history_readback_http_requests"] = 200
        result["actions_definition"] = (
            "logical UI/API actions; pagination subrequests counted separately in http_requests"
        )
        save(PUBLIC / "stateful.json", result)
        print("Persisted stateful history reads", checks)
    finally:
        state["isolated_http_count"] = BudgetClient.counter
        save(LEDGER, state)
        await engine.dispose()


async def restart_web(state):
    listeners = [
        c.pid
        for c in psutil.net_connections(kind="tcp")
        if c.laddr and c.laddr.port == 3000 and c.status == "LISTEN"
    ]
    assert len(listeners) == 1
    process = psutil.Process(listeners[0])
    assert Path(process.cwd()).resolve() == (ROOT / "apps/web").resolve()
    assert "next" in " ".join(process.cmdline()) and "start" in process.cmdline()
    process.terminate()
    await asyncio.to_thread(process.wait, 5)
    logfile = ROOT / ".local" / ("web-search-safety-" + state["run"] + ".log")
    with logfile.open("ab") as log:
        process = await asyncio.to_thread(
            subprocess.Popen,
            [
                "C:/Program Files/nodejs/node.exe",
                "node_modules/next/dist/bin/next",
                "start",
                "--hostname",
                "127.0.0.1",
            ],
            cwd=ROOT / "apps/web",
            stdout=log,
            stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    state["main_web_pid"], state["main_web_log"] = process.pid, str(logfile)
    save(LEDGER, state)
    async with httpx.AsyncClient() as c:
        for _ in range(50):
            try:
                if (await c.get("http://127.0.0.1:3000/api/health")).status_code == 200:
                    print("Frontend and API proxy HTTP 200")
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.2)
    raise RuntimeError("Frontend did not recover after production build")


async def characterize(state):
    from quality.acceptance import BudgetClient, percentiles

    BudgetClient.counter = state["isolated_http_count"]
    BudgetClient.deadline = time.monotonic() + 120
    engine, sessions = create_database(state["database_url"])
    async with sessions() as db:
        ids = {
            r["project_id"]: r["research_session_id"]
            for r in (
                await db.execute(
                    text(
                        "SELECT q.project_id,q.research_session_id FROM project_queries q JOIN projects p ON p.active_query_id=q.id"
                    )
                )
            ).mappings()
        }
    samples = []
    observations = []
    stop = False

    async def monitor():
        while not stop:
            processes = []
            for pid in (state["api_pid"], state["pg_pid"]):
                process = psutil.Process(pid)
                processes.extend([process, *process.children(recursive=True)])
            rss = sum(p.memory_info().rss for p in processes if p.is_running())
            free = psutil.virtual_memory().available // 1024**2
            if (
                rss > CAPS["max_owned_rss_mb"] * 1024**2
                or free < CAPS["min_free_memory_mb"]
            ):
                raise RuntimeError("Resource stop threshold exceeded")
            async with sessions() as db:
                snapshot = (
                    (
                        await db.execute(
                            text(
                                "SELECT count(*) connections,count(*) FILTER(WHERE state='active') active,count(*) FILTER(WHERE state='idle in transaction') idle_in_transaction FROM pg_stat_activity WHERE datname=current_database()"
                            )
                        )
                    )
                    .mappings()
                    .one()
                )
            observations.append(
                {"rss_bytes": rss, "free_memory_mb": free, **dict(snapshot)}
            )
            await asyncio.sleep(0.1)

    task = asyncio.create_task(monitor())

    async def actor(user):
        async with BudgetClient(
            base_url="http://127.0.0.1:18000", cookies=user["cookies"], timeout=15
        ) as c:
            for _ in range(4):
                started = time.perf_counter()
                r = await c.get("/api/researches/" + ids[user["project"]["id"]])
                r.raise_for_status()
                assert r.json()["source_mode"] == "index"
                samples.append(time.perf_counter() - started)

    try:
        await asyncio.gather(*(actor(u) for u in state["http_users"]))
        stop = True
        await task
        async with BudgetClient(
            base_url="http://127.0.0.1:18000", cookies=state["http_users"][0]["cookies"]
        ) as c:
            runtime = (await c.get("/api/dev/runtime")).json()
            assert (await c.get("/api/health")).status_code == 200
        assert (
            runtime["pool_checked_out"] == 0
            and runtime["admission_active"] == runtime["admission_queued"] == 0
        )
        save(
            PUBLIC / "resources.json",
            {
                "status": "PASS",
                "users": 10,
                "read_requests": 40,
                "latency": percentiles(samples),
                "peak_owned_rss_bytes": max(x["rss_bytes"] for x in observations),
                "minimum_free_memory_mb": min(
                    x["free_memory_mb"] for x in observations
                ),
                "db_peak_connections": max(x["connections"] for x in observations),
                "runtime": runtime,
                "scope": "separate final 40-request characterization; not the peak over the earlier complete load run",
            },
        )
    finally:
        stop = True
        await asyncio.gather(task, return_exceptions=True)
        state["isolated_http_count"] = BudgetClient.counter
        save(LEDGER, state)
        await engine.dispose()


async def refresh_fixtures(state):
    from app.database.index import IndexRepository
    from app.database.models import AvailabilityObservation, ProductObservation
    from sqlalchemy import select

    assert ":55433/" in state["database_url"]
    e, ss = create_database(state["database_url"])
    try:
        values = fixture_products()
        await IndexRepository(ss, Settings(database_url=state["database_url"])).ingest(
            values
        )
        stale = next(p for p in values if p.metadata["quality_fixture"] == "stale")
        old = datetime(2020, 1, 1, tzinfo=UTC)
        async with ss.begin() as db:
            row = await db.get(ProductObservation, stale.id)
            assert row and stale.id.startswith("vto:99")
            row.observed_at = row.last_verified_at = old
            row.expires_at = old
            payload = dict(row.payload, fetched_at=old.isoformat())
            for record in payload.get("availability_records", []):
                record["observed_at"] = old.isoformat()
            row.payload = payload
            for record in (
                await db.scalars(
                    select(AvailabilityObservation).where(
                        AvailabilityObservation.offer_id == stale.id
                    )
                )
            ).all():
                record.observed_at = old
        save(
            ROOT / "apps/api/tests/quality/dataset.json",
            {
                "version": VERSION,
                "seed": SEED,
                "products": products(),
                "cases": cases(),
            },
        )
        save(
            PUBLIC / "fixture-consistency.json",
            {
                "status": "PASS",
                "offers": len(values),
                "north_original_color": "navy blue",
                "stale_observation": "2020-01-01",
                "zero_history": True,
                "parent_shared": True,
                "scope": "isolated PostgreSQL only",
            },
        )
    finally:
        await e.dispose()


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "phase",
        choices=[
            "prepare",
            "baseline",
            "corpus",
            "baseline-http",
            "holdout",
            "properties",
            "serve",
            "restart",
            "stateful",
            "load",
            "mixed",
            "faults",
            "deploy",
            "live",
            "browser",
            "browser-check",
            "browser-isolated",
            "restore-stateful",
            "restart-web",
            "supplement",
            "summary",
            "cleanup",
            "resources",
            "fixtures",
            "regression",
        ],
    )
    parser.add_argument("--stage", default="before")
    arguments = parser.parse_args()
    phase = arguments.phase
    if phase == "regression":
        from quality.regression_acceptance import run
        await run(arguments.stage)
        return
    state = (
        json.loads(LEDGER.read_text("utf-8"))
        if LEDGER.exists()
        else {"run": uuid4().hex}
    )
    if phase == "prepare":
        await prepare(state)
    elif phase == "baseline-http":
        await http_baseline(state)
    elif phase == "serve":
        await serve(state)
    elif phase == "restart":
        stop_isolated_api(state)
        await serve(state)
    elif phase in ("stateful", "load", "mixed"):
        await http_run(state, phase)
    elif phase == "properties":
        if os.environ.get("QUALITY_PROPERTY_WORKER") == "1":
            properties()
        else:
            process = await asyncio.to_thread(
                subprocess.Popen,
                [sys.executable, "-X", "utf8", str(Path(__file__)), "properties"],
                cwd=ROOT,
                env=dict(os.environ, QUALITY_PROPERTY_WORKER="1"),
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            try:
                code = await asyncio.wait_for(asyncio.to_thread(process.wait), 120)
                assert code == 0, "Offline property worker failed"
            except BaseException:
                parent = psutil.Process(process.pid)
                for child in parent.children(recursive=True):
                    child.kill()
                parent.kill()
                raise
    elif phase == "faults":
        from quality.faults import run

        save(PUBLIC / "faults.json", await run(state))
    elif phase == "deploy":
        await deploy(state)
    elif phase == "restore-stateful":
        await restore_stateful(state)
    elif phase == "restart-web":
        await restart_web(state)
    elif phase == "supplement":
        from quality.reporting import supplement

        save(
            PUBLIC / "live-sku.json", await supplement(state, lambda s: save(LEDGER, s))
        )
    elif phase == "summary":
        from quality.reporting import summarize

        value = summarize(state, ROOT)
        print(
            "Summary:",
            value["labelled"]["passed"],
            "labelled; overall",
            value["overall"],
        )
    elif phase == "cleanup":
        from quality.cleanup import run

        result = await run(state, ROOT, PG_BIN, LEDGER, PUBLIC, stop_isolated_api)
        print(
            "Cleanup:", result["status"], "; main API/frontend/PG/Ollama remain running"
        )
    elif phase == "resources":
        await characterize(state)
    elif phase == "fixtures":
        await refresh_fixtures(state)
    elif phase == "live":
        from quality.live import run

        save(PUBLIC / "live.json", await run(state, lambda s: save(LEDGER, s)))
    elif phase == "browser-check":
        process = psutil.Process(state["edge_pid"])
        assert "--user-data-dir=" + state["edge_profile"] in process.cmdline()
        from quality.browser import run

        save(PUBLIC / "browser.json", await run(state))
    elif phase == "browser-isolated":
        from quality.browser_isolated import run

        save(
            PUBLIC / "browser-isolated.json",
            await run(state, lambda s: save(LEDGER, s)),
        )
    elif phase == "browser":
        import socket

        with socket.socket() as probe:
            assert probe.connect_ex(("127.0.0.1", 9226)) != 0, (
                "Refusing to use an existing browser listener"
            )
        edge = Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe")
        assert edge.exists()
        profile = Path(state["directory"]) / "edge-profile"
        assert profile.resolve().parent == Path(state["directory"]).resolve()
        process = await asyncio.to_thread(
            subprocess.Popen,
            [
                str(edge),
                "--headless=new",
                "--disable-extensions",
                "--remote-debugging-port=9226",
                "--user-data-dir=" + str(profile),
                "--no-first-run",
                "--no-default-browser-check",
                "about:blank",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        state["edge_pid"] = process.pid
        state["edge_profile"] = str(profile)
        save(LEDGER, state)
        async with httpx.AsyncClient() as c:
            for _ in range(50):
                try:
                    if (
                        await c.get("http://127.0.0.1:9226/json/list")
                    ).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.2)
        from quality.browser import run

        save(PUBLIC / "browser.json", await run(state))
    else:
        await corpus_run(state, phase)


if __name__ == "__main__":
    asyncio.run(main())
