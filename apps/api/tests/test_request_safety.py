import asyncio

import httpx
import pytest
from sqlalchemy import select

from app.database.models import Base, Project, ResearchRecord
from app.domain.query_constraints import parse_constraints
from app.domain.research import ResearchFilters
from app.main import create_app


@pytest.mark.parametrize(
    "query",
    [
        "",
        "   ",
        "🙂",
        "ручки\x00",
        "ручки\u202e",
        "ручки от -3 шт",
        "ручки цена от 5000 до 1000 тенге",
        "только красные ручки, исключи красные",
    ],
)
async def test_bad_request_keeps_previous_workspace(settings, query):
    settings.auth_required = True
    app = create_app(settings, [])
    async with app.state.engine.begin() as db:
        await db.run_sync(Base.metadata.create_all)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        await c.post("/api/auth/register", json={"name": "input-owner", "password": "input-password-123"})
        await c.post("/api/chats", json={"project_name": "Input safety"})
        project = (await c.get("/api/projects")).json()[0]["id"]
        route = f"/api/projects/{project}/requests"
        valid = await c.post(route, json={"query": "ручки"})
        assert valid.status_code == 202
        initial = valid.json()["query"]["id"]
        response = await c.post(route, json={"query": query})
        assert response.status_code == 422, response.text
        async with app.state.sessions() as db:
            assert (await db.get(Project, project)).active_query_id == initial
    await app.state.engine.dispose()


@pytest.mark.parametrize("value", [True, False, -1, 0, 10_000_001, "300"])
def test_quantity_filter_rejects_coercion_and_bounds(value):
    with pytest.raises(ValueError):
        ResearchFilters(minimum_quantity=value)


def test_exclusive_bounds_technical_numbers_and_model_number_are_separate():
    constraints, head = parse_constraints("Бутылки модель X2, 500 мл, больше 80 штук, дешевле 2500 тенге")
    assert constraints.quantity_min == 80 and constraints.quantity_min_exclusive
    assert constraints.price_max == 2500 and constraints.price_max_exclusive
    assert constraints.capacity_ml == 500 and "X2" in head
    assert "80" not in head and "500" not in head


async def test_failed_serialization_does_not_publish_and_retry_recovers(settings, monkeypatch):
    settings.auth_required = True
    app = create_app(settings, [])
    async with app.state.engine.begin() as db:
        await db.run_sync(Base.metadata.create_all)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    ) as c:
        await c.post("/api/auth/register", json={"name": "atomic-owner", "password": "input-password-123"})
        await c.post("/api/chats", json={"project_name": "Atomic safety"})
        project = (await c.get("/api/projects")).json()[0]["id"]
        endpoint = f"/api/projects/{project}/requests"
        initial = (await c.post(endpoint, json={"query": "ручки"})).json()
        original = app.state.research.grid_response

        async def fail(*args, **kwargs):
            raise RuntimeError("Synthetic render fault")

        monkeypatch.setattr(app.state.research, "grid_response", fail)
        assert (await c.post(endpoint, json={"query": "бутылки"})).status_code == 500
        async with app.state.sessions() as db:
            assert (await db.get(Project, project)).active_query_id == initial["query"]["id"]
            assert len((await db.scalars(select(ResearchRecord))).all()) == 1
        monkeypatch.setattr(app.state.research, "grid_response", original)
        assert (await c.post(endpoint, json={"query": "бутылки"})).status_code == 202
    await app.state.engine.dispose()


async def test_llm_total_deadline_releases_slot():
    from app.ai.local import ModelUnavailable, OllamaProvider
    from app.domain.research import ResearchDecision

    provider = OllamaProvider("http://127.0.0.1:1", "synthetic", timeout=0.03, retries=0)

    async def hang(*args):
        await asyncio.sleep(10)

    provider.request = hang
    with pytest.raises(ModelUnavailable, match="LLM_DEADLINE"):
        await provider.complete([], ResearchDecision)
    assert not provider._slots.locked() and provider._waiting == 0


async def test_frontend_empty_get_json_header_is_not_a_json_body(settings):
    app = create_app(settings, [])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/health", headers={"Content-Type": "application/json"})
        assert response.status_code == 200 and response.json()["database"] == "ok"
    await app.state.engine.dispose()


