from datetime import timedelta

import pytest
from conftest import make_product
from sqlalchemy import update

from app.application.index_query import IndexQueryService
from app.application.index_research import IndexedResearchService
from app.application.research_planner import ResearchPlanner
from app.application.research_refinement import ResearchRefinementEngine
from app.database.conversations import ConversationRepository
from app.database.index import IndexRepository
from app.database.models import CatalogSyncJob, IndexedProduct, SupplierSyncState
from app.database.research import ResearchRepository
from app.domain.models import utcnow
from app.domain.taxonomy import CategoryMatch, ProductCategoryMatcher


async def test_pencil_regression_isolated_from_bottle_research():
    planner = ResearchPlanner()
    bottles = await planner.parse("Синие бутылки 300 шт")
    pencils = await planner.parse("Карандаши 300шт")
    assert bottles.categories == ["bottle"]
    assert pencils.categories == ["pencil"]
    assert pencils.quantity == 300 and not pencils.colors
    assert not pencils.discovery


@pytest.mark.parametrize(
    "query,category",
    [
        ("Ручки 300 шт", "pen"),
        ("Карандашей 300 шт", "pencil"),
        ("Зонты 100 шт", "umbrella"),
        ("Рюкзаки 200 шт", "backpack"),
        ("Термосы 50 шт", "thermos"),
        ("Блокноты 300 шт", "notebook"),
        ("Powerbank 100 шт", "powerbank"),
    ],
)
async def test_categories(query, category):
    assert (await ResearchPlanner().parse(query)).categories == [category]


async def test_all_results_freshness_unknown_term_and_no_browser(repository, settings):
    index = IndexRepository(repository.sessions, settings)
    products = [
        make_product(
            id=f"oasis:pencil-{n}",
            name=f"Карандаш деревянный Model{n}",
            material="дерево",
            stock_quantity=400,
        )
        for n in range(61)
    ]
    products += [
        make_product(id="oasis:stale", name="Карандаш старый", fetched_at=utcnow() - timedelta(hours=2)),
        make_product(id="oasis:bottle", name="Бутылка синяя"),
        make_product(id="oasis:unknown", name="Антистресс Куб", stock_quantity=500),
    ]
    await index.ingest(products)
    intent = await ResearchPlanner().parse("Карандаши 300шт")
    result, metrics = await IndexQueryService(index).search(intent)
    assert len(result) == 62 and metrics["browser_invoked"] is False
    assert next(p for p in result if p.id == "oasis:stale").metadata["procurement_status"] == "NEEDS_REFRESH"
    unknown = await ResearchPlanner().parse("Антистрессы 100 шт")
    assert unknown.categories[0].startswith("dynamic:")
    assert len((await IndexQueryService(index).search(unknown))[0]) == 1
    chat = await ConversationRepository(repository.sessions).create("Index")
    service = IndexedResearchService(ResearchRepository(repository.sessions), [], settings, None, index)
    session = await service.start(chat, "Карандаши 300шт")
    assert len(session.indexed_offer_ids) == 62 and session.background_job_ids
    engine = ResearchRefinementEngine(service)
    value, decision, _ = await engine.apply(session.id, "Только деревянные")
    assert decision.action == "LOCAL_SEMANTIC_REFINE"
    value, _, _ = await engine.apply(session.id, "До 2000 тенге")
    assert len(value.products) == 62 and value.view.filters.max_price == 2000
    assert not service.tasks
    restored = await service.repository.get(session.id)
    assert len(restored.products) == 62 and restored.view.filters.max_price == 2000
    assert service.independent_query("Синий мерч для IT конференции 300шт", restored)
    assert service.independent_query("Антистресс 100шт", restored)
    assert not service.independent_query("Только деревянные", restored)
    newer = await service.start(chat, "Ручки 300шт")
    await service.repository.save(restored)
    assert (await service.repository.latest(chat)).id == newer.id


