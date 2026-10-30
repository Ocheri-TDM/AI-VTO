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


@dataclass(frozen=True)
class ProcurementEvaluation:
    required_quantity: int
    result: str
    tier: str | None
    available_quantity: int | None
    evidence: list[dict]
    reason_code: str


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
            r.free_quantity for r in records if r.state == "REMOTE_STOCK" and r.free_quantity is not None
        ]
        etas = [r.expected_at for r in records if r.expected_at is not None]
        now = sum(current) if current else None
        incoming_total = sum(incoming) if incoming else None
        remote_total = sum(remote) if remote else None
        required = quantity or 1
        return ProcurementAvailability(
            available_now=now,
            incoming_confirmed=incoming_total,
            remote_available=remote_total,
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
            view(r)
            for r in records
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

    def evaluate(self, records: list[AvailabilityRecord], quantity: int, *, stale: bool = False):
        evidence = [
            {
                "state": record.state,
                "location": record.location_label or record.location_code,
                "free_quantity": record.free_quantity,
                "total_quantity": record.total_quantity,
                "reserved_quantity": record.reserved_quantity,
                "expected_at": record.expected_at.isoformat() if record.expected_at else None,
                "observed_at": record.observed_at.isoformat(),
                "parser_version": record.parser_version,
            }
            for record in records
        ]
        derived = self.derive(records, quantity)
        if derived.can_fulfill_now:
            if stale:
                return ProcurementEvaluation(
                    quantity, "PASS", "LAST_KNOWN", derived.available_now,
                    evidence, "LAST_KNOWN_FREE_SUFFICIENT",
                )
            return ProcurementEvaluation(
                quantity, "PASS", "CURRENT_FREE", derived.available_now, evidence, "CURRENT_FREE_SUFFICIENT"
            )
        if derived.can_fulfill_with_incoming:
            available = (derived.available_now or 0) + (derived.incoming_confirmed or 0)
            return ProcurementEvaluation(
                quantity,
                "PASS",
                "CURRENT_PLUS_INCOMING",
                available,
                evidence,
                "CONFIRMED_INCOMING_SUFFICIENT",
            )
        if derived.can_fulfill_remote:
            return ProcurementEvaluation(
                quantity, "PASS", "REMOTE", derived.remote_available, evidence, "REMOTE_STOCK_SUFFICIENT"
            )
        alternatives = [
            record.free_quantity
            for record in records
            if record.state in {"FACTORY", "PREORDER", "PRODUCTION", "ORDER_ON_DEMAND", "WAITING_SHIPMENT"}
            and record.free_quantity is not None
        ]
        if alternatives and max(alternatives) >= quantity:
            return ProcurementEvaluation(
                quantity, "PASS", "ALTERNATIVE", max(alternatives), evidence, "ALTERNATIVE_SUPPLY_SUFFICIENT"
            )
        current = derived.available_now
        if current is not None:
            reason = (
                "RESERVED_ONLY"
                if current == 0 and any((r.reserved_quantity or 0) > 0 for r in records)
                else "FREE_QUANTITY_INSUFFICIENT"
            )
            return ProcurementEvaluation(quantity, "REJECT", None, current, evidence, reason)
        if records:
            return ProcurementEvaluation(quantity, "REJECT", None, None, evidence, "UNKNOWN_QUANTITY")
        return ProcurementEvaluation(quantity, "REJECT", None, None, evidence, "NO_PROCUREMENT_EVIDENCE")

    def evaluate_on_hand(
        self,
        records: list[AvailabilityRecord],
        quantity: int,
        *,
        stale: bool = False,
        fresh_only: bool = False,
    ) -> ProcurementEvaluation:
        """Evaluate numeric ON_HAND free stock without promoting other supply tiers."""
        evaluation = self.evaluate(records, quantity, stale=stale)
        if evaluation.tier == "CURRENT_FREE":
            return evaluation
        if evaluation.tier == "LAST_KNOWN":
            if not fresh_only:
                return evaluation
            return ProcurementEvaluation(
                quantity, "REJECT", None, evaluation.available_quantity,
                evaluation.evidence, "STALE_OBSERVATION",
            )
        if evaluation.reason_code in {
            "NO_PROCUREMENT_EVIDENCE", "UNKNOWN_QUANTITY",
            "FREE_QUANTITY_INSUFFICIENT", "RESERVED_ONLY",
        }:
            return evaluation
        states = {record.state for record in records}
        reason = (
            "INCOMING_ONLY" if states & {"INCOMING", "RECEIVING"}
            else "REMOTE_ONLY" if "REMOTE_STOCK" in states
            else "PREORDER_ONLY" if "PREORDER" in states
            else "FACTORY_ONLY" if states & {"FACTORY", "PRODUCTION"}
            else "ORDER_ON_DEMAND" if states & {"ORDER_ON_DEMAND", "WAITING_SHIPMENT"}
            else evaluation.reason_code
        )
        return ProcurementEvaluation(
            quantity, "REJECT", None, evaluation.available_quantity,
            evaluation.evidence, reason,
        )
