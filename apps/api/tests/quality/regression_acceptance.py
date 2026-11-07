"""Persistent extension of the search quality harness; exact-ID live resources.

No catalog resets, imported fixtures or credentials in public evidence.
"""

import asyncio
import hashlib
import json
import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
from sqlalchemy import text

from app.config import Settings
from app.database.connection import create_database

ROOT = Path(__file__).resolve().parents[4]
LEDGER = ROOT / ".local/search-understanding-ledger.json"
PUBLIC = ROOT / "docs/screenshots/search-understanding-throughput"
CONTROLS = {
    "A": "красные ручки блокноты термокружки и кепки. тираж 200",
    "B": "красные ручки блокноты термокружки и бейсболка. тираж 200",
    "C": "красные. ручки. блокноты. термокружки. и. кепки. тираж. 200.",
    "D": "красные, ручки, блокноты, термокружки, и, бейсболки, тираж, 200",
}


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), "utf-8")


async def call(c, method, path, **kwargs):
    r = await c.request(method, path, **kwargs)
    if r.status_code >= 400:
        raise AssertionError((method, path, r.status_code, r.text[:600]))
    return r.json() if r.content else None


async def prepare():
    assert not LEDGER.exists(), "Existing ledger must be resumed, not overwritten"
    with socket.socket() as probe:
        assert probe.connect_ex(("127.0.0.1", 8000)) != 0, "Main API port must be free; never claim ownership of an existing process"
    settings = Settings()
    engine, sessions = create_database(settings.database_url)
    state = {"run": secrets.token_hex(8), "users": [], "projects": []}
    try:
        async with sessions() as db:
            await db.execute(text("SET TRANSACTION READ ONLY"))
            baseline = {
                "migration": await db.scalar(text("SELECT version_num FROM alembic_version")),
                "settings": {
                    k: getattr(settings, k)
                    for k in (
                        "llm_backend",
                        "llm_model",
                        "vto_page_size",
                        "vto_request_delay_seconds",
                        "search_requests_per_minute",
                        "supplier_failure_backoff_minutes",
                        "supplier_failure_backoff_max_hours",
                    )
                },
            }
            baseline["jobs"] = [
                dict(r)
                for r in (
                    await db.execute(
                        text(
                            "SELECT id,kind,status,error,updated_at,available_at,checkpoint,metrics FROM catalog_sync_jobs"
                        )
                    )
                ).mappings()
            ]
            baseline["supplier_states"] = [
                dict(r) for r in (await db.execute(text("SELECT * FROM supplier_sync_states"))).mappings()
            ]
            baseline["protected"] = {}
            for table, order in [
                ("users", "id"),
                ("projects", "id"),
                ("project_collection_items", "project_id,offer_id"),
                ("presentation_documents", "id"),
                ("presentation_revisions", "id"),
                ("commercial_offer_shares", "document_id"),
            ]:
                baseline["protected"][table] = dict(
                    (
                        await db.execute(
                            text(
                                f"SELECT count(*) count,md5(string_agg(row_to_json(t)::text,'' ORDER BY {order})) fingerprint FROM {table} t"
                            )
                        )
                    )
                    .mappings()
                    .one()
                )
            baseline["catalog"] = dict(
                (
                    await db.execute(
                        text(
                            "SELECT count(*) offers,count(DISTINCT product_id) families FROM supplier_offers"
                        )
                    )
                )
                .mappings()
                .one()
            )
        state["baseline"] = baseline
        state["key_hash"] = hashlib.sha256(
            (settings.presentation_storage / "keys/commercial-offers.key").read_bytes()
        ).hexdigest()
        save(LEDGER, state)
        with (
            (ROOT / ".local/api-search-regression.log").open("ab") as out,
            (ROOT / ".local/api-search-regression.err.log").open("ab") as err,
        ):
            process = await asyncio.to_thread(
                subprocess.Popen,
                [sys.executable, "-m", "app"],
                cwd=ROOT / "apps/api",
                stdout=out,
                stderr=err,
                env=dict(os.environ, BACKGROUND_RESEARCH_ENABLED="false"),
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        state["api_wrapper"] = process.pid
        save(LEDGER, state)
        async with httpx.AsyncClient(base_url="http://127.0.0.1:8000", timeout=90) as c:
            for _ in range(100):
                try:
                    if (await c.get("/api/health")).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.2)
            for n in range(2):
                c.cookies.clear()
                name, password = "understanding-" + state["run"] + "-" + str(n), secrets.token_urlsafe(24)
                u = await call(c, "POST", "/api/auth/register", json={"name": name, "password": password})
                state["users"].append(
                    {"id": u["id"], "name": name, "password": password, "cookies": dict(c.cookies)}
                )
                save(LEDGER, state)
        print(
            "Recorded baseline; two isolated HTTP accounts; main API background disabled for parser reproduction"
        )
    finally:
        await engine.dispose()


