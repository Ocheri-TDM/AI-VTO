"""VTO traversal through the existing durable queue, lease and checkpoint store."""

import asyncio
import copy
import time

from sqlalchemy.exc import DBAPIError

from app.domain.errors import SupplierError


async def completed_products(provider, batch, checkpoint, save, owned_tasks, index, metrics):
    """Commit the fast card while its slow neighbour is still fetching.

    Cancellation only cancels this slice's tasks. Variant/page progress is saved
    through the same lease-owned checkpoint before another request starts.
    """
    async def fetch(parent):
        try:
            if hasattr(provider, "resume_products"):
                progress = checkpoint.setdefault("card_progress", {}).setdefault(str(parent["id"]), {})
                async def core_ready(products):
                    try:
                        await index.ingest(products)
                    except DBAPIError as exc:
                        state = str(getattr(exc.orig, "sqlstate", ""))
                        if not state.startswith(("22", "23")):
                            raise
                        raise SupplierError("PERSISTENCE_DATA_ERROR", state) from exc
                    metrics["core_live_offers"] = metrics.get("core_live_offers", 0) + len(products)
                result = await provider.resume_products(parent, progress, save, core_ready)
            else:
                result = await provider.products(parent)
            return parent, result
        except Exception as exc:
            return parent, exc
    tasks = {asyncio.create_task(fetch(parent)) for parent in batch}
    owned_tasks.update(tasks)
    try:
        while tasks:
            done, tasks = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            owned_tasks.difference_update(done)
            for task in done:
                yield task.result()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def collection_page(provider, checkpoint, page, save, metrics=None):
    """Defer a repeatedly failing page durably; never call that a full scan."""
    failures = checkpoint.setdefault("failed_listing_pages", {})
    try:
        result = await provider.collection(page, checkpoint["per_page"])
    except SupplierError as exc:
        if exc.code == "VTO_HTTP_ERROR" and exc.evidence.get("http_status") in {500, 502, 503, 504} and not exc.retry_after_seconds:
            old = failures.get(str(page), {})
            failures[str(page)] = {"page": page, "per_page": checkpoint["per_page"], "attempts": old.get("attempts", 0)+1, "http_status": exc.evidence["http_status"]}
            if failures[str(page)]["attempts"] >= 2 and page == checkpoint["page"] and not checkpoint.get("pagination_exhausted"):
                checkpoint["page"] = page+1
                await save()
                # One lease-governed probe of another page after a short pause.
                # A failure there again escalates the shared transport counter.
                if (metrics or {}).get("retry_decision",{}).get("failures",0) <= 2:
                    raise SupplierError("VTO_LISTING_PAGE_DEFERRED", "Repeated listing 5xx retained for later retry", retryable=True, evidence=exc.evidence) from exc
            await save()
        raise
    failures.pop(str(page), None)
    return result


