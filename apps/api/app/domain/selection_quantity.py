"""Freeze only evidence that was applicable when this variant was first selected."""

from app.domain.effective_filters import branch_filter_state, effective_intent
from app.domain.models import utcnow
from app.domain.research import ResearchSession


def selection_quantity(payload=None, offer_id=None):
    result = {"schema_version": 1, "quantity": 1, "rule": "NO_SOURCE", "captured_at": utcnow().isoformat()}
    if not payload:
        return result
    try:
        research = ResearchSession.model_validate(payload)
        intent = effective_intent(research)
        matching = [
            b for b, ids in zip(intent.branches, research.branch_offer_ids, strict=True) if offer_id in ids
        ]
        visible = [b for b in matching if b.id not in research.view.hidden_branch_ids]
        matching = visible or matching
        result.update(
            research_id=research.id,
            research_revision=research.revision,
            view_version=research.view.version,
            branch_ids=[b.id for b in matching],
        )
        bounds = []
        for branch in matching:
            state = branch_filter_state(intent, branch, research.view.filters)["minimum_quantity"]
            minimum = (state["number"] + int(state["exclusive"])) if state["number"] is not None else 1
            maximum = branch.constraints.quantity_max
            if maximum is not None:
                maximum -= int(branch.constraints.quantity_max_exclusive)
            if state["mode"] == "conflict" or maximum is not None and minimum > maximum:
                result["rule"] = "INCOMPATIBLE_CONTEXT"
                return result
            bounds.append((max(1, minimum), maximum))
        if not bounds or len(set(bounds)) != 1:
            result["rule"] = "AMBIGUOUS_CONTEXT"
            return result
        quantity, maximum = bounds[0]
        result.update(
            quantity=quantity, maximum=maximum, rule="SEARCH_CONSTRAINT" if quantity != 1 else "NO_MINIMUM"
        )
    except (ValueError, TypeError, KeyError):
        result["rule"] = "HISTORICAL_CONTEXT_UNAVAILABLE"
    return result
