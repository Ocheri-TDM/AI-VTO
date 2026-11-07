"""Narrow, idempotent repair of proved old internal 15/30-minute waits."""

import re
from datetime import timedelta

from sqlalchemy import select

from app.database.models import CatalogSyncJob, SupplierSyncState
from app.domain.offer_lifecycle import aware


async def repair_legacy_wait(db, now):
    state = await db.scalar(
        select(SupplierSyncState)
        .where(
            SupplierSyncState.supplier == "vto",
            SupplierSyncState.lease_until < now,
            SupplierSyncState.error == "VTO_HTTP_ERROR",
            SupplierSyncState.failures.in_((1, 2)),
        )
        .with_for_update(skip_locked=True)
    )
    if not state or state.circuit_open_until and aware(state.circuit_open_until) > now:
        return
    job = await db.scalar(
        select(CatalogSyncJob)
        .where(
            CatalogSyncJob.supplier == "vto",
            CatalogSyncJob.status == "PENDING",
            CatalogSyncJob.error == "VTO_HTTP_ERROR",
        )
        .order_by(CatalogSyncJob.updated_at.desc())
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if not job:
        return
    metrics = dict(job.metrics or {})
    if (
        metrics.get("retry_policy_version")
        or metrics.get("retry_after_seconds")
        or metrics.get("transport_error")
    ):
        return
    if not re.search(r"Server error '(502|503|504)\b", metrics.get("last_error", "")):
        return
    # Older clients retained long Retry-After explicitly. Unrecorded short
    # headers were <=60s; the anchored 90/120s floor also preserves those.
    deadline = max(now, aware(job.updated_at) + timedelta(seconds=90 if state.failures == 1 else 120))
    if aware(state.retry_after) <= deadline:
        return
    metrics["legacy_wait_repair"] = {
        "old_retry_after": aware(state.retry_after).isoformat(),
        "new_retry_after": deadline.isoformat(),
        "reason": "PROVED_INTERNAL_15_30_MINUTE_BACKOFF",
    }
    state.retry_after = deadline
    job.available_at = max(aware(job.available_at), deadline)
    job.metrics = metrics