async def smaller_listing_probe(provider, checkpoint, metrics, save):
    """One next-lease probe with a smaller response, only after repeated 5xx.

    It replaces that attempt's normal collection request, never bypasses the
    supplier wait, and commits cursor conversion only after a validated response.
    """
    evidence = metrics.get("transport_error", {})
    size = checkpoint["per_page"]
    target = next((n for n in (50,20) if n<size and size%n==0),None)
    if (checkpoint.get("listing_probe_attempted") or checkpoint.get("validated_page_size") or checkpoint.get("pagination_exhausted") or checkpoint["pending_products"] or not target
            or evidence.get("stage") != "listing" or evidence.get("http_status") not in {500,502,503,504}
            or metrics.get("retry_after_seconds",0)>0 or metrics.get("retry_decision",{}).get("failures",0)<3):
        return
    from urllib.parse import parse_qs, urlsplit
    previous_size = parse_qs(urlsplit(evidence.get("resource", "")).query).get("per_page", [])
    checkpoint["listing_probe_attempted"] = True
    await save()
    if previous_size and int(previous_size[0]) < size:
        return  # A prior failed smaller probe is already evidence; do not repeat it.
    offset = (checkpoint["page"]-1)*size
    page = offset//target+1
    data,total,pages = await provider.collection(page,target)
    if len({row["id"] for row in data}) != len(data):
        raise SupplierError("VTO_INCOMPLETE_RESPONSE","Duplicate IDs in collection probe")
    converted = {}
    for row in checkpoint.get("failed_listing_pages",{}).values():
        start = (row["page"]-1)*row["per_page"]
        for p in range(start//target+1,(start+row["per_page"]-1)//target+2):
            converted[str(p)] = row | {"page":p,"per_page":target}
    converted.pop(str(page),None)
    checkpoint.update(per_page=target,validated_page_size=target,page=page+1,
        failed_listing_pages=converted,pagination_exhausted=page>=pages,
        reported_total=total,reported_pages=pages,
        pending_products=[r for r in data if r["id"] not in set(checkpoint["seen_parent_ids"])])
    from app.domain.models import utcnow
    metrics["listing_size_probe"]={"original_per_page":size,"validated_per_page":target,"offset":offset,"validated_at":utcnow().isoformat(),"reason":"REPEATED_LISTING_5XX","http_calls":1}
    metrics["collection_pages"] = metrics.get("collection_pages",0)+1
    await save()


async def sync_slice(index, provider, job, seconds=240):
    checkpoint, metrics = job["checkpoint"], job["metrics"]
    checkpoint_lock = asyncio.Lock()
    owned_tasks = set()
    async def save():
        async with checkpoint_lock:
            await index.checkpoint(job["id"], copy.deepcopy(checkpoint), dict(metrics))
    if checkpoint.get("coverage_complete"):
        return "COMPLETED", None
    checkpoint.setdefault("page", 1)
    checkpoint.setdefault("seen_parent_ids", [])
    checkpoint.setdefault("failed_products", {})
    checkpoint.setdefault("pending_products", [])
    page_size = checkpoint.get("validated_page_size") or provider.settings.vto_page_size
    if (checkpoint.get("scan_order") != "date-desc"
            or checkpoint.get("per_page") not in (None, page_size)):
        # Some id/asc pages return HTTP 502 on this VTO installation.
        # Retain known IDs and retries while reconciling with verified date/desc.
        checkpoint["previous_page"] = checkpoint["page"]
        checkpoint.update(page=1, pending_products=[], pagination_exhausted=False, scan_order="date-desc")
        checkpoint.pop("start_total", None)
    checkpoint["per_page"] = page_size
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
    started_timings = dict(getattr(provider, "timings", {}))
    started_kinds = dict(getattr(provider, "request_kinds", {}))
    checkpoint.setdefault("stages", {})
    try:
        await smaller_listing_probe(provider,checkpoint,metrics,save)
        while time.monotonic() < deadline:
            if await index.cancelled(job["id"]):
                return "CANCELLED", None
            if not checkpoint["pending_products"]:
                if checkpoint.get("pagination_exhausted"):
                    pages_to_retry = [r for r in checkpoint.get("failed_listing_pages", {}).values() if r["attempts"] < 3]
                    if pages_to_retry:
                        page = min(pages_to_retry, key=lambda r: r["page"])["page"]
                        data, _total, _pages = await collection_page(provider, checkpoint, page, save, metrics)
                        seen = set(checkpoint["seen_parent_ids"])
                        checkpoint["pending_products"] = [row for row in data if row["id"] not in seen]
                        await save()
                        if not checkpoint["pending_products"]:
                            continue
                    elif checkpoint.get("failed_listing_pages"):
                        return "FAILED", "VTO_UNPROCESSED_PAGES"
                    if checkpoint["pending_products"]:
                        continue
                    failed = checkpoint["failed_products"]
                    retry = [value["product"] for value in failed.values() if value["attempts"] < 3]
                    if not retry:
                        if failed:
                            return "FAILED", "VTO_UNPROCESSED_PRODUCTS"
                        # Re-enumerate IDs, without re-fetching details. Equal
                        # counts alone cannot detect one deleted/replaced parent.
                        verify_page = checkpoint.get("verification_page", 1)
                        data, total, pages = await provider.collection(verify_page, checkpoint["per_page"])
                        verified = set(checkpoint.get("verification_ids", []))
                        verified.update(row["id"] for row in data)
                        checkpoint["verification_ids"] = sorted(verified)
                        checkpoint["verification_page"] = verify_page + 1
                        checkpoint["end_total"] = total
                        await save()
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
                    page = checkpoint["page"]
                    data, total, pages = await collection_page(provider, checkpoint, page, save, metrics)
                    checkpoint.setdefault("start_total", total)
                    checkpoint["reported_total"], checkpoint["reported_pages"] = total, pages
                    seen = set(checkpoint["seen_parent_ids"])
                    checkpoint["pending_products"] = [row for row in data if row["id"] not in seen]
                    checkpoint["pagination_exhausted"] = checkpoint["page"] >= pages
                    checkpoint["page"] += 1
                    metrics["collection_pages"] = metrics.get("collection_pages", 0) + 1
                    checkpoint["stages"]["discovered_parents"] = len(seen) + len(checkpoint["pending_products"])
                await save()
            if not checkpoint["pending_products"]:
                continue
            # At most two HTTP requests in flight; no DB transaction during I/O.
            batch = checkpoint["pending_products"][:2]
            async for parent, result in completed_products(provider, batch, checkpoint, save, owned_tasks, index, metrics):
                key = str(parent["id"])
                if isinstance(result, Exception):
                    if isinstance(result, SupplierError) and result.code not in {
                        "VTO_NOT_FOUND", "VTO_PARSING_ERROR", "VTO_PARENT_FILTER_ERROR", "VTO_VARIATION_INCOMPLETE", "PERSISTENCE_DATA_ERROR"
                    }:
                        # Rate limits/access/network failures affect the source,
                        # never burn the three-attempt budget of unrelated cards.
                        metrics["retry_after_seconds"] = result.retry_after_seconds
                        raise result
                    if not isinstance(result, SupplierError):
                        raise result
                    old = checkpoint["failed_products"].get(key, {})
                    core_ids = old.get("core_offer_ids", [])
                    if result.code != "PERSISTENCE_DATA_ERROR" and not old.get("core_attempted") and hasattr(provider, "core_products"):
                        try:
                            core = await provider.core_products(parent)
                            await index.ingest(core)
                            core_ids = [product.id for product in core]
                            metrics["core_fallback_offers"] = metrics.get("core_fallback_offers", 0) + len(core)
                        except SupplierError as exc:
                            if exc.retryable:
                                raise
                        except (ValueError, KeyError, TypeError):
                            pass
                    checkpoint["failed_products"][key] = {
                        "product": parent, "attempts": old.get("attempts", 0) + 1,
                        "error": getattr(result, "code", type(result).__name__),
                        "detail": str(result)[:300],
                        "stage": "DETAIL_ENRICHMENT", "parser_version": version,
                        "core_attempted": True, "core_offer_ids": core_ids,
                    }
                    metrics["failed_attempts"] = metrics.get("failed_attempts", 0) + 1
                else:
                    # Atomic upsert before advancing the cursor. Crash replay is safe.
                    try:
                        db_started = time.monotonic()
                        summary = await index.ingest(result)
                        metrics["db_ms"] = metrics.get("db_ms", 0) + round((time.monotonic() - db_started) * 1000, 2)
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
                        checkpoint["pending_products"].remove(parent)
                        await save()
                        continue
                    for name, value in summary.items():
                        metrics[name] = metrics.get(name, 0) + value
                    from app.domain.models import utcnow
                    metrics["last_successful_record_at"] = utcnow().isoformat()
                    checkpoint["failed_products"].pop(key, None)
                    checkpoint.get("card_progress", {}).pop(key, None)
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
                        checkpoint["stages"].update(core_committed_parents=len(checkpoint["seen_parent_ids"]),
                                                    detail_enriched_parents=len(checkpoint["seen_parent_ids"]),
                                                    stock_known_offers=metrics.get("known_quantity", 0),
                                                    stock_unknown_offers=metrics.get("unknown_quantity", 0))
                checkpoint["pending_products"].remove(parent)
                await save()
        return "PENDING", "TIME_BUDGET"
    finally:
        for task in owned_tasks:
            task.cancel()
        await asyncio.gather(*owned_tasks, return_exceptions=True)
        metrics["http_requests"] = metrics.get("http_requests", 0) + provider.requests - started_requests
        metrics["http_errors"] = metrics.get("http_errors", 0) + provider.errors - started_errors
        for name, value in getattr(provider, "timings", {}).items():
            metrics[name] = round(metrics.get(name, 0) + value - started_timings.get(name, 0), 2)
        kinds = metrics.setdefault("requests_by_kind", {})
        for name, value in getattr(provider, "request_kinds", {}).items():
            kinds[name] = kinds.get(name, 0) + value - started_kinds.get(name, 0)
        await save()


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
        from app.domain.vto_retry import record_error
        record_error(job["metrics"], exc)
    except Exception as exc:
        error = type(exc).__name__
        job["metrics"]["last_error"] = str(getattr(exc, "orig", exc))[:600]
    finally:
        job["metrics"]["duration_ms"] = job["metrics"].get("duration_ms", 0) + round((time.monotonic() - started) * 1000)
        await worker.repository.checkpoint(job["id"], job["checkpoint"], job["metrics"])
        await worker.repository.finish(job, worker.owner, status, error)