async def test_rare_category_without_quantity_keeps_stale_or_unknown_stock(repository, settings):
    index = IndexRepository(repository.sessions, settings)
    product = make_product(
        id="happygifts:flask-1",
        supplier="happygifts",
        source_url="https://happygifts.ru/catalog/posuda/flyazhki/flask-1/",
        name="Подарочная фляжка Steel",
        category=None,
        stock_quantity=None,
        original_price=None,
        original_currency=None,
        fetched_at=utcnow() - timedelta(days=4),
        metadata={"supplier_category": "Фляжки", "breadcrumbs": ["Посуда", "Фляжки"]},
    )
    await index.ingest([product])
    intent = await ResearchPlanner().parse("фляжки")
    assert intent.quantity is None and intent.categories[0].startswith("dynamic:")
    found, metrics = await IndexQueryService(index).search(intent)
    assert [p.id for p in found] == ["happygifts:flask-1"]
    assert found[0].fetched_at < utcnow() - timedelta(days=3)
    strict, _ = await IndexQueryService(index).search(await ResearchPlanner().parse("фляжки 300 шт"))
    assert [p.id for p in strict] == ["happygifts:flask-1"]
    assert strict[0].metadata["procurement_status"] == "NEEDS_REFRESH"
    assert metrics["browser_invoked"] is False


async def test_known_taxonomy_is_not_a_gate_for_older_supplier_classification(repository, settings):
    index = IndexRepository(repository.sessions, settings)
    products = [
        make_product(
            id=f"gifts:keychain-{number}",
            supplier="gifts",
            source_url=f"https://gifts.ru/id/keychain-{number}",
            name=f"Брелок металлический Model {number}",
            category=None,
            fetched_at=utcnow() - timedelta(hours=2),
        )
        for number in range(12)
    ]
    await index.ingest(products)
    # Emulate products indexed before the current taxonomy learned this broad
    # category. Exact supplier text must remain searchable through Russian FTS.
    async with repository.sessions() as db, db.begin():
        await db.execute(update(IndexedProduct).values(category=None))

    intent = await ResearchPlanner().parse("брелки")
    assert intent.categories == ["accessory"] and intent.quantity is None
    found, metrics = await IndexQueryService(index).search(intent)
    assert len(found) == 12
    assert metrics["browser_invoked"] is False


async def test_available_now_and_incoming_are_distinct_procurement_evidence(repository, settings):
    index = IndexRepository(repository.sessions, settings)
    values = {
        "A": (500, None), "B": (100, 500), "C": (0, 500),
        "D": (None, 500), "E": (100, None),
    }
    await index.ingest([
        make_product(
            id=f"gifts:availability-{key}", supplier="gifts",
            source_url=f"https://gifts.ru/id/availability-{key}",
            name=f"Брелок availability {key}", stock_quantity=available,
            incoming_quantity=incoming,
            source_availability_payload={"free": available, "incoming": incoming},
        )
        for key, (available, incoming) in values.items()
    ])
    default = await ResearchPlanner().parse("брелки 300 шт")
    found, _ = await IndexQueryService(index).search(default)
    assert {p.id.rsplit("-", 1)[-1] for p in found} == {"A", "B", "C", "D"}
    strict = await ResearchPlanner().parse("брелки 300 шт, только в наличии")
    found, _ = await IndexQueryService(index).search(strict)
    assert [p.id.rsplit("-", 1)[-1] for p in found] == ["A"]
    no_quantity = await ResearchPlanner().parse("брелки")
    found, _ = await IndexQueryService(index).search(no_quantity)
    assert {p.id.rsplit("-", 1)[-1] for p in found} == set(values)


@pytest.mark.parametrize("query", [
    "фляжка", "фляжки", "фляжек", "фляжку", "фляжкой",
    "шейкеры", "штопоры", "термосумки", "дождевики",
])
async def test_rare_concepts_remain_specific_dynamic_queries(query):
    intent = await ResearchPlanner().parse(query)
    assert intent.quantity is None
    assert len(intent.categories) == 1


