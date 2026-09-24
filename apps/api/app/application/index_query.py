import time

from app.application.availability import ProcurementAvailabilityService
from app.application.research_analysis import facts
from app.domain.taxonomy import CategoryMatch, ProductCategoryMatcher


class IndexQueryService:
    """Strict procurement: stale/unknown stock never establishes availability."""

    def __init__(self, repository):
        self.repository = repository

    async def search(self, intent):
        started = time.perf_counter()
        candidates = await self.repository.candidates(intent)
        products = []
        matcher = ProductCategoryMatcher()
        availability = ProcurementAvailabilityService()
        for p in candidates:
            match = next(
                (
                    c
                    for c in intent.categories
                    if (
                        intent.discovery and p.category == c
                        or matcher.match(p, c, intent.raw_query)
                        in {CategoryMatch.EXACT, CategoryMatch.STRONG_RELATED}
                    )
                ),
                None,
            )
            if not match:
                continue
            if intent.colors and not any(c.normalized_color in intent.colors for c in p.colors):
                continue
            if intent.material and intent.material.casefold() not in facts(p):
                continue
            if intent.attributes and any(
                str(p.metadata.get("attributes", {}).get(k, "")) != v for k, v in intent.attributes.items()
            ):
                continue
            p.metadata["matched_concept"] = match
            p.metadata["category_match"] = matcher.match(p, match, intent.raw_query).value
            if intent.quantity is not None:
                derived = availability.derive(p.availability_records, intent.quantity)
                p.metadata["procurement_availability"] = {
                    "available_now": derived.available_now,
                    "incoming_confirmed": derived.incoming_confirmed,
                    "remote_available": derived.remote_available,
                    "earliest_eta": derived.earliest_eta.isoformat() if derived.earliest_eta else None,
                    "confidence": derived.availability_confidence,
                }
                if p.metadata.get("availability_stale"):
                    p.metadata["procurement_status"] = "NEEDS_REFRESH"
                elif derived.can_fulfill_now:
                    p.metadata["procurement_status"] = "AVAILABLE_NOW"
                elif derived.can_fulfill_with_incoming and intent.availability_mode != "AVAILABLE_NOW_ONLY":
                    p.metadata["procurement_status"] = "AVAILABLE_WITH_INCOMING"
                elif derived.can_fulfill_remote and intent.availability_mode == "WAIT_ALLOWED":
                    p.metadata["procurement_status"] = "REMOTE_OPTION"
                else:
                    continue
            products.append(p)
        if intent.quantity is not None:
            order = {"AVAILABLE_NOW": 0, "AVAILABLE_WITH_INCOMING": 1,
                     "REMOTE_OPTION": 2, "NEEDS_REFRESH": 3}
            products.sort(key=lambda p: order.get(p.metadata.get("procurement_status"), 3))
        return products, {
            "index_candidates": len(candidates),
            "valid_matches": len(products),
            "metadata_gap": bool(
                intent.material and any(not p.material and not p.description for p in candidates)
            ),
            "index_query_ms": round((time.perf_counter() - started) * 1000, 2),
            "browser_invoked": False,
        }