async def controls(label):
    state = json.loads(LEDGER.read_text("utf-8"))
    evidence = {"label": label, "cases": []}
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:8000", cookies=state["users"][0]["cookies"], timeout=150
    ) as c:
        for contextual in (False, True):
            for name, query in CONTROLS.items():
                chat = await call(
                    c, "POST", "/api/chats", json={"project_name": "Understanding " + label + " " + name}
                )
                project = next(p for p in await call(c, "GET", "/api/projects") if p["chat_id"] == chat["id"])
                state["projects"].append(project["id"])
                save(LEDGER, state)
                endpoint = "/api/projects/" + project["id"] + "/requests"
                if contextual:
                    await call(c, "POST", endpoint, json={"query": "ручка Tippo"})
                start = time.perf_counter()
                response = await c.post(endpoint, json={"query": query})
                data = response.json()
                result = {
                    "name": name,
                    "after_tippo": contextual,
                    "query": query,
                    "http_status": response.status_code,
                    "request_id": response.headers.get("x-request-id"),
                    "seconds": round(time.perf_counter() - start, 3),
                }
                if response.status_code in (200, 202):
                    research = data["research"]
                    result.update(
                        intent=research["intent"],
                        total=research.get("matching_count"),
                        response_fields=list(research),
                        timings=research.get("timings"),
                        product_ids=[p["id"] for p in research["products"]],
                        constraints=research.get("effective_filters"),
                        branches=research.get("query_categories"),
                        research_id=research["id"],
                    )
                    page = research
                    while page.get("next_cursor"):
                        page = await call(
                            c,
                            "GET",
                            f"/api/researches/{research['id']}/products",
                            params={"cursor": page["next_cursor"]},
                        )
                        result["product_ids"].extend(p["id"] for p in page["products"])
                    result["product_ids"] = sorted(set(result["product_ids"]))
                    result["history"] = await call(c, "GET", "/api/projects/" + project["id"] + "/queries")
                else:
                    result["problem"] = data
                evidence["cases"].append(result)
                save(PUBLIC / ("controls-" + label + ".json"), evidence)
                print(
                    label,
                    name,
                    "after Tippo" if contextual else "new",
                    response.status_code,
                    result.get("intent", {}).get("categories"),
                    result.get("intent", {}).get("models"),
                    flush=True,
                )


