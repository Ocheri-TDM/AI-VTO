"""Controlled clocks/transport: no real requests or waiting on supplier timers."""

from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest
from test_vto_throughput import repository as _pg_repository

from app.database.index import IndexRepository
from app.database.models import CatalogSyncJob, SupplierSyncState
from app.domain.errors import SupplierError
from app.domain.offer_lifecycle import aware
from app.domain.vto_retry import plan, record_error
from app.providers.vto.provider import VTOProvider

repository = _pg_repository


async def test_scheduler_coalescing_does_not_erase_a_durable_local_retry(repository, settings, monkeypatch):
    clock = [datetime.now(UTC) + timedelta(hours=1)]
    monkeypatch.setattr("app.database.index.utcnow", lambda: clock[0])
    index = IndexRepository(repository.sessions, settings)
    identifier = await index.enqueue("vto", "FULL_CATALOG_RECONCILIATION", {"page": 41})
    job = await index.claim("worker", job_ids=[identifier])
    await index.finish(job, "worker", "PENDING", "VTO_LISTING_PAGE_DEFERRED")
    assert (
        await index.enqueue(
            "vto", "FULL_CATALOG_RECONCILIATION", {"reason": "scheduler"}, available_at=clock[0]
        )
        == identifier
    )
    assert await index.claim("early", job_ids=[identifier]) is None
    clock[0] += timedelta(seconds=60)
    assert (await index.claim("due", job_ids=[identifier]))["id"] == identifier


async def test_failed_smaller_probe_is_not_retried_and_does_not_change_cursor():
    from app.application.vto_sync import smaller_listing_probe

    checkpoint = {"page": 41, "per_page": 100, "seen_parent_ids": [], "pending_products": []}
    metrics = {"transport_error": {"stage": "listing", "http_status": 502}, "retry_decision": {"failures": 4}}
    calls = []

    class Provider:
        async def collection(self, page, size):
            calls.append((page, size))
            raise SupplierError("VTO_HTTP_ERROR", "controlled 502")

    async def save():
        pass

    with pytest.raises(SupplierError):
        await smaller_listing_probe(Provider(), checkpoint, metrics, save)
    await smaller_listing_probe(Provider(), checkpoint, metrics, save)
    assert calls == [(81, 50)] and checkpoint["per_page"] == 100 and checkpoint["page"] == 41


async def test_deferring_a_page_during_mass_outage_keeps_transport_backoff():
    from app.application.vto_sync import collection_page

    checkpoint = {"page": 41, "per_page": 100, "failed_listing_pages": {"41": {"attempts": 1}}}

    class Provider:
        async def collection(self, page, size):
            raise SupplierError("VTO_HTTP_ERROR", "controlled 502", evidence={"http_status": 502})

    async def save():
        pass

    with pytest.raises(SupplierError) as exc:
        await collection_page(Provider(), checkpoint, 41, save, {"retry_decision": {"failures": 4}})
    assert exc.value.code == "VTO_HTTP_ERROR" and checkpoint["page"] == 42


async def test_smaller_probe_validates_before_converting_cursor_and_preserves_failed_ranges(settings):
    from app.application.vto_sync import smaller_listing_probe

    checkpoint = {
        "page": 41,
        "per_page": 100,
        "seen_parent_ids": [1],
        "pending_products": [],
        "failed_listing_pages": {"40": {"page": 40, "per_page": 100, "attempts": 2}},
    }
    metrics = {"transport_error": {"stage": "listing", "http_status": 502}, "retry_decision": {"failures": 4}}
    saved = []

    class Provider:
        async def collection(self, page, size):
            assert (page, size) == (81, 50)
            return [{"id": i} for i in range(4001, 4051)], 4500, 90

    async def save():
        saved.append(True)

    await smaller_listing_probe(Provider(), checkpoint, metrics, save)
    assert checkpoint["per_page"] == 50 and checkpoint["page"] == 82
    assert set(checkpoint["failed_listing_pages"]) == {"79", "80"}
    assert len(checkpoint["pending_products"]) == 50 and checkpoint["seen_parent_ids"] == [1]
    assert metrics["listing_size_probe"]["offset"] == 4000 and saved == [True, True]
    # A later slice keeps the proved page size and cannot start a cascade of probes.
    await smaller_listing_probe(Provider(), checkpoint, metrics, save)
    assert saved == [True, True]


