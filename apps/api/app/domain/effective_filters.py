"""Pure saved-view overrides and all-branch projection, independent of results.

The original intent is the reset baseline. Edits are explicit set/clear/inherit
operations in the existing revisioned view, never copied additional AND filters.
"""

from app.domain.catalog_identity import brand_identity
from app.domain.models import ColorGroup
from app.domain.query_branches import branch_label
from app.domain.research import EffectiveFilterEdit

FIELDS = ("brands", "colors", "materials", "sizes", "min_price", "max_price", "minimum_quantity")


def apply_filter_edits(research, edits):
    ids = {b.id for b in research.intent.branches}
    saved = list(research.view.effective_overrides)
    for raw in edits:
        edit = raw if isinstance(raw, EffectiveFilterEdit) else EffectiveFilterEdit.model_validate(raw)
        if edit.branch_id and edit.branch_id not in ids:
            raise ValueError("UNKNOWN_BRANCH")
        if (
            edit.branch_id
            and edit.action != "inherit"
            and getattr(research.view.filters, edit.field) not in ([], None)
        ):
            raise ValueError("GLOBAL_ADDITIONAL_FILTER")
        saved = [
            e
            for e in saved
            if not (e.field == edit.field and (not edit.branch_id or e.branch_id == edit.branch_id))
        ]
        if edit.action != "inherit":
            saved.append(edit)
        # A direct effective edit replaces additional narrowing of this field.
        # All other additional filters retain their existing contract.
        default = None if edit.field in ("min_price", "max_price", "minimum_quantity") else []
        if not edit.branch_id:
            setattr(research.view.filters, edit.field, default)
            research.view.filters.origins.pop(edit.field, None)
        if edit.field == "brands":
            if edit.branch_id:
                category = next(b.category for b in research.intent.branches if b.id == edit.branch_id)
                research.view.filters.branch_brands.pop(category, None)
            else:
                research.view.filters.branch_brands = {}
    research.view.effective_overrides = saved


def effective_intent(research):
    edits = research.view.effective_overrides
    intent = research.intent.model_copy(deep=True)
    for branch in intent.branches:
        for outer, default, inner in [
            ("colors", list(intent.colors), "colors"),
            ("quantity", intent.quantity, "quantity_min"),
            ("materials", [intent.material] if intent.material else [], "materials"),
            ("min_price", intent.budget.min if intent.budget else None, "price_min"),
            ("max_price", intent.budget.max if intent.budget else None, "price_max"),
        ]:
            current = getattr(branch, outer)
            if (current is None or current == []) and not branch.origins.get(outer):
                setattr(branch, outer, default)
                setattr(branch.constraints, inner, default)
    if any(e.field == "brands" for e in edits):
        # Distribute a global positive brand before local replacements. Keep
        # genuine empty intersections unsatisfiable, rather than making them ALL.
        global_brands = {brand_identity(v) for v in intent.brands}
        for branch in intent.branches:
            if global_brands:
                if branch.brands:
                    common = [v for v in branch.brands if brand_identity(v) in global_brands]
                    if not common:
                        branch.origins["effective_brand_conflict"] = "true"
                    branch.brands = common
                else:
                    branch.brands = list(intent.brands)
        intent.brands = []
    for edit in edits:
        for branch in intent.branches:
            if edit.branch_id and edit.branch_id != branch.id:
                continue
            values = list(dict.fromkeys(edit.values or [])) if edit.action == "set" else []
            number = edit.number if edit.action == "set" else None
            if edit.field == "sizes":
                branch.constraints.sizes = [v.upper() for v in values]
            elif edit.field == "minimum_quantity":
                branch.quantity = int(number) if number is not None else None
                branch.constraints.quantity_min = branch.quantity
                branch.constraints.quantity_min_exclusive = False
            elif edit.field in ("min_price", "max_price"):
                setattr(branch, edit.field, number)
                inner = "price_min" if edit.field == "min_price" else "price_max"
                setattr(branch.constraints, inner, number)
                setattr(branch.constraints, inner + "_exclusive", False)
            else:
                setattr(branch, edit.field, values)
                if edit.field in ("colors", "materials"):
                    setattr(branch.constraints, edit.field, values)
                if edit.field == "colors":
                    branch.constraints.allow_near_colors = False
                if edit.field == "brands":
                    branch.origins.pop("effective_brand_conflict", None)
            branch.origins["effective_" + edit.field] = edit.action
            branch.origins["quantity" if edit.field == "minimum_quantity" else edit.field] = (
                "manual: " + edit.action
            )
    for field, outer, empty in [("colors", "colors", []), ("minimum_quantity", "quantity", None)]:
        if any(e.field == field for e in edits):
            values = [getattr(b, outer) for b in intent.branches]
            common = values[0] if values and all(v == values[0] for v in values) else empty
            setattr(intent, outer, [ColorGroup(v) for v in common] if field == "colors" else common)
    if any(e.field == "materials" for e in edits):
        values = [b.materials for b in intent.branches]
        intent.material = (
            values[0][0] if values and len(values[0]) == 1 and all(v == values[0] for v in values) else None
        )
    if any(e.field in ("min_price", "max_price") for e in edits):
        # Complete effective price bounds already live on every branch. Drop
        # the old global shorthand; it cannot encode mixed/fractional/conflicting
        # bounds and must not resurrect a cleared bound during a chat refinement.
        intent.budget = None
    return intent


