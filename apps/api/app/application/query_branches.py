"""Branch edits use the existing research history and optimistic version lock."""

from uuid import uuid4

from app.domain.query_branches import branch_label
from app.domain.research import BranchAction, ProductBranch


def latest_removals(research):
    seen = set()
    for entry in reversed(research.history):
        for item in reversed(entry.get("branch_removals", [])):
            if item["branch"]["id"] not in seen:
                seen.add(item["branch"]["id"])
                yield item


def apply_branch_actions(research, actions):
    requery = False
    for value in actions:
        action = BranchAction.model_validate(value) if isinstance(value, dict) else value
        if action.action == "show_all":
            research.view.hidden_branch_ids = []
            continue
        if action.action == "restore":
            removal = next(
                (item for item in latest_removals(research) if item["id"] == action.removal_id),
                None,
            )
            if removal is None:
                raise ValueError("STALE_REMOVAL")
            branch = ProductBranch.model_validate(removal["branch"])
            if not any(b.id == branch.id for b in research.intent.branches):
                research.intent.branches.append(branch)
                research.intent.branches.sort(key=lambda b: b.order)
                if removal["hidden"]:
                    research.view.hidden_branch_ids.append(branch.id)
                for field, values in removal["local_filters"].items():
                    getattr(research.view.filters, field).setdefault(branch.category, values)
            research.history[-1]["restored_removal_id"] = removal["id"]
            requery = True
            continue
        branch = next((b for b in research.intent.branches if b.id == action.branch_id), None)
        if branch is None or branch.category == "*":
            raise ValueError("UNKNOWN_BRANCH")
        if action.action == "hide":
            research.view.hidden_branch_ids = list(
                dict.fromkeys(research.view.hidden_branch_ids + [branch.id])
            )
        elif action.action == "show":
            research.view.hidden_branch_ids = [
                id for id in research.view.hidden_branch_ids if id != branch.id
            ]
        else:
            removal = {
                "id": str(uuid4()),
                "branch": branch.model_dump(mode="json"),
                "label": branch_label(branch),
                "order": [b.id for b in research.intent.branches],
                "hidden": branch.id in research.view.hidden_branch_ids,
                "local_filters": {},
            }
            research.history[-1].setdefault("branch_removals", []).append(removal)
            research.intent.branches = [b for b in research.intent.branches if b.id != branch.id]
            research.view.hidden_branch_ids = [
                id for id in research.view.hidden_branch_ids if id != branch.id
            ]
            if not any(b.category == branch.category for b in research.intent.branches):
                for field in ("branch_brands", "branch_models"):
                    values = getattr(research.view.filters, field).pop(branch.category, None)
                    if values is not None:
                        removal["local_filters"][field] = values
            requery = True
    research.intent.categories = list(
        dict.fromkeys(b.category for b in research.intent.branches if b.category != "*")
    )
    return requery


def removal_notice(research):
    for item in latest_removals(research):
        if not any(b.id == item["branch"]["id"] for b in research.intent.branches):
            return {"id": item["id"], "label": item["label"], "branch_id": item["branch"]["id"]}
    return None
