"""SQL facets over the same semantically verified, revisioned branch snapshots."""

import hashlib
import json
from collections import defaultdict

from sqlalchemy import (
    String,
    and_,
    any_,
    bindparam,
    func,
    literal,
    or_,
    select,
    true,
    type_coerce,
    union_all,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

from app.application.catalog_identity import brand_predicate
from app.database.models import (
    IndexedProduct,
    OfferAttributeProjection,
    OfferAttributeValue,
    ProductObservation,
    SupplierOffer,
)
from app.domain.catalog_identity import brand_identity, model_evidence
from app.domain.effective_filters import effective_intent
from app.domain.product_attributes import ATTRIBUTE_VERSION, ATTRIBUTES, applicable_attributes, value_label
from app.domain.query_branches import branch_label


def offer_ids(db, values):
    return (
        SupplierOffer.id == any_(bindparam(None, value=list(values), type_=ARRAY(String)))
        if db.bind.dialect.name == "postgresql"
        else SupplierOffer.id.in_(values)
    )


def attribute_predicate(branch_id, filters, omit=None):
    conditions = []
    for attribute, values in filters.branch_attributes.get(branch_id, {}).items():
        if values and omit != (branch_id, attribute):
            conditions.append(
                select(OfferAttributeValue.offer_id)
                .where(
                    OfferAttributeValue.offer_id == SupplierOffer.id,
                    OfferAttributeValue.attribute_id == attribute,
                    OfferAttributeValue.value_key.in_(values),
                )
                .correlate(SupplierOffer)
                .exists()
            )
    return conditions


def source_restricts(research, edit):
    intent = research.intent
    branches = [b for b in intent.branches if not edit.branch_id or b.id == edit.branch_id]
    if edit.field == "sizes":
        return any(b.constraints.sizes for b in branches)
    if edit.field == "minimum_quantity":
        return intent.quantity is not None or any(b.quantity is not None for b in branches)
    if edit.field in ("min_price", "max_price"):
        return bool(intent.budget) or any(getattr(b, edit.field) is not None for b in branches)
    return bool(getattr(intent, "material" if edit.field == "materials" else edit.field, None)) or any(
        getattr(b, edit.field) for b in branches
    )


def facet_research(research, omit=None):
    """Omit only manual narrowing, never an original required condition."""
    result = research.model_copy(deep=True)
    result.view.effective_overrides = [
        e
        for e in result.view.effective_overrides
        if e.action != "set" or omit is not None and e.field != omit or source_restricts(research, e)
    ]
    return result


class ContextualFacets:
    async def build(self, grid, source, registry, db):
        intent = effective_intent(source)
        o, p, a = SupplierOffer, ProductObservation, OfferAttributeValue
        root_pools = source.facet_branch_offer_ids or source.branch_offer_ids
        query_count = 0
        # Model matching is the existing normalized identity rule, not a weaker
        # raw substring SQL surrogate. Only IDs and identity scalars are read.
        allowed_models = None
        if source.view.filters.models or source.view.filters.branch_models:
            ids = set(v for pool in root_pools for v in pool)
            rows = (
                await db.execute(
                    select(
                        o.id,
                        o.payload["name"].as_string(),
                        o.payload["metadata"]["parent_name"].as_string(),
                        o.payload["metadata"]["model"].as_string(),
                    ).where(offer_ids(db, ids))
                )
            ).all()
            pool_sets = {b.id: set(pool) for b, pool in zip(intent.branches, root_pools, strict=True)}
            allowed_models = {
                b.id: [
                    row[0]
                    for row in rows
                    if row[0] in pool_sets[b.id]
                    and model_evidence(
                        tuple(
                            source.view.filters.models + source.view.filters.branch_models.get(b.category, [])
                        ),
                        row[2],
                        row[1],
                        row[3] or "",
                    )["accepted"]
                ]
                for b in intent.branches
            }
            query_count += 1

        def scope(omit=None, only_branch=None):
            common = {"brand": "brands", "color": "colors", "material": "materials", "size": "sizes"}.get(
                omit
            )
            scoped = effective_intent(facet_research(source, common)) if common else intent
            clauses = []
            for branch, pool in zip(scoped.branches, root_pools, strict=True):
                if branch.id in source.view.hidden_branch_ids or only_branch and branch.id != only_branch:
                    continue
                ids = allowed_models[branch.id] if allowed_models is not None else pool
                extra = []
                if omit != "brand" and source.view.filters.branch_brands.get(branch.category):
                    extra.append(
                        brand_predicate(registry, source.view.filters.branch_brands[branch.category])
                    )
                clauses.append(
                    and_(
                        offer_ids(db, ids),
                        *grid.grid_conditions(db, scoped, branch, registry),
                        *extra,
                        *attribute_predicate(
                            branch.id, source.view.filters, omit if isinstance(omit, tuple) else None
                        ),
                    )
                )
            return and_(
                or_(*clauses) if clauses else o.id.in_([]),
                *grid.grid_view_conditions(
                    db, source, registry, omit=omit if isinstance(omit, str) else None
                ),
            )

        def base(statement):
            return (
                statement.select_from(o)
                .join(p, p.offer_id == o.id)
                .join(IndexedProduct, IndexedProduct.id == o.product_id)
            )

        statements = []
        for field in ("brand", "material", "color", "size"):
            if field == "size":
                # Compatibility only; no untyped raw size or global values.
                statement = (
                    base(
                        select(
                            literal(field).label("field"),
                            a.value_key.label("value"),
                            func.count(func.distinct(o.id)).label("count"),
                        )
                    )
                    .join(a, a.offer_id == o.id)
                    .where(a.attribute_id.in_(["apparel_size", "paper_format"]), scope(field))
                    .group_by(a.value_key)
                )
            elif field == "color":
                values = (
                    func.jsonb_array_elements_text(type_coerce(o.normalized_colors, JSONB))
                    .table_valued("value")
                    .lateral()
                    if db.bind.dialect.name == "postgresql"
                    else func.json_each(o.normalized_colors).table_valued("value")
                )
                statement = (
                    base(
                        select(
                            literal(field).label("field"),
                            values.c.value.label("value"),
                            func.count(func.distinct(o.id)).label("count"),
                        )
                    )
                    .join(values, true())
                    .where(scope(field))
                    .group_by(values.c.value)
                )
            else:
                column = func.coalesce(o.payload[field].as_string(), "")
                statement = (
                    base(
                        select(
                            literal(field).label("field"),
                            column.label("value"),
                            func.count(func.distinct(o.id)).label("count"),
                        )
                    )
                    .where(scope(field))
                    .group_by(column)
                )
            statements.append(statement)
        rows = (await db.execute(union_all(*statements))).all()
        query_count += 1
        facets = {field: {} for field in ("brand", "color", "material", "size")}
        for field, raw, count in rows:
            if not raw and field != "brand":
                continue
            key = brand_identity(raw) if field == "brand" else raw
            value = facets[field].setdefault(
                key,
                {
                    "value": key,
                    "label": registry.display(raw)
                    if key and field == "brand"
                    else "Бренд не указан"
                    if field == "brand"
                    else raw,
                    "count": 0,
                },
            )
            value["count"] += count
        facets = {
            field: sorted(values.values(), key=lambda v: v["value"]) for field, values in facets.items()
        }
        typed_queries, groups = [], []
        for branch in intent.branches:
            if branch.id in source.view.hidden_branch_ids:
                continue
            groups.append(
                {
                    "branch_id": branch.id,
                    "label": branch_label(branch),
                    "category": branch.category,
                    "attributes": [],
                }
            )
            for attribute in applicable_attributes(branch.category):
                typed_queries.append(
                    base(
                        select(
                            literal(branch.id).label("branch_id"),
                            a.attribute_id,
                            a.value_key,
                            a.label,
                            a.numeric_value,
                            func.count(func.distinct(o.id)).label("count"),
                        )
                    )
                    .join(a, a.offer_id == o.id)
                    .where(a.attribute_id == attribute, scope((branch.id, attribute), branch.id))
                    .group_by(a.attribute_id, a.value_key, a.label, a.numeric_value)
                )
        typed = (await db.execute(union_all(*typed_queries))).all() if typed_queries else []
        query_count += bool(typed_queries)
        by_scope = defaultdict(dict)
        for branch_id, attribute, key, label, numeric, count in typed:
            by_scope[branch_id, attribute][key] = {
                "value": key,
                "label": label,
                "number": float(numeric) if numeric is not None else None,
                "count": count,
            }
        for group in groups:
            branch = next(b for b in intent.branches if b.id == group["branch_id"])
            for attribute in applicable_attributes(branch.category):
                selected = source.view.filters.branch_attributes.get(branch.id, {}).get(attribute, [])
                values = by_scope[branch.id, attribute]
                source_value = getattr(branch.constraints, attribute, None)
                if source_value is None and attribute in ("apparel_size", "paper_format") and branch.constraints.sizes:
                    source_value = (
                        branch.constraints.sizes[0]
                        if len(branch.constraints.sizes) == 1
                        else ", ".join(branch.constraints.sizes)
                    )
                if source_value is not None:
                    source_key = str(source_value)
                    values.setdefault(
                        source_key,
                        {
                            "value": source_key,
                            "label": value_label(attribute, source_key),
                            "number": source_value if ATTRIBUTES[attribute].kind == "number" else None,
                            "count": 0,
                        },
                    )
                for key in selected:
                    values.setdefault(
                        key,
                        {
                            "value": key,
                            "label": value_label(attribute, key),
                            "number": float(key) if ATTRIBUTES[attribute].kind == "number" else None,
                            "count": 0,
                        },
                    )
                if not values:
                    continue
                spec = ATTRIBUTES[attribute]
                group["attributes"].append(
                    {
                        "id": attribute,
                        "label": spec.label,
                        "type": spec.kind,
                        "unit": spec.unit,
                        "source_value": source_value,
                        "selected_values": selected,
                        "total_values": len(values),
                        "has_more": False,
                        "values": [
                            {
                                **v,
                                "selected": v["value"] in selected,
                                "disabled": v["count"] == 0 and v["value"] not in selected,
                            }
                            for v in sorted(
                                values.values(),
                                key=lambda v: (v["number"] is None, v["number"] or 0, v["value"]),
                            )
                        ],
                    }
                )
        # Context token includes applicable projection generations, never a global
        # all_sizes cache. Sorting and pagination do not affect facet membership.
        pools = set(id for pool in root_pools for id in pool)
        index_version = (
            await db.scalar(
                select(func.max(OfferAttributeProjection.derived_at)).where(
                    OfferAttributeProjection.offer_id.in_(pools)
                )
            )
            if db.bind.dialect.name != "postgresql"
            else await db.scalar(
                select(func.max(OfferAttributeProjection.derived_at)).where(
                    OfferAttributeProjection.offer_id
                    == any_(bindparam(None, value=list(pools), type_=ARRAY(String)))
                )
            )
        )
        query_count += 1
        token = hashlib.sha256(
            json.dumps(
                [
                    ATTRIBUTE_VERSION,
                    source.id,
                    intent.model_dump(mode="json"),
                    source.view.filters.model_dump(mode="json"),
                    source.view.hidden_branch_ids,
                    str(index_version),
                    facets,
                    groups,
                ],
                sort_keys=True,
                default=str,
            ).encode()
        ).hexdigest()[:24]
        return (
            facets,
            [g for g in groups if g["attributes"]],
            {
                "version": token,
                "projection_version": ATTRIBUTE_VERSION,
                "index_version": str(index_version),
                "scope": "visible_branches_before_pagination",
                "manual_self_filter": "excluded",
                "source_constraints": "retained",
                "count_unit": "unique_offer_id",
                "sql_queries": query_count,
            },
        )
