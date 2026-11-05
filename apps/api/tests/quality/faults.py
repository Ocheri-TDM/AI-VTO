"""Fault injection is restricted to the ledger-owned PostgreSQL/API instance."""

import asyncio
import hashlib
import io
import json
import sys
from pathlib import Path
from uuid import uuid4

import httpx
from PIL import Image, ImageDraw
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.config import Settings
from app.main import create_app
from quality.acceptance import all_ids, register, submit
from quality.corpus import matching, products


async def run(state):
    assert ":55433/" in state["database_url"] and "search-safety-" in state["directory"]
    settings = Settings(
        _env_file=None,
        database_url=state["database_url"],
        llm_backend="basic",
        auth_required=True,
        background_research_enabled=False,
        presentation_worker_enabled=False,
        search_deadline_seconds=2,
        search_requests_per_minute=10000,
        presentation_storage=Path(state["directory"]) / "presentations",
    )
    app = create_app(settings, [])
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    clients = [httpx.AsyncClient(transport=transport, base_url="http://quality.test") for _ in range(2)]
    projects = [await register(c, "fault-" + uuid4().hex[:12]) for c in clients]
    c = clients[0]
    endpoint = f"/api/projects/{projects[0]['id']}/requests"
    checks = []
    collections = []
    doc = revision = None
    preview_hash = None
    try:
        for who, client in enumerate(clients):
            base = f"/api/projects/{projects[who]['id']}/collection"
            response = await client.post(base + "/items", json={"offer_id": products()[who]["id"]})
            response.raise_for_status()
            collections.append((await client.get(base)).json())
        response = await c.post(
            f"/api/projects/{projects[0]['id']}/presentations",
            json={"collection_version": collections[0]["version"]},
        )
        response.raise_for_status()
        doc = response.json()
        # Explicit synthetic test asset, uploaded through the existing API.
        # It is never inserted into the main VTO catalog or labelled a real photo.
        picture = Image.new("RGBA", (320, 320), (0, 0, 0, 0))
        ImageDraw.Draw(picture).rectangle((145, 35, 175, 285), fill="#163E52")
        image = io.BytesIO()
        picture.save(image, format="PNG")
        response = await c.post(
            f"/api/presentations/{doc['id']}/items/{products()[0]['id']}/image",
            params={"version": doc["version"]},
            content=image.getvalue(),
            headers={"Content-Type": "image/png"},
        )
        response.raise_for_status()
        doc = response.json()
        response = await c.post(f"/api/presentations/{doc['id']}/generate", json={"version": doc["version"]})
        response.raise_for_status()
        row = await app.state.presentations.claim_next()
        assert row is not None
        await app.state.presentations.execute(row)
        value = (await c.get(f"/api/presentations/{doc['id']}")).json()
        revision = value["revisions"][0]
        assert revision["status"] in ("NEEDS_REVIEW", "READY"), revision
        preview = await c.get(revision["result"]["pages"][0]["preview"])
        preview.raise_for_status()
        preview_hash = hashlib.sha256(preview.content).hexdigest()
        snapshot = json.dumps(doc["draft"]["items"], sort_keys=True)

        async def canary(label):
            healthy = await c.get("/api/health")
            assert healthy.status_code == 200
            for who, client in enumerate(clients):
                query = "Ручка шариковая Tippo" if who == 0 else "бренд Molti"
                r = await submit(client, projects[who]["id"], query)
                spec = (
                    {"branches": [{"in": {"model": ["Tippo"]}}]}
                    if who == 0
                    else {"branches": [{"in": {"brand": ["Molti"]}}]}
                )
                assert await all_ids(client, r) == matching(products(), spec), (
                    label,
                    "canary membership",
                    who,
                )
                col = (await client.get(f"/api/projects/{projects[who]['id']}/collection")).json()
                assert (
                    col["selected_ids"] == collections[who]["selected_ids"]
                    and col["version"] == collections[who]["version"]
                ), (label, "collection", who)
                foreign = await client.get(f"/api/projects/{projects[1 - who]['id']}/collection")
                assert foreign.status_code == 404, (label, "ownership")
            value = (await c.get(f"/api/presentations/{doc['id']}")).json()
            assert json.dumps(value["draft"]["items"], sort_keys=True) == snapshot
            page = await c.get(revision["result"]["pages"][0]["preview"])
            assert page.status_code == 200 and hashlib.sha256(page.content).hexdigest() == preview_hash
            async with app.state.sessions() as db:
                count = await db.scalar(
                    text(
                        "SELECT count(*) FROM pg_stat_activity WHERE pid<>pg_backend_pid() AND state='idle in transaction'"
                    )
                )
                assert count == 0, (label, "idle transactions", count)
                count = await db.scalar(
                    text("SELECT count(*) FROM pg_locks WHERE locktype='advisory' AND granted")
                )
                assert count == 0, (label, "advisory locks", count)
                running = await db.scalar(
                    text("SELECT count(*) FROM research_jobs WHERE status IN ('QUEUED','RUNNING')")
                )
                assert running == 0
            assert app.state.engine.pool.checkedout() == 0
            assert app.state.admission.active == app.state.admission.queued == 0
            checks.append(
                {
                    "fault": label,
                    "recovery": "PASS",
                    "workspace_canaries": 2,
                    "collections_versions": "unchanged",
                    "snapshot_preview": "unchanged",
                    "idle_transactions": count,
                    "pool_checked_out": 0,
                }
            )

        await canary("initial")
        await submit(c, projects[0]["id"], "Tippo")
        exact_sku = next(p for p in products() if p.get("sku"))
        result = await submit(c, projects[0]["id"], exact_sku["sku"])
        assert await all_ids(c, result) == {exact_sku["id"]}
        assert result["intent"]["raw_query"] == exact_sku["sku"]
        await canary("standalone_sku_after_model")
        settings.search_requests_per_minute = 10
        app.state.admission.users.clear()
        rate_path = f"/api/projects/{projects[0]['id']}/queries"
        for n in range(10):
            response = await c.get(rate_path, headers={"X-Forwarded-For": f"192.0.2.{n + 1}"})
            assert response.status_code == 200
        response = await c.get(rate_path, headers={"X-Forwarded-For": "192.0.2.250"})
        assert response.status_code == 429
        response = await clients[1].get(f"/api/projects/{projects[1]['id']}/queries")
        assert response.status_code == 200, "Shared-IP user was incorrectly rate limited"
        settings.search_requests_per_minute = 10000
        app.state.admission.users.clear()
        await canary("per_user_rate_limit_forwarded_header_ignored")
        plan = app.state.research.grid_plan

        async def slow(*args, **kwargs):
            await asyncio.sleep(10)
            return await plan(*args, **kwargs)

        app.state.research.grid_plan = slow
        before = (await c.get(f"/api/projects/{projects[0]['id']}/queries")).json()
        response = await c.post(endpoint, json={"query": "бутылки"})
        app.state.research.grid_plan = plan
        assert response.status_code == 504 and response.json()["code"] == "REQUEST_DEADLINE", response.text
        after = (await c.get(f"/api/projects/{projects[0]['id']}/queries")).json()
        assert next(q["id"] for q in before if q["is_active"]) == next(
            q["id"] for q in after if q["is_active"]
        )
        await canary("planning_deadline")

        response_builder = app.state.research.grid_response

        async def serialization_fault(*args, **kwargs):
            raise RuntimeError("Synthetic serialization failure")

        app.state.research.grid_response = serialization_fault
        response = await c.post(endpoint, json={"query": "бутылки"})
        app.state.research.grid_response = response_builder
        assert response.status_code == 500
        await canary("serialization_failure")

        save = app.state.research.repository.save

        async def failed_commit(r, **kwargs):
            await save(r, **kwargs)
            raise SQLAlchemyError("Synthetic commit failure")

        app.state.research.repository.save = failed_commit
        response = await c.post(endpoint, json={"query": "бутылки"})
        app.state.research.repository.save = save
        assert response.status_code == 503
        await canary("publication_rollback")

        async def statement_timeout(*args, **kwargs):
            async with app.state.sessions.begin() as db:
                await db.execute(text("SET LOCAL statement_timeout = '40ms'"))
                await db.execute(text("SELECT pg_sleep(1)"))
            return await plan(*args, **kwargs)

        app.state.research.grid_plan = statement_timeout
        response = await c.post(endpoint, json={"query": "бутылки"})
        app.state.research.grid_plan = plan
        assert response.status_code == 503
        await canary("sql_statement_timeout")

        held = []
        try:
            for _ in range(15):
                held.append(await app.state.engine.connect())
            response = await c.post(endpoint, json={"query": "бутылки"})
            assert response.status_code in (503, 504)
        finally:
            for connection in held:
                await connection.close()
        await canary("pool_exhaustion")

        from app.ai.local import ModelUnavailable, OllamaProvider
        from app.domain.research import ResearchDecision

        model = OllamaProvider(
            "http://127.0.0.1:1", "synthetic-model", timeout=0.15, retries=0, queue_limit=0
        )

        async def inference_slow(*args):
            await asyncio.sleep(10)

        model.request = inference_slow
        try:
            await model.complete([], ResearchDecision)
            raise AssertionError("Missing LLM deadline")
        except ModelUnavailable as exc:
            assert str(exc) == "LLM_DEADLINE"
        assert model._waiting == 0 and not model._slots.locked()
        await canary("llm_inference_deadline")

        async def invalid(*args):
            return '{"action":"EXECUTE_SQL","sql":"DROP DATABASE main"}'

        model.request = invalid
        try:
            await model.complete([], ResearchDecision)
            raise AssertionError("Invalid LLM plan accepted")
        except ModelUnavailable:
            pass
        assert model._waiting == 0 and not model._slots.locked()
        await canary("llm_invalid_plan")
        await model._slots.acquire()
        try:
            try:
                await model.complete([], ResearchDecision)
                raise AssertionError("Full LLM queue admitted")
            except ModelUnavailable as exc:
                assert str(exc) == "LLM_QUEUE_FULL"
        finally:
            model._slots.release()
        await canary("llm_queue_full")

        # The lock holder exists only in the owned test cluster.
        async with app.state.sessions.begin() as blocker:
            await blocker.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": projects[0]["id"]}
            )
            response = await c.post(endpoint, json={"query": "бутылки"})
            assert response.status_code in (503, 504)
        await canary("project_lock_timeout")

        app.state.research.grid_plan = slow
        task = asyncio.create_task(c.post(endpoint, json={"query": "бутылки"}))
        await asyncio.sleep(0.15)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        app.state.research.grid_plan = plan
        await canary("caller_cancellation")

        # An older slow request cannot win after a newer search publishes.
        entered, release = asyncio.Event(), asyncio.Event()

        async def ordered(query, **kwargs):
            if query == "бутылки":
                entered.set()
                await release.wait()
            return await plan(query, **kwargs)

        app.state.research.grid_plan = ordered
        first = asyncio.create_task(c.post(endpoint, json={"query": "бутылки"}))
        await entered.wait()
        second = await c.post(endpoint, json={"query": "рюкзаки"})
        assert second.status_code == 202
        release.set()
        old = await first
        app.state.research.grid_plan = plan
        assert old.status_code == 409
        active = (await c.get(f"/api/projects/{projects[0]['id']}/queries")).json()
        assert (
            next(q["research_session_id"] for q in active if q["is_active"])
            == second.json()["research"]["id"]
        )
        await canary("concurrent_publication")

        # History activation uses the same publication epoch as a new search.
        history = (await c.get(f"/api/projects/{projects[0]['id']}/queries")).json()
        older, newer = history[-1], history[-2]
        prepare = app.state.research.grid_prepare_response
        entered, release = asyncio.Event(), asyncio.Event()

        async def delayed_history(research):
            if research.id == older["research_session_id"]:
                entered.set()
                await release.wait()
            return await prepare(research)

        app.state.research.grid_prepare_response = delayed_history
        first = asyncio.create_task(
            c.post(f"/api/projects/{projects[0]['id']}/queries/{older['id']}/activate")
        )
        await asyncio.wait_for(entered.wait(), 1)
        second = await c.post(f"/api/projects/{projects[0]['id']}/queries/{newer['id']}/activate")
        release.set()
        old = await first
        app.state.research.grid_prepare_response = prepare
        assert second.status_code == 200 and old.status_code == 409
        await canary("history_activation_race")

        active = await submit(c, projects[0]["id"], "Tippo")
        stream = f"/api/researches/{active['id']}/events"
        response = await clients[1].get(stream)
        assert response.status_code == 404
        async with app.state.sessions() as db:
            actor_id = await db.scalar(
                text("SELECT user_id FROM projects WHERE id=:id"), {"id": projects[0]["id"]}
            )
        async with app.state.stream_admission.enter(actor_id):
            async with app.state.stream_admission.enter(actor_id):
                response = await c.get(stream)
                assert response.status_code == 200 and "event: request.failed" in response.text
        await canary("sse_capacity_failure")
        response = await c.get(stream)
        assert response.status_code == 200 and "event: research.completed" in response.text
        assert app.state.stream_admission.active == app.state.stream_admission.queued == 0
        await canary("sse_reconnect_completed")

        # Crash only the task-owned child. The original PDF/preview must remain
        # readable, with a distinct failed revision and no automatic overwrite.
        value = (await c.get(f"/api/presentations/{doc['id']}")).json()
        response = await c.post(
            f"/api/presentations/{doc['id']}/generate",
            json={"version": value["version"], "request_id": str(uuid4())},
        )
        response.raise_for_status()
        row = await app.state.presentations.claim_next()
        assert row is not None and row.id != revision["id"]
        spawn = asyncio.create_subprocess_exec

        async def crash_worker(*args, **kwargs):
            assert "app.application.presentation_worker" in args
            return await spawn(sys.executable, "-c", "raise SystemExit(7)", **kwargs)

        asyncio.create_subprocess_exec = crash_worker
        try:
            await app.state.presentations.execute(row)
        finally:
            asyncio.create_subprocess_exec = spawn
        value = (await c.get(f"/api/presentations/{doc['id']}")).json()
        failed = next(r for r in value["revisions"] if r["id"] == row.id)
        assert failed["status"] == "FAILED"
        await canary("pdf_worker_exit_7")

        import app.application.product_grid as grid_module

        evidence = grid_module.model_evidence
        grid_module.model_evidence = lambda *args, **kwargs: {"accepted": True}
        try:
            value = await submit(c, projects[0]["id"], "Tippo")
            actual = await all_ids(c, value)
            gold = matching(products(), {"branches": [{"in": {"model": ["Tippo"]}}]})
            detected = False
            try:
                assert actual == gold
            except AssertionError:
                detected = True
            assert detected, "Dropped model mutation escaped the membership oracle"
            checks.append(
                {
                    "fault": "dropped_model_mutation",
                    "oracle_detected": True,
                    "actual_count": len(actual),
                    "expected_count": len(gold),
                }
            )
        finally:
            grid_module.model_evidence = evidence
        await canary("model_mutation_restored")

        async def drop_exclusion(*args, **kwargs):
            value = await plan(*args, **kwargs)
            value.excluded_materials = []
            for branch in value.branches:
                branch.constraints.excluded_materials = []
            return value

        app.state.research.grid_plan = drop_exclusion
        try:
            value = await submit(c, projects[0]["id"], "ручки без пластика")
            actual = await all_ids(c, value)
            gold = matching(
                products(), {"branches": [{"concept": "pen", "not_in": {"material": ["пластик"]}}]}
            )
            detected = False
            try:
                assert actual == gold
            except AssertionError:
                detected = True
            assert detected, "Dropped exclusion mutation escaped the membership oracle"
            checks.append(
                {
                    "fault": "dropped_exclusion_mutation",
                    "oracle_detected": True,
                    "actual_count": len(actual),
                    "expected_count": len(gold),
                }
            )
        finally:
            app.state.research.grid_plan = plan
        await canary("exclusion_mutation_restored")

        # Real mutation control: bypass ownership in the service, observe an
        # actual 200, and show that the same foreign-resource assertion fails.
        read = app.state.collections.read
        async with app.state.sessions() as db:
            foreign_owner = await db.scalar(
                text("SELECT user_id FROM projects WHERE id=:id"), {"id": projects[1]["id"]}
            )

        async def bypass(project, owner, **kwargs):
            return await read(project, foreign_owner, **kwargs)

        app.state.collections.read = bypass
        response = await c.get(f"/api/projects/{projects[1]['id']}/collection")
        app.state.collections.read = read
        assert response.status_code == 200, "Mutation control did not exercise an ownership bypass"
        checks.append({"fault": "ownership_mutation_control", "oracle_detected": response.status_code != 404})
        await canary("ownership_mutation_restored")

        return {
            "status": "PASS",
            "checks": checks,
            "preview_sha256": preview_hash,
            "document_id": doc["id"],
            "revision_id": revision["id"],
            "transport": "ASGI HTTP + isolated real PostgreSQL",
        }
    finally:
        for client in clients:
            await client.aclose()
        await app.state.research.close()
        await app.state.engine.dispose()