@pytest.mark.parametrize("recovers", [True, False])
async def test_failed_listing_page_is_durable_and_healthy_pages_continue(repository, settings, recovers):
    from app.application.vto_sync import sync_slice
    from app.domain.models import Product

    index = IndexRepository(repository.sessions, settings)
    identifier = await index.enqueue("vto", "FULL_CATALOG_RECONCILIATION")
    job = await index.claim("one", job_ids=[identifier])

    class Provider:
        requests = errors = 0
        parser_version = "controlled-page-recovery"

        def __init__(self):
            self.settings = settings
            self.calls = []

        async def collection(self, page, size):
            self.requests += 1
            self.calls.append(page)
            if page == 2 and (not recovers or self.calls.count(2) <= 2):
                self.errors += 1
                raise SupplierError(
                    "VTO_HTTP_ERROR",
                    "controlled 502",
                    retryable=True,
                    evidence={"http_status": 502, "stage": "listing"},
                )
            return [{"id": page, "type": "simple"}], 3, 3

        async def products(self, parent):
            return [
                Product(
                    id=f"vto:page-test-{parent['id']}",
                    supplier="vto",
                    source_url=f"https://vto.kz/product/page-{parent['id']}/",
                    name="Ручка",
                    original_currency="KZT",
                    metadata={"vto_parent_id": parent["id"]},
                )
            ]

    provider = Provider()
    for _ in range(2):
        with pytest.raises(SupplierError):
            await sync_slice(index, provider, job)
    assert job["checkpoint"]["page"] == 3
    assert job["checkpoint"]["failed_listing_pages"]["2"]["attempts"] == 2
    if not recovers:
        with pytest.raises(SupplierError):
            await sync_slice(index, provider, job)
    result = await sync_slice(index, provider, job)
    assert 3 in job["checkpoint"]["seen_parent_ids"]
    if recovers:
        assert result == ("COMPLETED", None) and job["checkpoint"]["coverage_complete"]
        assert set(job["checkpoint"]["seen_parent_ids"]) == {1, 2, 3}
    else:
        assert result == ("FAILED", "VTO_UNPROCESSED_PAGES")
        assert not job["checkpoint"].get("coverage_complete")


async def test_initial_import_cannot_be_starved_by_another_refresh_claim(repository, settings):
    index = IndexRepository(repository.sessions, settings)
    full = await index.enqueue("vto", "FULL_CATALOG_RECONCILIATION")
    await index.enqueue("vto", "AVAILABILITY_REFRESH", priority=100)
    async with repository.sessions.begin() as db:
        row = await db.get(CatalogSyncJob, full)
        row.updated_at = datetime.now(UTC) - timedelta(seconds=31)
    assert await index.claim("refresh", kinds={"AVAILABILITY_REFRESH"}) is None
    assert (await index.claim("full", kinds={"FULL_CATALOG_RECONCILIATION"}))["id"] == full


@pytest.mark.parametrize("source_deadline", [0, 900])
async def test_only_proved_legacy_internal_wait_is_repaired(repository, settings, source_deadline):
    index = IndexRepository(repository.sessions, settings)
    identifier = await index.enqueue("vto", "FULL_CATALOG_RECONCILIATION", {"page": 40})
    now = datetime.now(UTC)
    async with repository.sessions.begin() as db:
        state = await db.get(SupplierSyncState, "vto")
        state.lease_until = now - timedelta(seconds=1)
        state.retry_after = now + timedelta(minutes=30)
        state.error, state.failures = "VTO_HTTP_ERROR", 2
        job = await db.get(CatalogSyncJob, identifier)
        job.updated_at = now - timedelta(minutes=3)
        job.error = "VTO_HTTP_ERROR"
        job.metrics = {"last_error": "Server error '502 Bad Gateway'", "retry_after_seconds": source_deadline}
    result = await index.claim("one", job_ids=[identifier])
    if source_deadline:
        assert result is None
    else:
        assert result["checkpoint"] == {"page": 40}
        assert result["metrics"]["legacy_wait_repair"]["reason"] == "PROVED_INTERNAL_15_30_MINUTE_BACKOFF"


@pytest.mark.parametrize(
    "failures,minimum,maximum", [(0, 60, 90), (1, 90, 120), (2, 180, 225), (5, 1440, 1800)]
)
def test_transient_retry_bounded_and_persistent_failures_escalate(settings, failures, minimum, maximum):
    now = datetime(2026, 11, 7, 9, tzinfo=UTC)
    value = plan("VTO_HTTP_ERROR", failures, {}, now, settings)
    assert minimum <= (value["next_attempt_at"] - now).total_seconds() <= maximum
    assert value["failures"] == failures + 1
    assert bool(value["circuit_open_until"]) == (failures + 1 >= settings.supplier_circuit_failure_threshold)


