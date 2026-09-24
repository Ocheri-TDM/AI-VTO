"""Supplier-neutral procurement derivation over source availability records."""
from dataclasses import dataclass
from datetime import datetime

from app.domain.models import AvailabilityRecord


@dataclass(frozen=True)
class ProcurementAvailability:
    available_now: int | None
    incoming_confirmed: int | None
    remote_available: int | None
    earliest_eta: datetime | None
    can_fulfill_now: bool
    can_fulfill_with_incoming: bool
    can_fulfill_remote: bool
    availability_confidence: str


class ProcurementAvailabilityService:
    """Never promotes remote, factory, preorder or incoming stock to ON_HAND."""

    def derive(self, records: list[AvailabilityRecord], quantity: int | None = None):
        current = [r.free_quantity for r in records if r.state == "ON_HAND" and r.free_quantity is not None]
        incoming = [
            r.free_quantity
            for r in records
            if r.state in {"INCOMING", "RECEIVING"} and r.free_quantity is not None
        ]
        remote = [
            r.free_quantity
            for r in records
            if r.state == "REMOTE_STOCK" and r.free_quantity is not None
        ]
        etas = [r.expected_at for r in records if r.expected_at is not None]
        now = sum(current) if current else None
        incoming_total = sum(incoming) if incoming else None
        remote_total = sum(remote) if remote else None
        required = quantity or 1
        return ProcurementAvailability(
            available_now=now, incoming_confirmed=incoming_total, remote_available=remote_total,
            earliest_eta=min(etas) if etas else None,
            can_fulfill_now=now is not None and now >= required,
            can_fulfill_with_incoming=(
                incoming_total is not None and (now or 0) + incoming_total >= required
            ),
            can_fulfill_remote=remote_total is not None and remote_total >= required,
            availability_confidence=(
                "HIGH"
                if records and all(r.confidence == "HIGH" for r in records)
                else "MEDIUM"
                if records
                else "UNKNOWN"
            ),
        )

    def presentation(self, records: list[AvailabilityRecord], quantity: int | None = None):
        """Build the API presentation without reinterpreting supplier evidence."""
        derived = self.derive(records, quantity)

        def view(record):
            return {
                "locationCode": record.location_code,
                "locationLabel": record.location_label,
                "state": record.state,
                "total": record.total_quantity,
                "free": record.free_quantity,
                "reserved": record.reserved_quantity,
                "expectedAt": record.expected_at.isoformat() if record.expected_at else None,
                "leadTimeMinDays": record.lead_time_min_days,
                "leadTimeMaxDays": record.lead_time_max_days,
                "observedAt": record.observed_at.isoformat(),
                "confidence": record.confidence,
            }

        current = [view(r) for r in records if r.state == "ON_HAND"]
        incoming = [view(r) for r in records if r.state in {"INCOMING", "RECEIVING"}]
        remote = [view(r) for r in records if r.state == "REMOTE_STOCK"]
        alternatives = [
            view(r) for r in records
            if r.state in {"FACTORY", "WAITING_SHIPMENT", "PREORDER", "ORDER_ON_DEMAND", "PRODUCTION"}
        ]
        tier = None
        if quantity is not None:
            if derived.can_fulfill_now:
                tier = "AVAILABLE_NOW"
            elif derived.can_fulfill_with_incoming:
                tier = "AVAILABLE_WITH_INCOMING"
            elif derived.can_fulfill_remote or alternatives:
                tier = "ALTERNATIVE_SUPPLY"
            else:
                tier = "UNCONFIRMED"
        return {
            "current": current,
            "incoming": incoming,
            "remote": remote,
            "alternatives": alternatives,
            "availableNow": derived.available_now,
            "incomingConfirmed": derived.incoming_confirmed,
            "remoteAvailable": derived.remote_available,
            "earliestIncomingAt": derived.earliest_eta.isoformat() if derived.earliest_eta else None,
            "procurementTier": tier,
            "confidence": derived.availability_confidence,
        }
