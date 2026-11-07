"""One durable retry owner for VTO; source deadlines are absolute lower bounds."""

import random
from datetime import timedelta

from app.domain.offer_lifecycle import aware

CARD_ERRORS = {
    "VTO_LISTING_PAGE_DEFERRED",
    "VTO_UNPROCESSED_PAGES",
    "VTO_NOT_FOUND",
    "VTO_PARSING_ERROR",
    "VTO_PARENT_FILTER_ERROR",
    "VTO_VARIATION_INCOMPLETE",
    "VTO_UNPROCESSED_PRODUCTS",
    "VTO_PARTIAL_REFRESH",
    "VTO_CATALOG_CHANGED_DURING_SCAN",
    "PERSISTENCE_DATA_ERROR",
}


def record_error(metrics, exc):
    metrics["last_error"] = str(exc)[:500]
    metrics["retry_after_seconds"] = exc.retry_after_seconds
    metrics["transport_error"] = dict(exc.evidence)
    counts = metrics.setdefault("errors_by_class", {})
    name = exc.evidence.get("class", exc.code)
    counts[name] = counts.get(name, 0) + 1


def plan(error, failures, metrics, now, settings):
    transport = error in {
        "VTO_HTTP_ERROR",
        "VTO_RATE_LIMITED",
        "VTO_ACCESS_DENIED",
        "SUPPLIER_HTTP_ERROR",
        "SUPPLIER_CAPTCHA_REQUIRED",
    }
    consecutive = failures + 1 if transport else failures if error and error != "TIME_BUDGET" else 0
    source = metrics.get("transport_error", {}) if transport else {}
    restricted = error in {"VTO_ACCESS_DENIED", "SUPPLIER_CAPTCHA_REQUIRED"} or source.get("http_status") in (
        401,
        403,
    )
    ceiling = settings.supplier_failure_backoff_max_hours * 3600
    if transport:
        if restricted:
            delay = max(
                3600 if error == "SUPPLIER_CAPTCHA_REQUIRED" else 900,
                settings.supplier_failure_backoff_minutes * 60,
            )
            reason = "ACCESS_RESTRICTED"
        elif consecutive == 1:
            delay = random.uniform(60, 90)
            reason = "TRANSIENT_FIRST_RETRY"
        elif consecutive == 2:
            delay = random.uniform(90, 120)
            reason = "TRANSIENT_SECOND_RETRY"
        else:
            base = min(ceiling, 180 * 2 ** min(consecutive - 3, 16))
            delay = min(ceiling, random.uniform(base, base * 1.25))
            reason = "PERSISTENT_SOURCE_FAILURE"
    else:
        delay = 1 if not error or error == "TIME_BUDGET" else 60 if error in CARD_ERRORS else 120
        reason = (
            "CHECKPOINT_CONTINUATION"
            if delay == 1
            else "CARD_REVIEW"
            if error in CARD_ERRORS
            else "LOCAL_OR_CONTRACT_ERROR"
        )
    next_at = now + timedelta(seconds=delay)
    if transport:
        from datetime import datetime

        deadline = source.get("retry_after_until")
        deadline = (
            aware(datetime.fromisoformat(deadline))
            if deadline
            else now + timedelta(seconds=metrics.get("retry_after_seconds", 0))
        )
        if deadline > next_at:
            next_at, reason = deadline, "SUPPLIER_RETRY_AFTER"
    circuit = (
        next_at
        if transport and (restricted or consecutive >= settings.supplier_circuit_failure_threshold)
        else None
    )
    return {
        "next_attempt_at": next_at,
        "reason": reason,
        "assigned_by": "SOURCE" if reason == "SUPPLIER_RETRY_AFTER" else "LOCAL_POLICY",
        "failures": consecutive,
        "circuit_open_until": circuit,
        "transport": transport,
    }


def diagnostics(job, state, now):
    metrics = job.metrics or {}
    decision = metrics.get("retry_decision", {})
    due = max(
        [aware(job.available_at)]
        + ([aware(state.retry_after)] if state else [])
        + ([aware(state.circuit_open_until)] if state and state.circuit_open_until else [])
    )
    working = bool(state and state.lease_owner and aware(state.lease_until) > now and job.status == "RUNNING")
    samples = metrics.get("progress_samples", [])
    window = None
    if len(samples) > 1:
        from datetime import datetime

        start, end = samples[0], samples[-1]
        seconds = (datetime.fromisoformat(end["at"]) - datetime.fromisoformat(start["at"])).total_seconds()
        delta = end["offers"] - start["offers"]
        window = {
            "seconds": seconds,
            "new_unique_offers": delta,
            "new_families": end["families"] - start["families"],
            "offers_per_hour": round(delta * 3600 / seconds, 2) if seconds > 0 else None,
            "http_calls_per_new_offer": round((end["http_requests"] - start["http_requests"]) / delta, 3)
            if delta > 0
            else None,
        }
    return {
        "state": "WORKING"
        if working
        else "COMPLETED"
        if job.status == "COMPLETED"
        else "PARTIAL_REQUIRES_REVIEW"
        if job.status == "FAILED"
        else "WAITING",
        "reason": decision.get("reason", job.error),
        "assigned_by": decision.get("assigned_by", "LEGACY_POLICY"),
        "next_attempt_at": due.isoformat() if job.status == "PENDING" else None,
        "last_successful_record_at": metrics.get("last_successful_record_at"),
        "transport_error": metrics.get("transport_error"),
        "failures": state.failures if state else 0,
        "active_lease": working,
        "lease_until": state.lease_until.isoformat() if state else None,
        "circuit_open_until": state.circuit_open_until.isoformat()
        if state and state.circuit_open_until
        else None,
        "scheduler_poll_seconds": 1,
        "start_lag_ms": metrics.get("start_lag_ms"),
        "working_seconds": round(metrics.get("duration_ms", 0) / 1000, 3),
        "waiting_seconds": metrics.get("waiting_seconds"),
        "measured_window": window,
    }
