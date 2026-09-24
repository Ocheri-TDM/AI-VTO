from datetime import UTC, datetime

from conftest import make_product
from sqlalchemy import func, select

from app.application.availability import ProcurementAvailabilityService
from app.config import Settings
from app.database.index import IndexRepository
from app.database.models import AvailabilityObservation
from app.domain.models import AvailabilityRecord


def record(state, *, total=None, free=None, eta=None, location=None):
    return AvailabilityRecord(
        state=state, total_quantity=total, free_quantity=free, expected_at=eta,
        location_label=location, parser_version="fixture-v1", source_label="contract fixture",
    )


def test_procurement_availability_tiers_preserve_source_semantics():
    service = ProcurementAvailabilityService()
    assert service.derive([record("ON_HAND", total=500, free=400)], 300).can_fulfill_now
    incoming = service.derive(
        [record("ON_HAND", total=500, free=100), record("INCOMING", free=500)], 300
    )
    assert not incoming.can_fulfill_now
    assert incoming.can_fulfill_with_incoming
    remote = service.derive(
        [record("ON_HAND", total=500, free=0), record("REMOTE_STOCK", free=1000)], 300
    )
    assert not remote.can_fulfill_now
    assert remote.can_fulfill_remote


def test_total_is_not_free_and_null_is_not_zero():
    service = ProcurementAvailabilityService()
    unknown = service.derive([record("ON_HAND", total=500, free=None)], 300)
    assert unknown.available_now is None
    assert not unknown.can_fulfill_now
    zero = service.derive([record("ON_HAND", total=1000, free=0)], 500)
    assert zero.available_now == 0
    assert not zero.can_fulfill_now


def test_incoming_shipments_keep_eta_and_never_become_current_stock():
    eta = datetime(2026, 10, 15, tzinfo=UTC)
    result = ProcurementAvailabilityService().derive(
        [
            record("INCOMING", free=200, eta=eta, location="shipment 1"),
            record("INCOMING", free=300, eta=datetime(2026, 10, 20, tzinfo=UTC), location="shipment 2"),
        ], 300,
    )
    assert result.available_now is None
    assert not result.can_fulfill_now
    assert result.can_fulfill_with_incoming
    assert result.incoming_confirmed == 500
    assert result.earliest_eta == eta


async def test_multiple_records_are_persisted_idempotently(repository, settings: Settings):
    product = make_product(
        availability_records=[
            record("ON_HAND", total=500, free=100, location="central"),
            record("INCOMING", free=500, location="shipment"),
        ]
    )
    index = IndexRepository(repository.sessions, settings)
    await index.ingest([product])
    await index.ingest([product])
    async with repository.sessions() as db:
        count = await db.scalar(select(func.count()).select_from(AvailabilityObservation))
        rows = (await db.scalars(select(AvailabilityObservation))).all()
    assert count == 2
    assert {(row.state, row.free_quantity) for row in rows} == {
        ("ON_HAND", 100), ("INCOMING", 500),
    }
