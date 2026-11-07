"""Card failures never masquerade as a source outage or overwrite newer stock."""

import asyncio
import copy
import os
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.application.vto_sync import sync_slice
from app.config import Settings
from app.database.index import IndexRepository
from app.database.models import CatalogSyncJob, ProductObservation, SupplierOffer, SupplierSyncState
from app.domain.errors import SupplierError
from app.domain.models import utcnow
from app.providers.vto import VTOProvider
from app.providers.vto.provider import normalize


@pytest.fixture
async def repository(repository, settings):
    """Optional real-PG replay; each case owns one generated schema on :55433.

    The inherited default remains disposable SQLite for the normal full suite.
    Never point this opt-in fixture at the application's main database.
    """
    url = os.environ.get("VTO_FAULT_POSTGRES_URL")
    if not url:
        yield repository
        return
    from uuid import uuid4

    from app.database.connection import create_database
    from app.database.models import Base
    from app.database.repository import SearchRepository

    assert url == "postgresql+asyncpg://quality_owner@127.0.0.1:55433/postgres"
    schema = "quality_faults_" + uuid4().hex
    engine, _ = create_database(url)
    translated = engine.execution_options(schema_translate_map={None: schema})
    settings.database_url = url
    try:
        async with engine.begin() as connection:
            await connection.execute(text('CREATE SCHEMA "' + schema + '"'))
        async with translated.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        yield SearchRepository(async_sessionmaker(translated, expire_on_commit=False))
    finally:
        async with engine.begin() as connection:
            await connection.execute(text('DROP SCHEMA "' + schema + '" CASCADE'))
        await engine.dispose()


@pytest.fixture
def melango():
    import json
    from pathlib import Path

    return json.loads((Path(__file__).parent / "fixtures/vto/melango.json").read_text("utf-8"))


async def test_late_old_positive_cannot_overwrite_new_zero(repository, settings, melango):
    index = IndexRepository(repository.sessions, settings)
    old = normalize(**melango)[0]
    new = copy.deepcopy(old)
    new.fetched_at = old.fetched_at + timedelta(minutes=1)
    new.stock_quantity = new.total_quantity = 0
    new.availability_records = []
    new.availability_status = "UNAVAILABLE_NOW"
    await index.ingest([new])
    await index.ingest([old])
    async with repository.sessions() as db:
        observation = await db.get(ProductObservation, old.id)
        assert observation.stock == 0
        assert (await db.get(SupplierOffer, old.id)).commercial_hash


@pytest.mark.parametrize("code", ["VTO_NOT_FOUND", "VTO_PARSING_ERROR", "VTO_UNPROCESSED_PRODUCTS", "VTO_VARIATION_INCOMPLETE", "PERSISTENCE_DATA_ERROR"])
async def test_card_error_keeps_other_jobs_claimable(repository, settings, code):
    index = IndexRepository(repository.sessions, settings)
    refresh = await index.enqueue("vto", "OBSERVATION_REFRESH", priority=80)
    full = await index.enqueue("vto", "FULL_CATALOG_RECONCILIATION")
    job = await index.claim("one", job_ids=[refresh])
    await index.finish(job, "one", "FAILED", code)
    assert (await index.claim("two", job_ids=[full]))["id"] == full


async def test_genuine_retry_after_remains_supplier_wide(repository, settings):
    index = IndexRepository(repository.sessions, settings)
    first = await index.enqueue("vto", "OBSERVATION_REFRESH")
    full = await index.enqueue("vto", "FULL_CATALOG_RECONCILIATION")
    job = await index.claim("one", job_ids=[first])
    job["metrics"]["retry_after_seconds"] = 7200
    await index.finish(job, "one", "PENDING", "VTO_HTTP_ERROR")
    assert await index.claim("two", job_ids=[full]) is None
    async with repository.sessions() as db:
        state = await db.get(SupplierSyncState, "vto")
        assert state.retry_after.replace(tzinfo=utcnow().tzinfo) > utcnow() + timedelta(hours=1)


async def test_compatibility_repair_never_steals_an_active_lease(repository, settings):
    index = IndexRepository(repository.sessions, settings)
    first = await index.enqueue("vto", "OBSERVATION_REFRESH")
    await index.claim("one", job_ids=[first])
    async with repository.sessions() as db, db.begin():
        await db.execute(
            update(SupplierSyncState)
            .where(SupplierSyncState.supplier == "vto")
            .values(error="VTO_NOT_FOUND", retry_after=utcnow() + timedelta(hours=4))
        )
    assert await index.claim("two") is None