@pytest.mark.parametrize("date_header", [False, True])
async def test_retry_after_has_one_durable_owner_survives_restart_and_prevents_early_http(
    repository, settings, monkeypatch, date_header
):
    clock = [datetime.now(UTC).replace(microsecond=0) + timedelta(hours=1)]
    monkeypatch.setattr("app.database.index.utcnow", lambda: clock[0])

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0].astimezone(tz) if tz else clock[0].replace(tzinfo=None)

    monkeypatch.setattr("app.providers.vto.provider.datetime", Clock)
    index = IndexRepository(repository.sessions, settings)
    identifier = await index.enqueue(
        "vto", "FULL_CATALOG_RECONCILIATION", {"page": 40, "pending_products": [{"id": 7}]}
    )
    job = await index.claim("first")
    calls = 0

    def transport(request):
        nonlocal calls
        calls += 1
        return httpx.Response(
            429,
            headers={
                "Retry-After": format_datetime(clock[0] + timedelta(seconds=900), usegmt=True)
                if date_header
                else "900"
            },
        )

    provider = VTOProvider(settings, httpx.AsyncClient(transport=httpx.MockTransport(transport)))
    try:
        with pytest.raises(SupplierError) as error:
            await provider.request("https://vto.kz/wp-json/wc/store/products?page=40")
        assert calls == 1 and error.value.evidence["stage"] == "listing"
        record_error(job["metrics"], error.value)
        await index.finish(job, "first", "PENDING", error.value.code)
        # A second adapter call and another repository cannot bypass the deadline.
        with pytest.raises(SupplierError):
            await provider.request("https://vto.kz/wp-json/wc/store/products?page=40")
        assert calls == 1
        restarted = IndexRepository(repository.sessions, settings)
        clock[0] += timedelta(seconds=899)
        assert await restarted.claim("second") is None
        async with repository.sessions() as db:
            row = await db.get(CatalogSyncJob, identifier)
            assert row.checkpoint["page"] == 40 and row.metrics["retry_decision"]["assigned_by"] == "SOURCE"
        clock[0] += timedelta(seconds=1)
        recovered = await restarted.claim("second")
        assert recovered["id"] == identifier and recovered["checkpoint"]["pending_products"] == [{"id": 7}]
    finally:
        await provider.close()


async def test_card_and_local_failures_do_not_create_supplier_transport_circuit(repository, settings):
    index = IndexRepository(repository.sessions, settings)
    for error in [
        "VTO_PARSING_ERROR",
        "VTO_NOT_FOUND",
        "VTO_UNPROCESSED_PRODUCTS",
        "DBAPIError",
        "TIME_BUDGET",
    ]:
        identifier = await index.enqueue("vto", "FULL_CATALOG_RECONCILIATION", {})
        job = await index.claim("worker", job_ids=[identifier])
        if not job:
            async with repository.sessions.begin() as db:
                row = await db.get(CatalogSyncJob, identifier)
                row.available_at = datetime(2000, 1, 1, tzinfo=UTC)
            job = await index.claim("worker", job_ids=[identifier])
        await index.finish(job, "worker", "PENDING", error)
        async with repository.sessions() as db:
            state = await db.get(SupplierSyncState, "vto")
            assert state.failures == 0 and state.circuit_open_until is None
            assert aware(state.retry_after) <= datetime.now(UTC)
            row = await db.get(CatalogSyncJob, identifier)
            if error == "TIME_BUDGET":
                assert (aware(row.available_at) - datetime.now(UTC)).total_seconds() <= 1


def test_access_denial_not_aggressively_retried_and_deadlines_are_maximum_not_sum(settings):
    now = datetime(2026, 11, 7, 9, tzinfo=UTC)
    denied = plan("VTO_ACCESS_DENIED", 0, {"transport_error": {"http_status": 403}}, now, settings)
    assert (denied["next_attempt_at"] - now).total_seconds() >= 900 and denied["circuit_open_until"]
    deadline = now + timedelta(seconds=900)
    value = plan(
        "VTO_HTTP_ERROR",
        0,
        {"transport_error": {"retry_after_until": deadline.isoformat()}, "retry_after_seconds": 900},
        now + timedelta(seconds=5),
        settings,
    )
    assert value["next_attempt_at"] == deadline