async def test_catalog_graph_accelerates_target_and_interactive_priority(repository, settings):
    index = IndexRepository(repository.sessions, settings)
    catalog = await index.enqueue("happygifts", "CATALOG_DISCOVERY")
    async with repository.sessions() as db, db.begin():
        await db.execute(update(CatalogSyncJob).where(CatalogSyncJob.id == catalog).values(
            status="COMPLETED",
            checkpoint={"roots": [{
                "url": "https://happygifts.ru/catalog/posuda/flyazhki/",
                "label": "Подарочные фляжки",
            }]},
        ))
    chat = await ConversationRepository(repository.sessions).create("Rare category")
    service = IndexedResearchService(ResearchRepository(repository.sessions), [], settings, None, index)
    research = await service.start(chat, "фляжки")
    assert research.intent.quantity is None
    async with repository.sessions() as db:
        jobs = (await db.scalars(
            __import__("sqlalchemy").select(CatalogSyncJob).where(
                CatalogSyncJob.id.in_(research.background_job_ids)
            )
        )).all()
    happy = next(job for job in jobs if job.supplier == "happygifts")
    assert happy.priority == 100
    assert happy.checkpoint["category_graph_hit"] is True
    assert happy.checkpoint["route"].endswith("/catalog/posuda/flyazhki/")


async def test_first_targeted_match_streams_into_an_empty_session(repository, settings):
    index = IndexRepository(repository.sessions, settings)
    chat = await ConversationRepository(repository.sessions).create("Cold targeted")
    service = IndexedResearchService(ResearchRepository(repository.sessions), [], settings, None, index)
    research = await service.start(chat, "фляжки")
    assert not research.products and research.background_job_ids
    await index.ingest([make_product(
        id="happygifts:streamed-flask", supplier="happygifts",
        source_url="https://happygifts.ru/catalog/posuda/flyazhki/streamed/",
        name="Фляжка Streamed", category=None, stock_quantity=None,
        metadata={"supplier_category": "Фляжки"},
    )])
    synced = await service.sync(research.id)
    assert [p.id for p in synced.products] == ["happygifts:streamed-flask"]
    assert synced.indexed_offer_ids == ["happygifts:streamed-flask"]
    assert synced.pending_offer_ids == []


def test_relevance_rejects_mentions_and_expansion_stays_in_category():
    from app.application.research_planner import QueryExpansionService
    from app.domain.research import ResearchBudget

    matcher = ProductCategoryMatcher()
    assert (
        matcher.match(make_product(name="Бутылка", description="В подарок карандаш"), "pencil")
        == CategoryMatch.REJECTED
    )
    assert matcher.match(make_product(name="Футляр для карандашей"), "pencil") == CategoryMatch.REJECTED
    queries = QueryExpansionService().expand("pencil", ResearchBudget())
    assert queries and not any("бутыл" in q for q in queries)


async def test_queue_lock_recovery_cancel_and_backoff(repository, settings):
    index = IndexRepository(repository.sessions, settings)
    first = await index.enqueue("gifts", "TARGETED_RESEARCH", {"query": "карандаш"})
    assert first == await index.enqueue("gifts", "TARGETED_RESEARCH", {"query": "карандаш"})
    await index.enqueue("gifts", "OBSERVATION_REFRESH")
    job = await index.claim("worker-one")
    assert job and await index.claim("worker-two") is None
    async with repository.sessions() as db, db.begin():
        await db.execute(update(SupplierSyncState).values(lease_until=utcnow() - timedelta(seconds=1)))
    recovered = await index.claim("worker-two")
    assert recovered["id"] == job["id"]
    await index.finish(recovered, "worker-two", "PENDING", "SUPPLIER_CAPTCHA_REQUIRED")
    async with repository.sessions() as db:
        row = await db.get(CatalogSyncJob, first)
        assert row.available_at.replace(tzinfo=utcnow().tzinfo) > utcnow() + timedelta(minutes=59)
    await index.cancel(first)
    assert await index.cancelled(first)