async def run(stage):
    if stage in (
        "vto-baseline",
        "vto-live",
        "vto-replay",
        "migration-chain",
        "restart-main",
        "restore-defaults",
        "browser",
        "verify",
        "audit-controls",
        "cleanup",
    ):
        from quality.throughput_acceptance import run as throughput_run

        return await throughput_run(stage)
    if stage == "prepare":
        await prepare()
    elif stage in ("before", "after"):
        await controls(stage)
    elif stage == "capture-before-pages":
        state = json.loads(LEDGER.read_text("utf-8"))
        file = PUBLIC / "controls-before.json"
        evidence = json.loads(file.read_text("utf-8"))
        async with httpx.AsyncClient(
            base_url="http://127.0.0.1:8000", cookies=state["users"][0]["cookies"], timeout=90
        ) as c:
            for case in evidence["cases"]:
                page = await call(c, "GET", f"/api/researches/{case['research_id']}/products")
                ids = [p["id"] for p in page["products"]]
                while page.get("next_cursor"):
                    page = await call(
                        c,
                        "GET",
                        f"/api/researches/{case['research_id']}/products",
                        params={"cursor": page["next_cursor"]},
                    )
                    ids.extend(p["id"] for p in page["products"])
                case["product_ids"] = sorted(set(ids))
        save(file, evidence)
        print("Captured every pre-repair matching ID, not just the first page")
    elif stage in ("isolated", "populate-isolated"):
        from check_search_safety import fixture_products
        from check_search_safety import prepare as prepare_isolated

        state = json.loads(LEDGER.read_text("utf-8"))
        if stage == "isolated":
            child = {
                "run": state["run"] + "-regression",
                "ledger": str(ROOT / ".local/search-understanding-isolated.json"),
                "public": str(PUBLIC / "isolated"),
                "regression_extension": True,
            }
            await prepare_isolated(child)
            state["isolated"] = child
            save(LEDGER, state)
        else:
            child = state["isolated"]
        from app.database.index import IndexRepository
        from app.domain.models import Product, ProductColor
        from quality.understanding_corpus import CONCEPTS

        engine, sessions = create_database(child["database_url"])
        extra = []
        try:
            for n, (category, noun) in enumerate(CONCEPTS):
                for color, word, stock in [
                    ("RED", "красный", 300),
                    ("BLUE", "синий", 500),
                    ("BLACK", "чёрный", 0),
                ]:
                    extra.append(
                        Product(
                            id=f"vto:q2-{category}-{color}",
                            supplier="vto",
                            name=noun + " контрольный выпуск",
                            source_url=f"https://vto.kz/product/isolated-q2-{category}-{color}/",
                            brand="Quality Fixture",
                            original_price=1000,
                            original_currency="KZT",
                            stock_quantity=stock,
                            colors=[ProductColor(original_color=word)],
                            metadata={
                                "quality_class": category,
                                "sku": f"Q2-{n:03}-{color}",
                                "subtype": "notepad" if category == "notebook" else "",
                            },
                        )
                    )
            for key, title in [
                ("drawing", "Кружка с рисунком бейсболки"),
                ("set", "Подарочный набор ручка и бейсболка"),
                ("lid", "Bottle cap"),
            ]:
                extra.append(
                    Product(
                        id="vto:q2-negative-" + key,
                        supplier="vto",
                        name=title,
                        source_url="https://vto.kz/product/isolated-negative-" + key + "/",
                        original_price=1000,
                        original_currency="KZT",
                        stock_quantity=500,
                        colors=[ProductColor(original_color="красный")],
                    )
                )
            repo = IndexRepository(sessions, Settings(database_url=child["database_url"]))
            await repo.ingest(fixture_products())
            await repo.ingest(extra)
        finally:
            await engine.dispose()
        print("Added", len(extra), "explicit isolated products; main catalog unchanged")
    elif stage in ("labelled", "holdout"):
        from app.main import create_app
        from quality.understanding_corpus import VERSION, holdout, labelled

        state = json.loads(LEDGER.read_text("utf-8"))
        rows = labelled() if stage == "labelled" else holdout()
        gold = ROOT / "apps/api/tests/quality" / ("understanding-" + stage + ".json")
        content = {"version": VERSION, "cases": rows}
        if gold.exists():
            assert json.loads(gold.read_text("utf-8")) == content, (
                "Gold must not be retuned to production output"
            )
        else:
            save(gold, content)
        app = create_app(
            Settings(
                database_url=state["isolated"]["database_url"],
                llm_backend="basic",
                background_research_enabled=False,
                presentation_worker_enabled=False,
            ),
            [],
        )
        results = []
        try:
            for n, case in enumerate(rows):
                started = time.perf_counter()
                try:
                    intent = await app.state.research.grid_plan(case["query"])
                    actual = {
                        "categories": sorted(intent.categories),
                        "models": intent.models + [m for b in intent.branches for m in b.models],
                        "colors": sorted(intent.colors),
                        "quantity": intent.quantity,
                        "branches": [
                            {"category": b.category, "colors": b.colors, "quantity": b.quantity}
                            for b in intent.branches
                        ],
                    }
                    checks = {
                        "categories": set(intent.categories) == set(case["categories"]),
                        "models": not actual["models"],
                        "colors": all(sorted(b.colors) == case["colors"] for b in intent.branches),
                        "quantity": all(b.quantity == case["quantity"] for b in intent.branches),
                    }
                    results.append(
                        {
                            "id": case["id"],
                            "class": case["class"],
                            "query": case["query"],
                            "checks": checks,
                            "pass": all(checks.values()),
                            "actual": actual,
                            "seconds": round(time.perf_counter() - started, 4),
                        }
                    )
                except (ValueError, TypeError, KeyError) as exc:
                    results.append(
                        {
                            "id": case["id"],
                            "class": case["class"],
                            "query": case["query"],
                            "pass": False,
                            "error": str(exc),
                        }
                    )
                if n % 25 == 0:
                    save(PUBLIC / (stage + ".json"), {"results": results})
            save(
                PUBLIC / (stage + ".json"),
                {
                    "version": VERSION,
                    "total": len(results),
                    "passed": sum(r["pass"] for r in results),
                    "results": results,
                },
            )
            print(stage, sum(r["pass"] for r in results), "/", len(results))
        finally:
            await app.state.engine.dispose()
    elif stage == "properties":
        if os.environ.get("UNDERSTANDING_PROPERTY_WORKER") != "1":
            process = await asyncio.to_thread(
                subprocess.Popen,
                [
                    sys.executable,
                    "-X",
                    "utf8",
                    str(ROOT / "scripts/check_search_safety.py"),
                    "regression",
                    "--stage",
                    "properties",
                ],
                cwd=ROOT,
                env=dict(os.environ, UNDERSTANDING_PROPERTY_WORKER="1"),
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            try:
                code = await asyncio.wait_for(asyncio.to_thread(process.wait), 120)
                assert code == 0, "Offline property worker failed"
            except BaseException:
                import psutil

                parent = psutil.Process(process.pid)
                for p in list(reversed(parent.children(recursive=True))) + [parent]:
                    if p.is_running():
                        p.kill()
                raise
            return
        from app.api.request_safety import validate_query
        from app.domain.query_constraints import parse_constraints
        from app.domain.query_normalization import normalize_query
        from app.domain.taxonomy import UniversalCategoryResolver
        from quality.understanding_corpus import CONCEPTS

        seen = set()
        failures = []
        started = time.perf_counter()
        separators = [". ", ", ", "; ", "\n", "\t", ".."]
        for n in range(5000):
            category, noun = CONCEPTS[n % len(CONCEPTS)]
            amount = n + 1
            original = separators[(n // len(CONCEPTS)) % len(separators)].join(
                (noun, "цвет", "красный", "тираж", str(amount))
            )
            seen.add(original)
            normal = normalize_query(original)
            attrs, _ = parse_constraints(normal.text)
            checks = {
                "concept": category in UniversalCategoryResolver().resolve(normal.text),
                "color": attrs.colors == ["RED"],
                "quantity": attrs.quantity_min == amount,
                "idempotence": normalize_query(normal.text).text == normal.text,
                "original": normal.original == original,
                "offsets": all(
                    0 <= t["original_start"] < t["original_end"] <= len(original)
                    for t in normal.trace()["tokens"]
                ),
            }
            if not all(checks.values()):
                failures.append({"query": original, "checks": checks, "normalized": normal.text})
        rejected = []
        for value in ["!" * 1000000, "ручки " + "." * 100000, "x" * 257, "ручки " * 300, "ручки\u0000"]:
            try:
                validate_query(value)
            except ValueError:
                rejected.append(True)
        result = {
            "generated": 5000,
            "unique": len(seen),
            "passed": 5000 - len(failures),
            "failures": failures,
            "hostile_inputs_rejected": len(rejected),
            "watchdog_seconds": 120,
            "seconds": round(time.perf_counter() - started, 3),
            "scope": "normalization, attribute and category invariants; no network/SQL or claim of 5000 independent language tasks",
        }
        save(PUBLIC / "properties.json", result)
        print("Properties", result["passed"], "/5000; hostile", len(rejected))
        assert not failures and len(rejected) == 5
    elif stage == "sequences":
        from check_search_safety import serve

        from quality.acceptance import BudgetClient, all_ids, change, submit
        from quality.understanding_corpus import CONCEPTS

        state = json.loads(LEDGER.read_text("utf-8"))
        child = state["isolated"]
        import psutil

        if not child.get("api_pid") or not psutil.pid_exists(child["api_pid"]):
            await serve(child)
        state["isolated"] = child
        save(LEDGER, state)
        state["sequence_attempt"] = state.get("sequence_attempt", 0) + 1
        save(LEDGER, state)
        BudgetClient.counter = 0
        BudgetClient.deadline = time.monotonic() + 1200
        clients = [BudgetClient(base_url="http://127.0.0.1:18000", timeout=45) for _ in range(2)]
        projects = []
        selected = [set(), set()]
        documents = [None, None]
        results = []

        def expected(categories, color="RED"):
            categories = set(categories)
            if "cap" in categories:
                categories.add("baseballcap")
            return {f"vto:q2-{c}-{color}" for c in categories}

        try:
            for n, c in enumerate(clients):
                await call(
                    c,
                    "POST",
                    "/api/auth/register",
                    json={
                        "name": "sequence-" + state["run"] + str(state["sequence_attempt"]) + str(n),
                        "password": secrets.token_urlsafe(24),
                    },
                )
                cookies = dict(c.cookies)
                c.cookies.clear()
                c.cookies.update(cookies)
                chat = await call(c, "POST", "/api/chats", json={"project_name": "Bounded sequence fixture"})
                projects.append(
                    next(p for p in await call(c, "GET", "/api/projects") if p["chat_id"] == chat["id"])
                )
            for n in range(100):
                who = n % 2
                c = clients[who]
                project = projects[who]
                cat, noun = CONCEPTS[n % len(CONCEPTS)]
                second, other = CONCEPTS[(n + 5) % len(CONCEPTS)]
                query = f"красные {noun} и {other}. Бренд Quality Fixture. Тираж 200"
                r = await submit(c, project["id"], query)
                gold = expected([cat, second])
                assert await all_ids(c, r) == gold, (n, "membership", query)
                branch = next(b for b in r["intent"]["branches"] if b["category"] == cat)
                hidden = await change(c, r, branch_actions=[{"action": "hide", "branch_id": branch["id"]}])
                assert await all_ids(c, hidden) == expected([second]), (n, "hide")
                shown = await change(
                    c, hidden, branch_actions=[{"action": "show", "branch_id": branch["id"]}]
                )
                assert await all_ids(c, shown) == gold
                removed = await change(
                    c, shown, branch_actions=[{"action": "remove", "branch_id": branch["id"]}]
                )
                assert await all_ids(c, removed) == expected([second])
                undone = await change(c, removed, restore="undo")
                assert await all_ids(c, undone) == gold
                reloaded = await call(c, "GET", "/api/researches/chat/" + project["chat_id"])
                assert await all_ids(c, reloaded) == gold
                offer = sorted(gold)[0]
                collection = await call(
                    c,
                    "POST",
                    f"/api/projects/{project['id']}/collection/items",
                    json={"offer_id": offer, "source_research_id": r["id"]},
                )
                selected[who].add(offer)
                refined = await submit(c, project["id"], "оставь только красные")
                assert await all_ids(c, refined) == gold
                queries = await call(c, "GET", f"/api/projects/{project['id']}/queries")
                active = next(q for q in queries if q["is_active"])
                await call(
                    c, "POST", f"/api/projects/{project['id']}/queries/{active['id']}/activate", json={}
                )
                document = await call(
                    c,
                    "POST",
                    f"/api/projects/{project['id']}/offers",
                    json={"collection_version": collection["version"]},
                )
                if documents[who]:
                    assert document["id"] == documents[who]
                documents[who] = document["id"]
                assert (
                    set((await call(c, "GET", f"/api/projects/{project['id']}/collection"))["selected_ids"])
                    == selected[who]
                )
                assert (await clients[1 - who].get(f"/api/offers/{document['id']}")).status_code == 404
                if n % 10 == 0:
                    bad = await c.post(f"/api/projects/{project['id']}/requests", json={"query": "!" * 5000})
                    assert bad.status_code in (413, 422)
                    canary = await submit(
                        clients[1 - who],
                        projects[1 - who]["id"],
                        "красные ручки. Бренд Quality Fixture. Тираж 200",
                    )
                    assert await all_ids(clients[1 - who], canary) == expected(["pen"])
                results.append({"sequence": n, "actions": 10, "pass": True, "categories": [cat, second]})
                if n % 10 == 0:
                    save(PUBLIC / "sequences.json", {"sequences": len(results), "results": results})
                    print("Sequence", n, flush=True)
            page = await submit(clients[0], projects[0]["id"], "ручки бренд Page Brand")
            from quality.corpus import products

            assert await all_ids(clients[0], page) == {
                p["id"] for p in products() if p["brand"] == "Page Brand"
            }
            save(
                PUBLIC / "sequences.json",
                {
                    "sequences": len(results),
                    "http_requests": BudgetClient.counter,
                    "filled_postgresql": True,
                    "membership_oracle": "Explicit q2 concept/color/quantity IDs; broad cap includes confirmed baseballcap subtype; all pages compared",
                    "pagination_pool": 130,
                    "results": results,
                },
            )
            print("Completed", len(results), "sequences; HTTP", BudgetClient.counter)
        finally:
            for c in clients:
                await c.aclose()
    elif stage.startswith("admission-"):
        state = json.loads(LEDGER.read_text("utf-8"))
        async with httpx.AsyncClient(
            base_url="http://127.0.0.1:8000", cookies=state["users"][1]["cookies"], timeout=90
        ) as c:
            chat = await call(
                c, "POST", "/api/chats", json={"project_name": "Normal hydration and selection"}
            )
            project = next(p for p in await call(c, "GET", "/api/projects") if p["chat_id"] == chat["id"])
            state["projects"].append(project["id"])
            save(LEDGER, state)
            research = (
                await call(c, "POST", f"/api/projects/{project['id']}/requests", json={"query": "ручки"})
            )["research"]
            p = project["id"]
            routes = [
                f"/api/researches/{research['id']}/products",
                f"/api/projects/{p}/queries",
                f"/api/projects/{p}/collection",
                f"/api/projects/{p}/offer",
                f"/api/projects/{p}/offers",
            ]
            evidence = []
            for attempt in range(3):

                async def request(method, route, **kw):
                    response = await c.request(method, route, **kw)
                    return {
                        "method": method,
                        "route": route.replace(p, "{owned_project}").replace(research["id"], "{research}"),
                        "status": response.status_code,
                        "request_id": response.headers.get("x-request-id"),
                        "problem": response.json() if response.status_code >= 400 else None,
                    }

                evidence.extend(
                    await asyncio.gather(
                        *[request("GET", route) for route in routes],
                        request(
                            "POST",
                            f"/api/projects/{p}/collection/items",
                            json={
                                "offer_id": research["products"][0]["id"],
                                "source_research_id": research["id"],
                            },
                        ),
                    )
                )
            save(PUBLIC / (stage + ".json"), evidence)
            print(stage, "responses", len(evidence), "errors", sum(r["status"] >= 400 for r in evidence))
    else:
        raise ValueError(stage)