async def test_initial_import_gets_a_turn_without_overtaking_interactive_work(repository, settings):
    index = IndexRepository(repository.sessions, settings)
    full = await index.enqueue("vto", "FULL_CATALOG_RECONCILIATION")
    await index.enqueue("vto", "OBSERVATION_REFRESH", priority=80)
    async with repository.sessions() as db, db.begin():
        await db.execute(
            update(CatalogSyncJob)
            .where(CatalogSyncJob.id == full)
            .values(updated_at=utcnow() - timedelta(hours=1))
        )
    interactive = await index.enqueue("vto", "TARGETED_RESEARCH", {"query": "pens"}, priority=100)
    job = await index.claim("one")
    assert job["id"] == interactive
    await index.finish(job, "one", "COMPLETED")
    assert (await index.claim("two"))["id"] == full


@pytest.mark.parametrize("code,card", [("VTO_PARSING_ERROR", True), ("VTO_HTTP_ERROR", False)])
async def test_source_error_does_not_spend_card_retry_budget(repository, settings, melango, code, card):
    index = IndexRepository(repository.sessions, settings)
    job_id = await index.enqueue("vto", "FULL_CATALOG_RECONCILIATION")
    job = await index.claim("one", job_ids=[job_id])

    class Provider:
        requests = errors = 0
        parser_version = "test"

        def __init__(self):
            self.settings = settings

        async def collection(self, page, per_page):
            return [melango["parent"]], 1, 1

        async def products(self, parent):
            raise SupplierError(code, "controlled fault", retryable=not card)

    if card:
        assert await sync_slice(index, Provider(), job) == ("FAILED", "VTO_UNPROCESSED_PRODUCTS")
        assert next(iter(job["checkpoint"]["failed_products"].values()))["attempts"] == 3
    else:
        with pytest.raises(SupplierError, match="controlled fault"):
            await sync_slice(index, Provider(), job)
        assert not job["checkpoint"]["failed_products"]
        assert len(job["checkpoint"]["pending_products"]) == 1


async def test_refresh_by_persisted_id_avoids_slug_lookup(melango):
    requested = []

    async def handler(request):
        requested.append(str(request.url))
        if request.url.path.endswith("/1521733"):
            return httpx.Response(200, json=melango["parent"])
        return httpx.Response(404)

    provider = VTOProvider(
        Settings(vto_request_delay_seconds=0.1), httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    try:
        with pytest.raises(SupplierError):
            await provider.research_products("https://vto.kz/product/old-name/", parent_id=1521733)
        assert requested[0].endswith("/products/1521733")
        assert not any("slug=" in url for url in requested)
    finally:
        await provider.close()


async def test_collection_404_is_source_error_not_missing_card():
    provider = VTOProvider(
        Settings(vto_request_delay_seconds=0.1),
        httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(404))),
    )
    try:
        with pytest.raises(SupplierError) as error:
            await provider.collection()
        assert error.value.code == "VTO_HTTP_ERROR"
    finally:
        await provider.close()