async def test_interactive_claim_precedes_background_work(repository, settings):
    index = IndexRepository(repository.sessions, settings)
    await index.enqueue("artegifts", "CATALOG_DISCOVERY", priority=0)
    targeted = await index.enqueue(
        "happygifts", "TARGETED_RESEARCH", {"query": "фляжки"}, priority=100
    )
    claimed = await index.claim("interactive", kinds={"TARGETED_RESEARCH"})
    assert claimed["id"] == targeted and claimed["priority"] == 100
    assert claimed["metrics"]["targeted_job_queue_ms"] >= 0


async def test_background_sync_keeps_all_observations(repository, settings):
    from test_research import ResearchProvider

    from app.application.background_index import BackgroundIndex

    index = IndexRepository(repository.sessions, settings)
    host = BackgroundIndex(index, [ResearchProvider()], settings)
    await index.enqueue("gifts", "TARGETED_RESEARCH", {"query": "бутылка", "category": "bottle"})
    job = await index.claim(host.owner)

    async def immediate(_job, call, _metric):
        return await call()

    host.request = immediate
    await host.run(job)
    results, _ = await IndexQueryService(index).search(await ResearchPlanner().parse("Бутылки 300шт"))
    assert len(results) == 60
    async with repository.sessions() as db:
        assert (await db.get(CatalogSyncJob, job["id"])).status == "COMPLETED"


async def test_scheduler_staggers_initial_discovery(repository, settings):
    import asyncio
    from contextlib import suppress

    from conftest import FakeProvider
    from sqlalchemy import select

    from app.application.background_index import BackgroundIndex

    index = IndexRepository(repository.sessions, settings)
    host = BackgroundIndex(index, [FakeProvider("gifts"), FakeProvider("ucontay")], settings)
    task = asyncio.create_task(host.schedule())
    try:
        async with asyncio.timeout(3):
            while len(await index.states()) < 2:
                await asyncio.sleep(0.01)
        async with repository.sessions() as db:
            states = (
                await db.scalars(select(SupplierSyncState).order_by(SupplierSyncState.next_discovery_at))
            ).all()
            assert (states[1].next_discovery_at - states[0].next_discovery_at).total_seconds() >= 299
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


async def test_background_failure_isolation_and_refresh(repository, settings):
    from sqlalchemy import select
    from test_research import ResearchProvider

    from app.application.background_index import BackgroundIndex
    from app.database.models import ProductObservation
    from app.domain.errors import SupplierError

    index = IndexRepository(repository.sessions, settings)
    provider = ResearchProvider()
    host = BackgroundIndex(index, [provider], settings)
    await index.ingest(
        [
            make_product(
                id="gifts:old",
                supplier="gifts",
                name="Бутылка",
                source_url="https://gifts.ru/id/old",
                fetched_at=utcnow() - timedelta(hours=2),
            )
        ]
    )
    await index.enqueue("gifts", "OBSERVATION_REFRESH")
    job = await index.claim(host.owner)

    async def immediate(_job, call, _metric):
        return await call()

    host.request = immediate
    await host.run(job)
    async with repository.sessions() as db:
        observation = await db.get(ProductObservation, "gifts:old")
        assert observation.status == "FRESH" and observation.stock == 300
    provider.error = SupplierError("SUPPLIER_CAPTCHA_REQUIRED", "test")
    await index.enqueue("gifts", "TARGETED_RESEARCH", {"query": "new"})
    job = await index.claim(host.owner)
    await host.run(job)
    await index.enqueue("oasis", "TARGETED_RESEARCH", {"query": "other"})
    next_job = await index.claim("another-worker")
    assert next_job["supplier"] == "oasis"
    async with repository.sessions() as db:
        state = await db.scalar(select(SupplierSyncState).where(SupplierSyncState.supplier == "gifts"))
        assert state.error == "SUPPLIER_CAPTCHA_REQUIRED"