def _state(values=None, number=None, *, complex=False, conflict=False, exclusive=False):
    values = sorted(set(values or []))
    return {
        "mode": "conflict"
        if conflict
        else "complex"
        if complex
        else "common"
        if values or number is not None
        else "none",
        "values": values,
        "number": number,
        "exclusive": exclusive,
    }


def _intersection(groups):
    groups = [set(g) for g in groups if g]
    return (set.intersection(*groups), not bool(set.intersection(*groups))) if groups else (set(), False)


def branch_filter_state(intent, branch, filters):
    result = {}
    for field in ("colors", "brands", "sizes"):
        source = branch.constraints.sizes if field == "sizes" else getattr(branch, field)
        groups = [source, getattr(filters, field)]
        if field == "brands":
            groups.extend([intent.brands, filters.branch_brands.get(branch.category, [])])
            groups = [[brand_identity(v) for v in group] for group in groups]
        if field == "sizes":
            groups = [[v.upper() for v in group] for group in groups]
        values, conflict = _intersection(groups)
        result[field] = _state(
            values,
            conflict=conflict or field == "brands" and bool(branch.origins.get("effective_brand_conflict")),
        )
        if result[field]["mode"] == "conflict":
            result[field]["requirements"] = [sorted(set(g)) for g in groups if g]
    source_materials, extra_materials = branch.materials, filters.materials
    result["materials"] = _state(
        extra_materials or source_materials,
        complex=(
            len(source_materials) > 1
            or bool(source_materials and extra_materials and set(source_materials) != set(extra_materials))
        ),
    )
    if result["materials"]["mode"] == "complex":
        result["materials"]["requirements"] = [[v] for v in source_materials] + (
            [extra_materials] if extra_materials else []
        )
    for field, source, extra, exclusive in [
        (
            "minimum_quantity",
            branch.quantity,
            filters.minimum_quantity,
            branch.constraints.quantity_min_exclusive,
        ),
        ("min_price", branch.min_price, filters.min_price, branch.constraints.price_min_exclusive),
        ("max_price", branch.max_price, filters.max_price, branch.constraints.price_max_exclusive),
    ]:
        numbers = [n for n in (source, extra) if n is not None]
        number = (min(numbers) if field == "max_price" else max(numbers)) if numbers else None
        result[field] = _state(number=number, exclusive=bool(exclusive and source == number))
    lower, upper = result["min_price"], result["max_price"]
    if (
        lower["number"] is not None
        and upper["number"] is not None
        and (
            lower["number"] > upper["number"]
            or lower["number"] == upper["number"]
            and (lower["exclusive"] or upper["exclusive"])
        )
    ):
        lower["mode"] = upper["mode"] = "conflict"
    quantity = result["minimum_quantity"]
    maximum = branch.constraints.quantity_max
    if (
        quantity["number"] is not None
        and maximum is not None
        and (
            quantity["number"] > maximum
            or quantity["number"] == maximum
            and (quantity["exclusive"] or branch.constraints.quantity_max_exclusive)
        )
    ):
        quantity["mode"] = "conflict"
    return result


def effective_filter_state(research, intent=None):
    intent = intent or effective_intent(research)
    rows = [
        {
            "id": b.id,
            "label": branch_label(b),
            "category": b.category,
            "fields": branch_filter_state(intent, b, research.view.filters),
        }
        for b in intent.branches
    ]
    fields = {}
    for field in FIELDS:
        values = [row["fields"][field] for row in rows]
        fields[field] = (
            dict(values[0])
            if values and all(v == values[0] for v in values)
            else ({"mode": "mixed", "values": [], "number": None, "exclusive": False} if values else _state())
        )
    return {"fields": fields, "branches": rows}
