from datetime import timedelta

from conftest import make_product
from sqlalchemy import select, update

from app.database.index import IndexRepository
from app.database.models import CatalogSyncJob, ProductObservation, SupplierSyncState
from app.domain.models import utcnow


async def test_daily_tick_coalesces_with_existing_full_checkpoint(repository, settings):
    index = IndexRepository(repository.sessions, settings)
    legacy = await index.enqueue("ucontay", "CATALOG_DISCOVERY")
    checkpoint = {"roots_observed": True, "cursor": "page-42", "reason": "initial_population"}
    async with repository.sessions() as db, db.begin():
        await db.execute(update(CatalogSyncJob).where(CatalogSyncJob.id == legacy).values(
            status="RUNNING", checkpoint=checkpoint
        ))
    daily = await index.enqueue(
        "ucontay", "FULL_CATALOG_RECONCILIATION",
        {"reason": "scheduled_full_reconciliation", "parser_version": "availability-v1"},
    )
    assert daily == legacy
    async with repository.sessions() as db:
        jobs = (await db.scalars(select(CatalogSyncJob).where(
            CatalogSyncJob.supplier == "ucontay",
            CatalogSyncJob.kind.in_(("CATALOG_DISCOVERY", "FULL_CATALOG_RECONCILIATION")),
        ))).all()
        assert len(jobs) == 1
        assert jobs[0].checkpoint["cursor"] == "page-42"


async def test_hot_is_due_but_cold_eight_hour_observation_is_not(repository, settings):
    index = IndexRepository(repository.sessions, settings)
    await index.ingest([
        make_product(id="gifts:hot", supplier="gifts", source_url="https://gifts.ru/id/hot"),
        make_product(id="gifts:cold", supplier="gifts", source_url="https://gifts.ru/id/cold"),
    ])
    eight_hours_ago = utcnow() - timedelta(hours=8)
    three_hours_ago = utcnow() - timedelta(hours=3)
    async with repository.sessions() as db, db.begin():
        await db.execute(update(ProductObservation).where(
            ProductObservation.offer_id == "gifts:hot"
        ).values(last_verified_at=three_hours_ago, observed_at=three_hours_ago))
        await db.execute(update(ProductObservation).where(
            ProductObservation.offer_id == "gifts:cold"
        ).values(last_verified_at=eight_hours_ago, observed_at=eight_hours_ago))
    await index.mark_usage(["gifts:hot"], "opened")
    due, counts = await index.adaptive_refresh_candidates("gifts")
    assert [offer for offer, *_ in due] == ["gifts:hot"]
    assert counts == {"HOT": 1, "WARM": 0, "COLD": 1, "DORMANT": 0}


async def test_parser_anomaly_reactivates_only_for_new_parser_version(repository, settings):
    index = IndexRepository(repository.sessions, settings)
    identifier = await index.enqueue(
        "happygifts", "OBSERVATION_REFRESH", {"parser_version": "v2"}
    )
    future = utcnow() + timedelta(hours=4)
    async with repository.sessions() as db, db.begin():
        await db.execute(update(CatalogSyncJob).where(CatalogSyncJob.id == identifier).values(
            status="FAILED", error="PARSER_ANOMALY", available_at=future
        ))
    assert identifier == await index.enqueue(
        "happygifts", "AVAILABILITY_REFRESH", {"parser_version": "v2"}, priority=80
    )
    async with repository.sessions() as db:
        unchanged = await db.get(CatalogSyncJob, identifier)
        assert unchanged.status == "FAILED" and unchanged.available_at.replace(tzinfo=future.tzinfo) == future
    resumed = await index.enqueue(
        "happygifts", "AVAILABILITY_REFRESH", {"parser_version": "v3"}, priority=80
    )
    assert resumed == identifier
    async with repository.sessions() as db:
        job = await db.get(CatalogSyncJob, identifier)
        assert job.status == "PENDING" and job.error is None
        assert job.checkpoint["parser_version"] == "v3"


async def test_repeated_http_failure_opens_supplier_circuit(repository, settings):
    index = IndexRepository(repository.sessions, settings)
    identifier = await index.enqueue("oasis", "TARGETED_RESEARCH", {"query": "probe"})
    for attempt in range(settings.supplier_circuit_failure_threshold):
        async with repository.sessions() as db, db.begin():
            await db.execute(update(CatalogSyncJob).where(CatalogSyncJob.id == identifier).values(
                status="PENDING", available_at=utcnow() - timedelta(seconds=1)
            ))
            await db.execute(update(SupplierSyncState).where(
                SupplierSyncState.supplier == "oasis"
            ).values(retry_after=utcnow() - timedelta(seconds=1), lease_until=utcnow() - timedelta(seconds=1)))
        job = await index.claim(f"worker-{attempt}")
        assert job
        await index.finish(job, f"worker-{attempt}", "PENDING", "SUPPLIER_HTTP_ERROR")
    async with repository.sessions() as db:
        state = await db.get(SupplierSyncState, "oasis")
        job = await db.get(CatalogSyncJob, identifier)
        assert state.circuit_open_until.replace(tzinfo=utcnow().tzinfo) > utcnow()
        assert job.error == "TEMPORARILY_UNAVAILABLE"
        assert job.available_at.replace(tzinfo=utcnow().tzinfo) > utcnow() + timedelta(minutes=14)