async def test_exact_sku_replaces_previous_model_workspace(settings):
    from app.domain.models import Product

    app = create_app(settings, [])
    async with app.state.engine.begin() as db:
        await db.run_sync(Base.metadata.create_all)
    await app.state.index.ingest(
        [
            Product(
                id="vto:old-model",
                supplier="vto",
                name="Ручка Tippo",
                source_url="https://vto.kz/product/pen/",
            ),
            Product(
                id="vto:exact-sku",
                supplier="vto",
                name="Рюкзак Melango",
                metadata={"sku": "GF-12449.40"},
                source_url="https://vto.kz/product/bag/",
            ),
        ]
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        await c.post("/api/auth/register", json={"name": "sku-owner", "password": "sku-test-password-123"})
        await c.post("/api/chats", json={"project_name": "SKU isolation"})
        project = (await c.get("/api/projects")).json()[0]["id"]
        endpoint = f"/api/projects/{project}/requests"
        first = (await c.post(endpoint, json={"query": "Tippo"})).json()
        second = (await c.post(endpoint, json={"query": "GF-12449.40"})).json()
        assert second["route"]["action"] == "NEW_SEARCH"
        assert second["research"]["id"] != first["research"]["id"]
        assert [p["id"] for p in second["research"]["products"]] == ["vto:exact-sku"]
    await app.state.engine.dispose()


async def test_identity_words_are_not_attribute_filters_and_soft_brand_is_not_hard(repository, settings):
    from app.application.index_research import IndexedResearchService
    from app.database.conversations import ConversationRepository
    from app.database.index import IndexRepository
    from app.database.research import ResearchRepository
    from app.domain.models import Product, ProductColor

    index = IndexRepository(repository.sessions, settings)
    await index.ingest(
        [
            Product(
                id="vto:blue-brand",
                supplier="vto",
                name="Ручка шариковая 42",
                brand="Blue Lake",
                colors=[ProductColor(original_color="чёрный")],
                source_url="https://vto.kz/product/blue-brand/",
            ),
            Product(
                id="vto:blue-model",
                supplier="vto",
                name="Ручка шариковая Blue",
                brand="Other",
                colors=[ProductColor(original_color="красный")],
                source_url="https://vto.kz/product/blue-model/",
            ),
        ]
    )
    grid = IndexedResearchService(ResearchRepository(repository.sessions), [], settings, None, index)
    chat = await ConversationRepository(repository.sessions).create("Identity roles")
    for query, expected in [
        ("бренд Blue Lake", ["vto:blue-brand"]),
        ("ручки модель Blue", ["vto:blue-model"]),
        ("ручки модель 42", ["vto:blue-brand"]),
    ]:
        r = await grid.start(chat, query)
        assert r.pool_offer_ids == expected
        assert all(not b.colors for b in r.intent.branches)
    r = await grid.start(chat, "ручки желательно бренд Other")
    assert set(r.pool_offer_ids) == {"vto:blue-brand", "vto:blue-model"}


async def test_zero_waiting_queue_admits_free_capacity_and_releases_on_cancel(settings):
    from app.api.request_safety import Admission, RequestProblem

    admission = Admission(settings.model_copy(update={"search_concurrency": 1, "search_queue_limit": 0}))
    async with admission.enter("first"):
        with pytest.raises(RequestProblem, match="Поиск занят"):
            async with admission.enter("second"):
                pass
    async with admission.enter("second"):
        assert admission.active == 1
    assert admission.active == admission.queued == 0


async def test_parallel_workspace_reads_have_bounded_capacity(settings):
    from contextlib import AsyncExitStack

    from app.api.request_safety import Admission, RequestProblem

    admission = Admission(settings)
    async with AsyncExitStack() as stack:
        for _ in range(4):
            await stack.enter_async_context(admission.enter("same-user", per_user_limit=4))
        with pytest.raises(RequestProblem):
            async with admission.enter("same-user", per_user_limit=4):
                pass
        async with admission.enter("other-user"):
            assert admission.active == 5
    assert admission.active == admission.queued == 0


async def test_in_stock_is_not_a_fabricated_quantity_and_preserves_last_known(repository, settings):
    from datetime import UTC, datetime

    from app.application.index_research import IndexedResearchService
    from app.database.conversations import ConversationRepository
    from app.database.index import IndexRepository
    from app.database.research import ResearchRepository
    from app.domain.models import Product

    index = IndexRepository(repository.sessions, settings)
    await index.ingest(
        [
            Product(
                id="vto:stock-" + suffix,
                supplier="vto",
                name="Ручка шариковая",
                stock_quantity=stock,
                fetched_at=datetime(2020, 1, 1, tzinfo=UTC),
                source_url="https://vto.kz/product/stock-" + suffix + "/",
            )
            for suffix, stock in [("old-positive", 500), ("zero", 0), ("unknown", None)]
        ]
    )
    grid = IndexedResearchService(ResearchRepository(repository.sessions), [], settings, None, index)
    chat = await ConversationRepository(repository.sessions).create("Stock semantics")
    result = await grid.start(chat, "ручки в наличии")
    assert result.pool_offer_ids == ["vto:stock-old-positive"]
    assert result.intent.branches[0].quantity is None
    assert result.intent.branches[0].constraints.availability_only
    result = await grid.start(chat, "ручки желательно в наличии")
    assert len(result.pool_offer_ids) == 3
    assert not result.intent.branches[0].constraints.availability_only


@pytest.mark.parametrize("query", ["ручки от 500 до 100 шт", "ручки от 0.5 шт", "бутылки больше 500 мл"])
def test_ambiguous_or_impossible_number_does_not_become_a_different_constraint(query):
    with pytest.raises(ValueError):
        parse_constraints(query)


async def test_compound_category_cannot_be_replaced_by_supplier_merchandising(repository, settings):
    from app.application.index_research import IndexedResearchService
    from app.database.conversations import ConversationRepository
    from app.database.index import IndexRepository
    from app.database.research import ResearchRepository
    from app.domain.models import Product
    from app.domain.query_understanding import QueryUnderstandingService

    index = IndexRepository(repository.sessions, settings)
    await index.ingest(
        [
            Product(
                id="vto:compound",
                supplier="vto",
                name="Рюкзак-мешок Melango",
                source_url="https://vto.kz/product/control/",
                metadata={
                    "parent_name": "Рюкзак-мешок Melango",
                    "supplier_category": "Наборы с рюкзаками и шопперами",
                },
            )
        ]
    )
    understanding = QueryUnderstandingService(repository.sessions)
    grid = IndexedResearchService(
        ResearchRepository(repository.sessions), [], settings, None, index, understanding
    )
    chat = await ConversationRepository(repository.sessions).create("Compound category")
    research = await grid.start(chat, "Рюкзак-мешок Melango")
    assert research.intent.categories == ["backpack"]
    assert research.pool_offer_ids == ["vto:compound"]
    assert all(d["action"] != "CORRECT" for d in research.intent.query_understanding["decisions"])


@pytest.mark.parametrize(
    "payload,status",
    [
        (b'{"query":"pens","query":"bottles"}', 400),
        (b'{"query":NaN}', 400),
        (b'{"query":', 400),
        (b'{"query":"\\ud800"}', 422),
        (b"[" * 30 + b"0" + b"]" * 30, 413),
        (b'{"query":"pens","padding":"' + b"x" * 65536 + b'"}', 413),
        (b'{"query":"pens","owner_id":"foreign","admin":true}', 422),
        (b'{"query":null}', 422),
        (b'{"query":true}', 422),
    ],
    ids=[
        "duplicate-fields",
        "nan",
        "broken-json",
        "surrogate",
        "depth",
        "bytes-limit",
        "server-fields",
        "null",
        "boolean",
    ],
)
async def test_body_validation_precedes_parsing(settings, payload, status, monkeypatch):
    app = create_app(settings, [])

    async def forbidden(*args, **kwargs):
        raise AssertionError("Expensive parsing ran on invalid input")

    monkeypatch.setattr(app.state.research, "grid_plan", forbidden)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    ) as c:
        response = await c.post(
            "/api/projects/00000000-0000-0000-0000-000000000001/requests",
            content=payload,
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == status, response.text
        assert "trace_id" in response.json()
    await app.state.engine.dispose()
