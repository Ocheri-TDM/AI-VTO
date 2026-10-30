"""VTO traversal through the existing durable queue, lease and checkpoint store."""

import asyncio
import time

from sqlalchemy.exc import DBAPIError

from app.domain.errors import SupplierError


async def sync_slice(index, provider, job, seconds=240):
    checkpoint, metrics = job["checkpoint"], job["metrics"]
    if checkpoint.get("coverage_complete"):
        return "COMPLETED", None
    checkpoint.setdefault("page", 1)
    checkpoint.setdefault("seen_parent_ids", [])
    checkpoint.setdefault("failed_products", {})
    checkpoint.setdefault("pending_products", [])
    if (checkpoint.get("scan_order") != "date-desc"
            or checkpoint.get("per_page") not in (None, provider.settings.vto_page_size)):
        # Some id/asc pages return HTTP 502 on this VTO installation.
        # Retain known IDs and retries while reconciling with verified date/desc.
        checkpoint["previous_page"] = checkpoint["page"]
        checkpoint.update(page=1, pending_products=[], pagination_exhausted=False, scan_order="date-desc")
        checkpoint.pop("start_total", None)
    checkpoint["per_page"] = provider.settings.vto_page_size
    version = getattr(provider, "parser_version", "vto-http-v2")
    if checkpoint.get("normalizer_version") != version:
        # A deployed parser repair retries its failed cards before continuing
        # the long scan, without resetting successful parent IDs or page state.
        retry = [entry["product"] for entry in checkpoint["failed_products"].values()]
        pending = retry + checkpoint["pending_products"]
        checkpoint["pending_products"] = list({row["id"]: row for row in pending}.values())
        for entry in checkpoint["failed_products"].values():
            entry["attempts"] = 0
        checkpoint["normalizer_version"] = version
    deadline = time.monotonic() + seconds
    started_requests = provider.requests
    started_errors = provider.errors
    try:
        while time.monotonic() < deadline:
            if await index.cancelled(job["id"]):
                return "CANCELLED", None
            if not checkpoint["pending_products"]:
                if checkpoint.get("pagination_exhausted"):
                    failed = checkpoint["failed_products"]
                    retry = [value["product"] for value in failed.values() if value["attempts"] < 3]
                    if not retry:
                        if failed:
                            return "FAILED", "VTO_UNPROCESSED_PRODUCTS"
                        # Re-enumerate IDs, without re-fetching details. Equal
                        # counts alone cannot detect one deleted/replaced parent.
                        verify_page = checkpoint.get("verification_page", 1)
                        data, total, pages = await provider.collection(verify_page, provider.settings.vto_page_size)
                        verified = set(checkpoint.get("verification_ids", []))
                        verified.update(row["id"] for row in data)
                        checkpoint["verification_ids"] = sorted(verified)
                        checkpoint["verification_page"] = verify_page + 1
                        checkpoint["end_total"] = total
                        await index.checkpoint(job["id"], checkpoint, metrics)
                        if verify_page < pages:
                            continue
                        checkpoint["coverage_complete"] = (
                            total == len(verified) == checkpoint["start_total"]
                            and verified == set(checkpoint["seen_parent_ids"])
                        )
                        return (("COMPLETED", None) if checkpoint["coverage_complete"]
                                else ("FAILED", "VTO_CATALOG_CHANGED_DURING_SCAN"))
                    checkpoint["pending_products"] = retry
                else:
                    data, total, pages = await provider.collection(checkpoint["page"], provider.settings.vto_page_size)
                    checkpoint.setdefault("start_total", total)
                    checkpoint["reported_total"], checkpoint["reported_pages"] = total, pages
                    seen = set(checkpoint["seen_parent_ids"])
                    checkpoint["pending_products"] = [row for row in data if row["id"] not in seen]
                    checkpoint["pagination_exhausted"] = checkpoint["page"] >= pages
                    checkpoint["page"] += 1
                    metrics["collection_pages"] = metrics.get("collection_pages", 0) + 1
                await index.checkpoint(job["id"], checkpoint, metrics)
            if not checkpoint["pending_products"]:
                continue
            # At most two HTTP requests in flight; no DB transaction during I/O.
            batch = checkpoint["pending_products"][:2]
            responses = await asyncio.gather(*(provider.products(parent) for parent in batch), return_exceptions=True)
            for parent, result in zip(batch, responses, strict=True):
                key = str(parent["id"])
                if isinstance(result, Exception):
                    old = checkpoint["failed_products"].get(key, {})
                    checkpoint["failed_products"][key] = {
                        "product": parent, "attempts": old.get("attempts", 0) + 1,
                        "error": getattr(result, "code", type(result).__name__),
                        "detail": str(result)[:300],
                    }
                    metrics["failed_attempts"] = metrics.get("failed_attempts", 0) + 1
                else:
                    # Atomic upsert before advancing the cursor. Crash replay is safe.
                    try:
                        summary = await index.ingest(result)
                    except DBAPIError as exc:
                        state = str(getattr(exc.orig, "sqlstate", ""))
                        if not state.startswith(("22", "23")):
                            raise
                        old = checkpoint["failed_products"].get(key, {})
                        checkpoint["failed_products"][key] = {
                            "product": parent, "attempts": old.get("attempts", 0) + 1,
                            "error": "PERSISTENCE_DATA_ERROR", "detail": state,
                        }
                        metrics["failed_attempts"] = metrics.get("failed_attempts", 0) + 1
                        checkpoint["pending_products"].pop(0)
                        await index.checkpoint(job["id"], checkpoint, metrics)
                        continue
                    for name, value in summary.items():
                        metrics[name] = metrics.get(name, 0) + value
                    checkpoint["failed_products"].pop(key, None)
                    if parent["id"] not in checkpoint["seen_parent_ids"]:
                        checkpoint["seen_parent_ids"].append(parent["id"])
                        metrics["parents"] = metrics.get("parents", 0) + 1
                        name = "simple_products" if parent["type"] == "simple" else "variable_parents"
                        metrics[name] = metrics.get(name, 0) + 1
                        metrics["offers"] = metrics.get("offers", 0) + len(result)
                        metrics["non_sellable_parents"] = metrics.get("non_sellable_parents", 0) + (not result)
                        metrics["variants"] = metrics.get("variants", 0) + sum(p.metadata.get("vto_variation_id") is not None for p in result)
                        metrics["known_quantity"] = metrics.get("known_quantity", 0) + sum(p.stock_quantity is not None for p in result)
                        metrics["zero_quantity"] = metrics.get("zero_quantity", 0) + sum(p.stock_quantity == 0 for p in result)
                        metrics["unknown_quantity"] = metrics.get("unknown_quantity", 0) + sum(p.stock_quantity is None for p in result)
                checkpoint["pending_products"].pop(0)
                await index.checkpoint(job["id"], checkpoint, metrics)
        return "PENDING", "TIME_BUDGET"
    finally:
        metrics["http_requests"] = metrics.get("http_requests", 0) + provider.requests - started_requests
        metrics["http_errors"] = metrics.get("http_errors", 0) + provider.errors - started_errors
        await index.checkpoint(job["id"], checkpoint, metrics)


async def run_vto_job(worker, job):
    started = time.monotonic()
    status, error = "PENDING", None
    try:
        async with asyncio.timeout(worker.settings.research_job_timeout_seconds - 5):
            status, error = await sync_slice(worker.repository, worker.providers["vto"], job,
                                             worker.settings.research_job_timeout_seconds - 10)
    except TimeoutError:
        error = "TIME_BUDGET"
    except asyncio.CancelledError:
        raise
    except SupplierError as exc:
        error = exc.code
        job["metrics"]["last_error"] = str(exc)[:500]
    except Exception as exc:
        error = type(exc).__name__
        job["metrics"]["last_error"] = str(getattr(exc, "orig", exc))[:600]
    finally:
        job["metrics"]["duration_ms"] = job["metrics"].get("duration_ms", 0) + round((time.monotonic() - started) * 1000)
        await worker.repository.checkpoint(job["id"], job["checkpoint"], job["metrics"])
        await worker.repository.finish(job, worker.owner, status, error)