async def test_verified_core_keeps_unknown_stock_and_does_not_erase_detail(repository, settings, melango):
    children = [
        dict(
            id=v["variation_id"],
            parent=melango["parent"]["id"],
            permalink=melango["parent"]["permalink"],
            sku=v["sku"],
            prices={"price": "2084", "currency_minor_unit": 0, "currency_code": "KZT"},
            attributes=[{"name": "Цвет", "value": "синий"}],
            images=[],
        )
        for v in melango["detail"]["variants"]
    ]

    def handler(request):
        assert request.url.params.get("parent") == str(melango["parent"]["id"])
        return httpx.Response(200, json=children, headers={"X-WP-Total": "4", "X-WP-TotalPages": "1"})

    provider = VTOProvider(
        Settings(vto_request_delay_seconds=0.1), httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    index = IndexRepository(repository.sessions, settings)
    try:
        core = await provider.core_products(melango["parent"])
        assert {p.id for p in core} == {f"vto:{c['id']}" for c in children}
        assert all(p.stock_quantity is None and p.original_price == 2084 for p in core)
        await index.ingest([core[0]])
        complete = normalize(**melango)[0]
        complete.fetched_at = core[0].fetched_at + timedelta(seconds=1)
        await index.ingest([complete])
        core[0].fetched_at = complete.fetched_at + timedelta(seconds=1)
        await index.ingest([core[0]])
        async with repository.sessions() as db:
            assert (await db.get(ProductObservation, complete.id)).stock == complete.stock_quantity
            assert (await db.get(SupplierOffer, complete.id)).payload["brand"] == "Molti"
    finally:
        await provider.close()


async def test_failed_refresh_card_moves_aside_and_retries_are_bounded(repository, settings):
    from app.application.background_index import BackgroundIndex

    index = IndexRepository(repository.sessions, settings)
    job_id = await index.enqueue(
        "vto",
        "OBSERVATION_REFRESH",
        {"pending": ["https://vto.kz/product/deleted/", "https://vto.kz/product/healthy/"]},
    )
    job = await index.claim("one", job_ids=[job_id])

    class Provider:
        supplier = "vto"
        parser_version = "test"

        def observation_group_key(self, url):
            return url

        async def research_products(self, url):
            if "deleted" in url:
                raise SupplierError("VTO_NOT_FOUND", "controlled missing page")
            return []

    worker = BackgroundIndex(index, [Provider()], settings)
    stored = []

    async def store(job, products):
        stored.append(products)

    worker.store = store
    for _ in range(4):
        await worker.refresh(job, worker.providers["vto"])
    assert len(stored) == 1
    failure = next(iter(job["checkpoint"]["failed_products"].values()))
    assert failure["attempts"] == 3 and failure["stage"] == "DETAIL_ENRICHMENT"
    assert job["checkpoint"]["pending"] == []


async def test_variant_checkpoint_resumes_without_replaying_html_or_first_page(melango):
    import json

    parent = copy.deepcopy(melango["parent"])
    parent["variations"] = []
    requests = []

    def handler(request):
        requests.append(str(request.url))
        if "/product/" in request.url.path:
            return httpx.Response(
                200,
                text=f'<form class="variations_form" data-product_id="{parent["id"]}" data-product_variations="false"></form>',
            )
        page = int(request.url.params["page"])
        ids = range(1001, 1021) if page == 1 else [1021]
        rows = [
            dict(
                id=i,
                parent=parent["id"],
                permalink=parent["permalink"],
                prices={"price": "100", "currency_code": "KZT", "currency_minor_unit": 0},
                attributes=[{"name": "Цвет", "value": "синий"}],
            )
            for i in ids
        ]
        return httpx.Response(200, json=rows, headers={"X-WP-Total": "21", "X-WP-TotalPages": "2"})

    provider = VTOProvider(
        Settings(vto_request_delay_seconds=0.1), httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    progress = {}
    saved = 0

    async def checkpoint():
        nonlocal saved
        saved += 1
        if saved == 2:
            raise asyncio.CancelledError()

    try:
        with pytest.raises(asyncio.CancelledError):
            await provider.resume_products(parent, progress, checkpoint)
        durable = json.loads(json.dumps(progress))
        products = await provider.resume_products(parent, durable, None)
        assert len(products) == 21 and len({p.id for p in products}) == 21
        assert len(requests) == 3
        assert sum("/product/" in url for url in requests) == 1
        assert sum("page=1" in url for url in requests) == 1
        assert all(p.stock_quantity is None for p in products)
    finally:
        await provider.close()


async def test_fast_parent_commits_before_slow_neighbour_and_cancel_cleans_tasks(
    repository, settings, melango
):
    index = IndexRepository(repository.sessions, settings)
    job_id = await index.enqueue("vto", "FULL_CATALOG_RECONCILIATION")
    job = await index.claim("one", job_ids=[job_id])
    slow_cancelled = asyncio.Event()
    second = copy.deepcopy(melango["parent"])
    second["id"] += 1
    detail = copy.deepcopy(melango["detail"])
    detail["parent_id"] = second["id"]

    class Provider:
        requests = errors = 0

        def __init__(self):
            self.settings = settings

        async def collection(self, page, per_page):
            return [melango["parent"], second], 2, 1

        async def products(self, parent):
            if parent["id"] == melango["parent"]["id"]:
                try:
                    await asyncio.Event().wait()
                finally:
                    slow_cancelled.set()
            return normalize(parent, detail)

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(sync_slice(index, Provider(), job), timeout=1)
    assert slow_cancelled.is_set()
    assert second["id"] in job["checkpoint"]["seen_parent_ids"]
    assert len(job["checkpoint"]["pending_products"]) == 1
    async with repository.sessions() as db:
        assert len((await db.scalars(select(SupplierOffer))).all()) == 4
