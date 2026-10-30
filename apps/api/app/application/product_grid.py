"""Product-branch execution and paged views for the existing research service.

Only IDs and scalar facet columns cross the full matching pool. Product DTOs
are hydrated for the requested page and explicit selections, never for facets.
"""

import base64
import hashlib
import json
import re
import time
from collections import Counter

from sqlalchemy import String, and_, any_, bindparam, case, cast, func, not_, or_, select, type_coerce
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

from app.application.research_analysis import PREFERENCE_TERMS
from app.database.models import IndexedProduct, ProductObservation, SupplierOffer
from app.domain.hard_request import deterministic_hard_request
from app.domain.intent import CATEGORY_TERMS
from app.domain.product_purpose import (
    PURPOSE_VERSION,
    identity_inputs,
    purpose_match,
    query_relation,
    scope_words,
)
from app.domain.research import ProductBranch, ResearchSession, ResearchView
from app.domain.taxonomy import QueryVocabulary, UniversalCategoryResolver, terms


def brand_key(value):
    return " ".join((value or "").split()).casefold()


def grid_ids(db, ids):
    # A populated VTO category can exceed asyncpg's parameter-count limit.
    # PostgreSQL receives a single typed array, not one parameter per offer.
    if db.bind.dialect.name == "postgresql":
        return SupplierOffer.id == any_(bindparam(None, value=ids, type_=ARRAY(String)))
    return SupplierOffer.id.in_(ids)


def handle_is_not_pen(name):
    return bool(
        re.search(r"(?:круж|стакан|сумк|двер|рюкзак).{0,90}ручк|ручк.{0,35}(?:круж|двер|сумк)", name, re.I)
        and not re.search(r"шариков|пишущ|роллер|ballpoint|writing pen|вечн\w*\s+карандаш", name, re.I)
    )


class ProductGridMixin:
    async def grid_upgrade(self, research):
        if (
            research.source_mode != "index"
            or research.unified
            and research.purpose_version == PURPOSE_VERSION
        ):
            return research
        async with self.repository.mutation_lock(research.id):
            r = await self.repository.get(research.id, hydrate=False)
            if r.unified and r.purpose_version == PURPOSE_VERSION:
                return r
            if r.unified:
                before = len(r.pool_offer_ids)
                r.history.append(
                    {
                        "view": r.view.model_dump(mode="json"),
                        "intent": r.intent.model_dump(mode="json"),
                        "operation": "REVALIDATE_PRODUCT_PURPOSE",
                        "previous_offer_ids": list(r.pool_offer_ids),
                        "previous_branch_offer_ids": [list(ids) for ids in r.branch_offer_ids],
                        "purpose_version": r.purpose_version,
                    }
                )
                await self.grid_pool(r)
                r.view.version += 1
                r.timings["purpose_revalidation"] = {
                    "from_count": before,
                    "to_count": len(r.pool_offer_ids),
                    "version": PURPOSE_VERSION,
                }
                await self.repository.save(r)
                return r
            # Retain existing effective constraints, selections and history.
            # Opening a legacy research never reparses it or contacts VTO.
            i = r.intent
            i.branches = [
                ProductBranch(
                    category=c,
                    label=i.raw_query if c.startswith("dynamic:") else c,
                    colors=[str(v) for v in i.colors],
                    quantity=i.quantity,
                    materials=[i.material] if i.material else [],
                    min_price=i.budget.min if i.budget else None,
                    max_price=i.budget.max if i.budget else None,
                )
                for c in i.categories
            ]
            f = r.view.filters
            for old, new in [("brand", "brands"), ("material", "materials"), ("size", "sizes")]:
                if f.attributes.get(old):
                    setattr(f, new, f.attributes.pop(old))
            r.unified = True
            r.view.version += 1
            await self.grid_pool(r)
            await self.repository.save(r)
            return r

    def grid_view_conditions(self, db, r):
        o, p, f = SupplierOffer, ProductObservation, r.view.filters
        brand = func.lower(func.trim(func.coalesce(o.payload["brand"].as_string(), "")))
        if db.bind.dialect.name == "postgresql":
            brand = func.regexp_replace(brand, r"\s+", " ", "g")
        else:
            brand = func.replace(func.replace(brand, "  ", " "), "  ", " ")
        material = func.coalesce(o.payload["material"].as_string(), "")
        size = o.payload["metadata"]["size"].as_string()
        conditions = []
        if f.brands:
            conditions.append(brand.in_([brand_key(v) for v in f.brands]))
        if f.materials:
            conditions.append(material.in_(f.materials))
        if f.sizes:
            conditions.append(size.in_(f.sizes))
        if f.colors:
            colors = (
                type_coerce(o.normalized_colors, JSONB)
                if db.bind.dialect.name == "postgresql"
                else cast(o.normalized_colors, String)
            )
            conditions.append(
                or_(
                    *(
                        colors.contains([str(v)])
                        if db.bind.dialect.name == "postgresql"
                        else colors.contains('"' + str(v) + '"')
                        for v in f.colors
                    )
                )
            )
        if f.categories:
            conditions.append(o.payload["category"].as_string().in_(f.categories))
        if f.min_price is not None:
            conditions.append(p.price_kzt >= f.min_price)
        if f.max_price is not None:
            conditions.append(p.price_kzt <= f.max_price)
        if f.minimum_quantity is not None:
            conditions.append(p.stock >= f.minimum_quantity)
        if f.selected_only:
            conditions.append(o.id.in_(r.view.selected_ids))

        def material_match(value):
            return or_(
                *(material.ilike("%" + token + "%") for token in PREFERENCE_TERMS.get(value, (value,)))
            )

        conditions.extend(not_(material_match(v)) for v in f.excluded_materials + f.excluded_terms)
        conditions.extend(material_match(v) for v in f.required_terms)
        return conditions

    async def grid_brands(self):
        async with self.index.sessions() as db:
            return [
                v
                for v in await db.scalars(select(SupplierOffer.payload["brand"].as_string()).distinct())
                if v
            ]

    async def grid_plan(self, query):
        relation = query_relation(query)
        understanding = await self.query_understanding.understand(query) if self.query_understanding else None
        parsed_query = understanding.search_query if understanding else query
        raw_roles = deterministic_hard_request(query)
        resolver = UniversalCategoryResolver()
        if (
            raw_roles.positions
            and all(resolver.resolve(p.category_text) for p in raw_roles.positions)
            or relation["scope"] != "STANDALONE"
            or re.search(r"для\s|подар|сотрудник|руководител|что-нибудь|что-то|в\s+дороге", query, re.I)
        ):
            # Catalog token correction must not replace purpose/audience prose
            # or an already resolved product list with popular catalog words.
            parsed_query = query
        intent = await self.planner.parse(parsed_query)
        roles = deterministic_hard_request(parsed_query)
        resolver = UniversalCategoryResolver()
        raw_roles = deterministic_hard_request(query)
        if raw_roles.positions and all(resolver.resolve(p.category_text) for p in raw_roles.positions):
            roles = raw_roles
        if (
            len(roles.positions) <= 1
            and roles.quantity.minimum is None
            and re.search(r"\d+\s*(?:шт|штук)|штук\s+\d+", query, re.I)
        ):
            roles.quantity.minimum = intent.quantity
        branches = []
        for position in roles.positions:
            resolved = resolver.resolve(position.category_text)
            if not resolved:
                resolved = [resolver.dynamic_id(position.category_text)]
            local = position.constraints
            for category in resolved:
                branches.append(
                    ProductBranch(
                        category=category,
                        label=position.category_text,
                        colors=local.color.values
                        if local.color and local.color.values
                        else roles.color.values,
                        quantity=local.required_quantity
                        if local.required_quantity is not None
                        else roles.quantity.minimum,
                        materials=local.materials or roles.materials,
                        min_price=local.price_min if local.price_min is not None else roles.price_min,
                        max_price=local.price_max if local.price_max is not None else roles.price_max,
                    )
                )
        if not branches:
            branches = [
                ProductBranch(
                    category=c,
                    label=resolver.concept(parsed_query) if c.startswith("dynamic:") else c,
                    colors=[x.value for x in intent.colors],
                    quantity=roles.quantity.minimum,
                    materials=[intent.material] if intent.material else [],
                    max_price=roles.price_max,
                    min_price=roles.price_min,
                )
                for c in intent.categories
            ]
        intent.branches = branches
        if relation["scope"] in ("SET", "PACKAGING", "ACCESSORY", "COMPONENT"):
            # A ready catalog set/accessory is one sold object, not independent
            # searches for each item mentioned in its contents/compatibility.
            branch = branches[0] if branches else ProductBranch(category="dynamic:object", label=query)
            branch = branch.model_copy(
                update=relation | {"category": "dynamic:role:" + relation["subject"], "label": query}
            )
            intent.branches = [branch]
        elif relation["scope"] == "ASSOCIATED":
            identity = identity_inputs(query)
            primary = [b for b in branches if b.category in identity.primary]
            intent.branches = [b.model_copy(update=relation) for b in primary] or branches
        elif relation["include_sets"]:
            intent.branches = [
                b.model_copy(update={"include_sets": True})
                for b in branches
                if not b.category.startswith("dynamic:")
            ]
        branches = intent.branches
        intent.categories = list(dict.fromkeys(b.category for b in branches))
        intent.brands = [
            v
            for v in await self.grid_brands()
            if re.search(r"(?<!\w)" + re.escape(v) + r"(?!\w)", query, re.I)
        ]
        # These fields describe global requirements, not colors copied from
        # every local branch by the legacy simple-query parser.
        intent.colors = roles.color.values
        intent.quantity = roles.quantity.minimum
        intent.material = roles.materials[0] if len(roles.materials) == 1 else None
        intent.original_query = intent.raw_query = query
        intent.normalized_query = parsed_query
        if understanding:
            intent.query_understanding = understanding.model_dump(mode="json")
            intent.query_understanding["retrieval_text_preserved"] = parsed_query == query
        return intent

    def grid_conditions(self, db, intent, branch):
        o, p = SupplierOffer, ProductObservation
        title = o.payload["name"].as_string()
        material = func.coalesce(o.payload["material"].as_string(), "")
        document = func.lower(func.coalesce(o.payload["metadata"]["semantic_document"].as_string(), title))
        conditions = [o.lifecycle != "REMOVED"]
        forms = QueryVocabulary.equivalents(branch.category) or terms(
            CATEGORY_TERMS.get(branch.category, branch.category)
        )
        if branch.scope in ("SET", "PACKAGING", "ACCESSORY", "COMPONENT"):
            # The deployed C-locale PostgreSQL does not case-fold Cyrillic
            # for ILIKE. Match sentence-case titles as on ordinary branches.
            conditions.append(
                or_(
                    *(
                        or_(title.ilike("%" + word + "%"), title.contains(word.capitalize()))
                        for word in scope_words(branch)
                    )
                )
            )
        elif branch.category.startswith("dynamic:"):
            words = terms(UniversalCategoryResolver().concept(branch.label))
            supplier_type = o.payload["metadata"]["supplier_category"].as_string()
            breadcrumbs = cast(o.payload["metadata"]["breadcrumbs"], String)
            conditions.extend(
                or_(*(field.ilike("%" + word + "%") for field in [title, supplier_type, breadcrumbs]))
                for word in words
            )
        else:
            evidence = or_(
                IndexedProduct.category == branch.category,
                *(or_(title.ilike("%" + form + "%"), title.contains(form.capitalize())) for form in forms),
            )
            conditions.append(evidence)
        for category in intent.excluded_categories:
            conditions.append(IndexedProduct.category != category)
        if branch.colors:
            colors = (
                type_coerce(o.normalized_colors, JSONB)
                if db.bind.dialect.name == "postgresql"
                else cast(o.normalized_colors, String)
            )
            conditions.append(
                or_(
                    *(
                        colors.contains([c])
                        if db.bind.dialect.name == "postgresql"
                        else colors.contains('"' + str(c) + '"')
                        for c in branch.colors
                    )
                )
            )
        if intent.excluded_colors:
            colors = (
                type_coerce(o.normalized_colors, JSONB)
                if db.bind.dialect.name == "postgresql"
                else cast(o.normalized_colors, String)
            )
            conditions.extend(
                not_(
                    colors.contains([str(c)])
                    if db.bind.dialect.name == "postgresql"
                    else colors.contains('"' + str(c) + '"')
                )
                for c in intent.excluded_colors
            )
        for value in branch.materials:
            conditions.append(or_(material.ilike("%" + value + "%"), document.contains(value.casefold())))
        for value in intent.excluded_materials:
            conditions.append(not_(document.contains(value.casefold())))
        for value, operator in [
            (branch.min_price, p.price_kzt.__ge__),
            (branch.max_price, p.price_kzt.__le__),
        ]:
            if value is not None:
                conditions.append(operator(value))
        if branch.quantity is not None:
            conditions.append(p.stock >= branch.quantity)
        if intent.brands:
            conditions.append(
                func.lower(func.trim(o.payload["brand"].as_string())).in_(
                    [brand_key(v) for v in intent.brands]
                )
            )
        return conditions

    async def grid_pool(self, research):
        pools = []
        async with self.index.sessions() as db:
            for branch in research.intent.branches:
                stmt = (
                    select(
                        SupplierOffer.id,
                        SupplierOffer.payload["name"].as_string(),
                        SupplierOffer.payload["metadata"]["supplier_category"].as_string(),
                        SupplierOffer.payload["metadata"]["breadcrumbs"],
                        SupplierOffer.payload["metadata"]["attributes"],
                        func.substr(SupplierOffer.payload["description"].as_string(), 1, 2000),
                        SupplierOffer.payload["brand"].as_string(),
                    )
                    .join(ProductObservation, ProductObservation.offer_id == SupplierOffer.id)
                    .join(IndexedProduct, IndexedProduct.id == SupplierOffer.product_id)
                    .where(*self.grid_conditions(db, research.intent, branch))
                    .order_by(SupplierOffer.id)
                )
                rows = (await db.execute(stmt)).all()
                pools.append(
                    [
                        id
                        for id, name, category, path, attrs, description, brand in rows
                        if purpose_match(
                            identity_inputs(name, category, path, attrs, description, brand), branch
                        )["accepted"]
                    ]
                )
        research.branch_offer_ids = pools
        research.indexed_offer_ids = list(dict.fromkeys(id for pool in pools for id in pool))
        research.pool_offer_ids = list(research.indexed_offer_ids)
        research.purpose_version = PURPOSE_VERSION

    async def grid_start(self, chat_id, query, budget=None):
        started = time.perf_counter()
        intent = await self.grid_plan(query)
        research = ResearchSession(
            chat_id=chat_id, intent=intent, unified=True, source_mode="index", job_status="COMPLETED"
        )
        if budget:
            research.budget = budget
        await self.grid_pool(research)
        notice = (
            "Показываю товары по указанным категориям; автоматическая сборка наборов больше не используется."
            if re.search(r"собери|собрать|составь", query, re.I)
            else f"Найдено {len(research.pool_offer_ids)} вариантов."
        )
        research.messages = [{"role": "user", "content": query}, {"role": "assistant", "content": notice}]
        research.timings = {
            "initial_search_ms": round((time.perf_counter() - started) * 1000, 2),
            "supplier_http": 0,
            "hydrated_objects": 0,
        }
        await self.repository.save(research)
        return research

    async def grid_change(self, identifier, **changes):
        async with self.repository.mutation_lock(identifier):
            r = await self.repository.get(identifier)
            if changes.get("version") is not None and changes["version"] != r.view.version:
                raise ValueError("STATE_CONFLICT")
            selected = changes.get("selected_ids")
            if selected is not None and not set(selected) <= set(r.pool_offer_ids) | set(r.view.selected_ids):
                raise ValueError("UNKNOWN_PRODUCT")
            version = r.view.version + 1
            if changes.get("restore") == "undo" and r.history:
                previous = r.history.pop()
                r.view = ResearchView.model_validate(previous["view"])
                if previous.get("intent"):
                    r.intent = type(r.intent).model_validate(previous["intent"])
                    await self.grid_pool(r)
            else:
                r.history.append(
                    {"view": r.view.model_dump(mode="json"), "intent": r.intent.model_dump(mode="json")}
                )
                if changes.get("restore") == "reset":
                    r.view = ResearchView(selected_ids=r.view.selected_ids)
                if changes.get("filters") is not None:
                    f = changes["filters"].model_copy(deep=True)
                    for field in f.model_fields_set - {"origins"}:
                        if getattr(f, field) != getattr(r.view.filters, field):
                            f.origins[field] = changes.get("origin", "manual")
                    r.view.filters = f
                if changes.get("sorting"):
                    r.view.sorting = changes["sorting"]
                if selected is not None:
                    r.view.selected_ids = selected
                if changes.get("intent") is not None:
                    r.intent = changes["intent"]
                    await self.grid_pool(r)
                if changes.get("preferences") is not None:
                    r.view.preferences = changes["preferences"]
            r.view.version = version
            for old, new in [("brand", "brands"), ("material", "materials"), ("size", "sizes")]:
                if r.view.filters.attributes.get(old):
                    setattr(r.view.filters, new, r.view.filters.attributes.pop(old))
            r.history = r.history[-30:]
            if changes.get("message"):
                r.messages.extend(
                    [
                        {"role": "user", "content": changes["message"]},
                        {"role": "assistant", "content": "Условия подборки обновлены."},
                    ]
                )
            await self.repository.save(r)
            return r

    async def grid_response(self, r, cursor=None, limit=100):
        from app.api.research import product_view
        from app.application.research_analysis import ProductGroupingService

        started = time.perf_counter()
        o, p = SupplierOffer, ProductObservation
        # Scalar projection with bounded purpose excerpts across the snapshot.
        # No Product/availability DTOs are hydrated for totals or facets.
        async with self.index.sessions() as db:
            branch_conditions = [
                and_(grid_ids(db, ids), *self.grid_conditions(db, r.intent, branch))
                for branch, ids in zip(r.intent.branches, r.branch_offer_ids, strict=True)
            ]
            stmt = (
                select(
                    o.id,
                    o.payload["brand"].as_string(),
                    o.payload["material"].as_string(),
                    o.payload["metadata"]["size"].as_string(),
                    o.normalized_colors,
                    p.price_kzt,
                    p.stock,
                    o.payload["category"].as_string(),
                    o.payload["name"].as_string(),
                    *(case((condition, 1), else_=0) for condition in branch_conditions),
                    o.payload["metadata"]["supplier_category"].as_string().label("purpose_category"),
                    o.payload["metadata"]["breadcrumbs"].label("purpose_breadcrumbs"),
                    o.payload["metadata"]["attributes"].label("purpose_attributes"),
                    func.substr(o.payload["description"].as_string(), 1, 2000).label("purpose_description"),
                )
                .join(p, p.offer_id == o.id)
                .join(IndexedProduct, IndexedProduct.id == o.product_id)
                .where(or_(*branch_conditions) if branch_conditions else o.id.in_([]))
            )
            rows = (await db.execute(stmt)).all()
            filtered_rows = (await db.execute(stmt.where(*self.grid_view_conditions(db, r)))).all()
        purpose_by_id = {}

        def verify(row):
            identity = identity_inputs(
                row[8],
                row.purpose_category,
                row.purpose_breadcrumbs,
                row.purpose_attributes,
                row.purpose_description,
                row[1],
            )
            accepted = {
                i: purpose_match(identity, b)
                for i, b in enumerate(r.intent.branches)
                if row[9 + i] and purpose_match(identity, b)["accepted"]
            }
            purpose_by_id[row[0]] = accepted
            return bool(accepted)

        rows = [row for row in rows if verify(row)]
        filtered_rows = [row for row in filtered_rows if purpose_by_id.get(row[0])]
        f = r.view.filters

        def eligible(row, omit=None):
            id, brand, material, size, colors, price, stock, category, name = row[:9]
            return (
                (omit == "brand" or not f.brands or brand_key(brand) in {brand_key(v) for v in f.brands})
                and (omit == "material" or not f.materials or material in f.materials)
                and (omit == "size" or not f.sizes or size in f.sizes)
                and (omit == "color" or not f.colors or set(colors or []) & set(f.colors))
                and (not f.categories or category in f.categories)
                and (f.min_price is None or price is not None and price >= f.min_price)
                and (f.max_price is None or price is not None and price <= f.max_price)
                and (f.minimum_quantity is None or stock is not None and stock >= f.minimum_quantity)
                and (not f.selected_only or id in r.view.selected_ids)
                and not any(
                    any(
                        token in (material or "").casefold()
                        for token in PREFERENCE_TERMS.get(value, (value,))
                    )
                    for value in f.excluded_materials + f.excluded_terms
                )
                and all(
                    any(
                        token in (material or "").casefold()
                        for token in PREFERENCE_TERMS.get(value, (value,))
                    )
                    for value in f.required_terms
                )
            )

        facets = {}
        for field, pos in [("brand", 1), ("material", 2), ("size", 3), ("color", 4)]:
            counts = Counter()
            display = {}
            for row in rows:
                if eligible(row, field):
                    for value in row[pos] or [] if field == "color" else [row[pos] or ""]:
                        key = brand_key(value) if field == "brand" else value
                        counts[key] += 1
                        display.setdefault(key, value or "Бренд не указан" if field == "brand" else value)
            facets[field] = [
                {"value": key, "label": display[key], "count": count}
                for key, count in sorted(counts.items())
                if key or field == "brand"
            ]
        branch_by_id = {}
        matches = {}
        for row in rows:
            for i, valid in enumerate(row[9 : 9 + len(r.intent.branches)]):
                if valid and i in purpose_by_id.get(row[0], {}):
                    branch_by_id.setdefault(row[0], i)
                    matches.setdefault(row[0], []).append(r.intent.branches[i].category)
        filtered = list(filtered_rows)

        def order(row):
            price = row[5]
            return (
                branch_by_id.get(row[0], 0),
                int(next(iter(purpose_by_id[row[0]].values()))["category_match"] != "EXACT")
                if r.view.sorting == "relevance"
                else 0,
                -sum(
                    any(
                        token.casefold() in ((row[2] or "") + " " + (row[8] or "")).casefold()
                        for token in PREFERENCE_TERMS.get(value, (value,))
                    )
                    or value in (row[4] or [])
                    for value in r.view.preferences + r.intent.soft_terms
                )
                if r.view.sorting == "relevance"
                else 0,
                price is None,
                (-price if r.view.sorting == "price_desc" else price)
                if price is not None and r.view.sorting != "relevance"
                else 0,
                row[0],
            )

        filtered.sort(key=order)
        ids = [row[0] for row in filtered]
        fingerprint = hashlib.sha256(
            json.dumps(
                [
                    r.id,
                    PURPOSE_VERSION,
                    r.intent.model_dump(mode="json"),
                    r.view.filters.model_dump(mode="json", exclude={"origins"}),
                    r.view.sorting,
                    r.view.preferences,
                    ids,
                ],
                sort_keys=True,
            ).encode()
        ).hexdigest()[:24]
        offset = 0
        if cursor:
            try:
                token, offset = json.loads(base64.urlsafe_b64decode(cursor))
                if token != fingerprint or type(offset) is not int or offset < 0:
                    raise ValueError()
            except (ValueError, TypeError, UnicodeError) as exc:
                raise ValueError("STALE_CURSOR") from exc
        page_ids = ids[offset : offset + limit]
        hydrate_ids = list(dict.fromkeys(page_ids + r.view.selected_ids))
        products = await self.index.candidates(r.intent, offer_ids=hydrate_ids) if hydrate_ids else []
        by_id = {p.id: p for p in products}

        def dto(id):
            match = next(
                iter(purpose_by_id.get(id, {}).values()),
                {"accepted": False, "category_match": "REJECTED", "reason": "OUTSIDE_CURRENT_RESULT"},
            )
            by_id[id].metadata["category_match"] = match["category_match"]
            if "identity" not in match:
                product = by_id[id]
                match = match | {
                    "identity": identity_inputs(
                        product.name,
                        product.metadata.get("supplier_category"),
                        product.metadata.get("breadcrumbs"),
                        product.metadata.get("attributes"),
                        product.description,
                        product.brand,
                    ).public()
                }
            return product_view(by_id[id]) | {
                "purpose_match": match,
                "matched_categories": matches.get(id, []),
                "branch_category": r.intent.branches[branch_by_id[id]].category
                if id in branch_by_id
                else None,
            }

        next_cursor = (
            base64.urlsafe_b64encode(json.dumps([fingerprint, offset + limit]).encode()).decode()
            if offset + limit < len(ids)
            else None
        )
        hidden = len(set(r.view.selected_ids) - set(ids))
        return {
            "id": r.id,
            "chat_id": r.chat_id,
            "intent": r.intent.model_dump(mode="json"),
            "source_mode": "index",
            "products": [dto(id) for id in page_ids if id in by_id],
            "selected_products": [dto(id) for id in r.view.selected_ids if id in by_id],
            "base_total": len(rows),
            "filtered_total": len(ids),
            "pool_count": len(rows),
            "matching_count": len(ids),
            "selected_count": len(r.view.selected_ids),
            "hidden_selected_count": hidden,
            "next_cursor": next_cursor,
            "new_matches_count": 0,
            "view": r.view.model_dump(mode="json"),
            "facets": facets,
            "groups": ProductGroupingService().groups([by_id[id] for id in page_ids if id in by_id]),
            "coverage": [],
            "completed_sources": 1,
            "source_count": 1,
            "stale_count": 0,
            "messages": r.messages,
            "can_undo": bool(r.history),
            "revision": r.revision,
            "status": "COMPLETED",
            "result_state": "INDEX_RESULTS" if ids else "COMPLETED_NO_MATCHES",
            "empty_branches": [
                b.label
                for i, b in enumerate(r.intent.branches)
                if not any(i in m for m in purpose_by_id.values())
            ],
            "timings": r.timings
            | {
                "view_ms": round((time.perf_counter() - started) * 1000, 2),
                "hydrated_objects": len(products),
                "sql_queries": 4 if hydrate_ids else 2,
            },
        }
