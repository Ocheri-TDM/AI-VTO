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

from app.application.catalog_identity import brand_predicate, brand_registry, identity_catalog
from app.application.query_branches import apply_branch_actions, removal_notice
from app.application.research_analysis import PREFERENCE_TERMS
from app.database.models import IndexedProduct, ProductObservation, SupplierOffer
from app.domain.catalog_identity import (
    IDENTITY_VERSION,
    brand_identity,
    brand_key,
    model_evidence,
    resolve_identity,
)
from app.domain.effective_filters import apply_filter_edits, effective_filter_state, effective_intent
from app.domain.hard_request import deterministic_hard_request
from app.domain.intent import CATEGORY_TERMS
from app.domain.models import ColorGroup
from app.domain.product_purpose import (
    PURPOSE_VERSION,
    identity_inputs,
    purpose_match,
    query_relation,
    scope_words,
)
from app.domain.query_branches import branch_label, effective_color_state, is_local_override, source_segments
from app.domain.query_constraints import MATERIAL_ALIASES, color_spans, parse_constraints
from app.domain.research import ProductBranch, ResearchSession, ResearchView
from app.domain.taxonomy import QueryVocabulary, UniversalCategoryResolver, terms


def identity_purpose(identity, branch):
    if branch.category == "*":
        return {
            "accepted": True,
            "category_match": "EXACT",
            "reason": "STRUCTURED_IDENTITY_SEARCH",
            "identity": identity.public(),
        }
    return purpose_match(identity, branch)


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
            and research.intent.identity_version == IDENTITY_VERSION
        ):
            return research
        async with self.repository.mutation_lock(research.id):
            r = await self.repository.get(research.id, hydrate=False)
            if (
                r.unified
                and r.purpose_version == PURPOSE_VERSION
                and r.intent.identity_version == IDENTITY_VERSION
            ):
                return r
            if r.unified:
                before = len(r.pool_offer_ids)
                r.history.append(
                    {
                        "view": r.view.model_dump(mode="json"),
                        "intent": r.intent.model_dump(mode="json"),
                        "operation": "REVALIDATE_PRODUCT_IDENTITY",
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

    def grid_view_conditions(self, db, r, registry):
        o, p, f = SupplierOffer, ProductObservation, r.view.filters
        material = func.coalesce(o.payload["material"].as_string(), "")
        size = o.payload["metadata"]["size"].as_string()
        conditions = []
        if f.brands:
            conditions.append(brand_predicate(registry, f.brands))
        if f.excluded_brands:
            conditions.append(not_(brand_predicate(registry, f.excluded_brands)))
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
        return list((await brand_registry(self.index.sessions)).labels.values())

    async def grid_identity(self, query, registry=None):
        registry = registry or await brand_registry(self.index.sessions)
        catalog = await identity_catalog(self.index.sessions)
        return resolve_identity(query, registry, catalog)

    @staticmethod
    def grid_apply_identity(intent, identity):
        intent.brands = identity.brands
        intent.excluded_brands = identity.excluded_brands
        intent.models = identity.models
        intent.identity_issues = identity.issues
        intent.identity_version = IDENTITY_VERSION
        for branch in intent.branches:
            scoped = identity.local.get(branch.category, {})
            branch.brands = scoped.get("brands", [])
            branch.excluded_brands = scoped.get("excluded_brands", [])
            branch.models = scoped.get("models", [])

    async def grid_plan(self, query, *, bind_sources=True):
        from app.domain.query_normalization import normalize_query
        original_query = query
        normalization = normalize_query(query)
        query = normalization.text
        registry = await brand_registry(self.index.sessions)
        identity = await self.grid_identity(query, registry)
        attributes, _ = parse_constraints(identity.attribute_query)
        semantic_query = (
            identity.cleaned
            if identity.brands
            or identity.models
            or identity.local
            or identity.excluded_brands
            or identity.issues
            else query
        )
        semantic_query = parse_constraints(semantic_query)[1]
        relation = query_relation(semantic_query)
        understanding = (
            await self.query_understanding.understand(semantic_query)
            if self.query_understanding and semantic_query
            else None
        )
        parsed_query = understanding.search_query if understanding else semantic_query
        raw_roles = deterministic_hard_request(semantic_query)
        resolver = UniversalCategoryResolver()
        if (
            resolver.resolve(semantic_query)
            or raw_roles.positions
            and all(resolver.resolve(p.category_text) for p in raw_roles.positions)
            or relation["scope"] != "STANDALONE"
            or re.search(r"для\s|подар|сотрудник|руководител|что-нибудь|что-то|в\s+дороге", query, re.I)
        ):
            # Catalog token correction must not replace purpose/audience prose
            # or an already resolved product list with popular catalog words.
            parsed_query = semantic_query
        intent = await self.planner.parse(parsed_query or query)
        excluded = []
        for span in re.finditer(
            r"\b(?:без|кроме|исключи|исключая|не\s+показывай)\s+([^,.!?;]+)", query, re.I
        ):
            if re.search(r"не\s+$", query[: span.start()], re.I):
                continue
            excluded.extend(UniversalCategoryResolver().resolve(span[1]))
            if re.search(r"одежд|apparel|clothing", span[1], re.I):
                excluded.append("tshirt")
        intent.excluded_categories = list(dict.fromkeys([*intent.excluded_categories, *excluded]))
        roles = deterministic_hard_request(parsed_query)
        resolver = UniversalCategoryResolver()
        raw_roles = deterministic_hard_request(semantic_query)
        if raw_roles.positions and all(resolver.resolve(p.category_text) for p in raw_roles.positions):
            roles = raw_roles
        if (
            len(roles.positions) <= 1
            and roles.quantity.minimum is None
            and re.search(r"\d+\s*(?:шт|штук)|штук\s+\d+", query, re.I)
        ):
            roles.quantity.minimum = intent.quantity
        branches = []
        known_categories = [c for c in resolver.resolve(parsed_query) if c not in intent.excluded_categories]
        if known_categories:
            # Legacy punctuation fragments are not product requirements. Unknown
            # actual list items are retained separately as unresolved branches.
            roles.positions = [p for p in roles.positions if resolver.resolve(p.category_text)]
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
                for c in known_categories or intent.categories
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
            purpose_identity = identity_inputs(query)
            primary = [b for b in branches if b.category in purpose_identity.primary]
            intent.branches = [b.model_copy(update=relation) for b in primary] or branches
        elif relation["include_sets"]:
            intent.branches = [
                b.model_copy(update={"include_sets": True})
                for b in branches
                if not b.category.startswith("dynamic:")
            ]
        branches = intent.branches
        if intent.discovery:
            # Functional prose is never a catalog position label. Reuse the
            # existing bounded discovery concepts with source provenance.
            branches = intent.branches = [
                ProductBranch(category=c, label=c, source_text=query)
                for c in intent.categories
                if c not in intent.excluded_categories
            ]
        if not semantic_query.strip() and (
            identity.brands or identity.models or identity.issues or attributes.sku
        ):
            intent.branches = branches = [ProductBranch(category="*", label="Все категории")]
        intent.categories = list(dict.fromkeys(b.category for b in branches))
        intent.categories = [c for c in intent.categories if c != "*"]
        self.grid_apply_identity(intent, identity)
        # These fields describe global requirements, not colors copied from
        # every local branch by the legacy simple-query parser.
        intent.colors = roles.color.values
        intent.quantity = roles.quantity.minimum
        intent.material = roles.materials[0] if len(roles.materials) == 1 else None
        intent.original_query = intent.raw_query = original_query
        intent.normalized_query = query if semantic_query != query else parsed_query
        if understanding:
            intent.query_understanding = understanding.model_dump(mode="json")
            intent.query_understanding["retrieval_text_preserved"] = parsed_query == query
            intent.query_understanding["identity_protected_text"] = semantic_query
        intent.query_understanding["normalization"] = normalization.trace()
        intent.query_understanding["unresolved_products"] = identity.unresolved_products
        intent.query_understanding["unresolved_attributes"] = identity.unresolved_attributes
        intent.query_understanding["interpretation_status"] = "PARTIAL" if identity.unresolved_products or identity.unresolved_attributes else "PARSED"
        if bind_sources:
            registry = await brand_registry(self.index.sessions)
            parts = source_segments(query, [b.category for b in intent.branches], registry)
            duplicate_categories = {c for c, _, _ in parts if sum(p[0] == c for p in parts) > 1}
            replacements = []
            if duplicate_categories and all(b.scope == "STANDALONE" for b in intent.branches):
                for category, source, offset in parts:
                    if category not in duplicate_categories:
                        continue
                    local_plan = await self.grid_plan(source, bind_sources=False)
                    for branch in local_plan.branches:
                        if branch.category == category:
                            branch.source_text = source
                            branch.models = branch.models or local_plan.models
                            branch.brands = branch.brands or local_plan.brands
                            branch.quantity = (
                                branch.quantity if branch.quantity is not None else intent.quantity
                            )
                            replacements.append((branch, offset))
                if replacements:
                    consumed_models = {m for b, _ in replacements for m in b.models}
                    consumed_brands = {v for b, _ in replacements for v in b.brands}
                    intent.models = [m for m in intent.models if m not in consumed_models]
                    intent.brands = [v for v in intent.brands if v not in consumed_brands]
            ordered = []
            for n, b in enumerate(intent.branches):
                if replacements and b.category in duplicate_categories:
                    continue
                item = next((p for p in parts if p[0] == b.category), None)
                offset = item[2] if item else query.casefold().find(b.label.casefold())
                b.source_text = (
                    item[1] if item else query[offset : offset + len(b.label)] if offset >= 0 else query
                )
                ordered.append((b, offset if offset >= 0 else len(query) + n))
            ordered.extend(replacements)
            unique = {}
            for b, _ in sorted(ordered, key=lambda item: item[1]):
                signature = json.dumps(
                    b.model_dump(mode="json", exclude={"id", "label", "source_text", "resolved"}),
                    sort_keys=True,
                )
                unique.setdefault(signature, b)
            intent.branches = list(unique.values())
            for order, branch in enumerate(intent.branches):
                branch.order = order
            intent.categories = list(dict.fromkeys(b.category for b in intent.branches if b.category != "*"))
        # Attribute scope follows the original product-bearing regions. Global
        # trailing clauses apply to every branch; local quantities/colors never
        # leak from one position into another.
        if len(intent.branches) > 12:
            raise ValueError("Слишком много товарных веток. Разделите запрос.")
        if len(intent.branches) <= 1:
            global_attributes = attributes
        else:
            remainder = list(query)
            for _, source, offset in source_segments(
                query, intent.categories, registry, include_overrides=True
            ):
                start = query.find(source, offset)
                if start >= 0:
                    remainder[start : start + len(source)] = " " * len(source)
            for sentence in re.finditer(r"(?<=[.!?])\s+([^.!?]+)", query):
                if not UniversalCategoryResolver().resolve(sentence[1]):
                    remainder[sentence.start(1) : sentence.end(1)] = sentence[1]
            global_attributes = parse_constraints(
                resolve_identity("".join(remainder), registry).attribute_query
            )[0]
        from app.domain.models import ColorGroup

        # Product agreement: an initial common color qualifies the entire list
        # if no branch introduces another hard color. Other local colors retain
        # their own source scope; negative/preference colors never enter here.
        from app.domain.query_branches import category_mentions
        mentions = category_mentions(query, intent.categories, registry)
        if len(intent.branches) > 1 and mentions:
            first = min(start for start, _, _ in mentions)
            prefix = parse_constraints(query[:first])[0]
            if prefix.colors and set(attributes.colors) == set(prefix.colors):
                global_attributes.colors = prefix.colors
            amounts = list(re.finditer(r"\b(?:тираж(?:ом)?|количество|минимум|не\s+менее|от)\s+(\d+)\s*(?:шт|штук)?", query, re.I))
            if len(amounts) == 1 and attributes.quantity_min is not None:
                amount = amounts[0]
                if amount.end() <= first or amount.start() >= max(end for _, end, _ in mentions):
                    global_attributes.quantity_min = attributes.quantity_min

        intent.colors = [ColorGroup(c) for c in global_attributes.colors]
        intent.quantity = global_attributes.quantity_min
        intent.excluded_colors = [
            ColorGroup(c) for c in dict.fromkeys([*intent.excluded_colors, *attributes.excluded_colors])
        ]
        intent.excluded_materials = list(
            dict.fromkeys([*intent.excluded_materials, *attributes.excluded_materials])
        )
        intent.soft_terms = list(dict.fromkeys([*intent.soft_terms, *attributes.preferences]))
        overrides = [
            (category, source)
            for category, source, _ in source_segments(
                query, intent.categories, registry, include_overrides=True
            )
            if is_local_override(source)
        ]
        for branch in intent.branches:
            local = (
                parse_constraints(resolve_identity(branch.source_text, registry).attribute_query)[0]
                if len(intent.branches) > 1
                else attributes
            )
            for category, source in overrides:
                if branch.category == category:
                    override = parse_constraints(resolve_identity(source, registry).attribute_query)[0]
                    if override.colors:
                        local.colors = override.colors
                        branch.origins["colors"] = "local_override: " + source
            merged = global_attributes.model_dump()
            merged.update(
                {k: v for k, v in local.model_dump().items() if v is not None and v != [] and v is not False}
            )
            branch.constraints = type(attributes).model_validate(merged)
            branch.colors = branch.constraints.colors
            if branch.colors and "colors" not in branch.origins:
                branch.origins["colors"] = (
                    "local: " + branch.source_text if local.colors else "global: " + query
                )
            if branch.constraints.allow_near_colors:
                from app.application.research_planner import BLUE_FAMILY

                if set(branch.colors).intersection(BLUE_FAMILY):
                    branch.colors = list(dict.fromkeys([*branch.colors, *BLUE_FAMILY]))
            branch.quantity = branch.constraints.quantity_min
            branch.materials = branch.constraints.materials
            branch.min_price = branch.constraints.price_min
            branch.max_price = branch.constraints.price_max
        intent.query_understanding["color_spans"] = [
            {
                "start": span.start,
                "end": span.end,
                "text": span.text,
                "role": span.role,
                "canonical": span.color,
            }
            for span in color_spans(identity.attribute_query)
        ]
        intent.query_understanding["color_scope_version"] = "color-scope-v1"
        for fragment in identity.unresolved_products:
            intent.branches.append(ProductBranch(category=resolver.dynamic_id(fragment), label=fragment, resolved=False,
                                                  source_text=fragment, order=len(intent.branches)))
        intent.categories = list(dict.fromkeys(b.category for b in intent.branches if b.category != "*"))
        return intent

    def grid_conditions(self, db, intent, branch, registry):
        o, p = SupplierOffer, ProductObservation
        title = o.payload["name"].as_string()
        material = func.coalesce(o.payload["material"].as_string(), "")
        document = func.lower(func.coalesce(o.payload["metadata"]["semantic_document"].as_string(), title))
        conditions = [o.lifecycle != "REMOVED"]
        if branch.origins.get("effective_brand_conflict"):
            from sqlalchemy import false

            conditions.append(false())
        if intent.identity_issues:
            from sqlalchemy import false

            conditions.append(false())
        forms = QueryVocabulary.equivalents(branch.category) or terms(
            CATEGORY_TERMS.get(branch.category, branch.category)
        )
        if branch.category == "*":
            pass
        elif branch.scope in ("SET", "PACKAGING", "ACCESSORY", "COMPONENT"):
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

        def material_match(value):
            aliases = MATERIAL_ALIASES.get(value, [value])
            return or_(
                *(or_(material.ilike("%" + a + "%"), document.contains(a.casefold())) for a in aliases)
            )

        for value in branch.materials:
            conditions.append(
                material == value
                if branch.origins.get("effective_materials") and value not in MATERIAL_ALIASES
                else material_match(value)
            )
        for value in intent.excluded_materials:
            conditions.append(not_(material_match(value)))
        constraints = branch.constraints
        if branch.min_price is not None:
            conditions.append(
                p.price_kzt > branch.min_price
                if constraints.price_min_exclusive
                else p.price_kzt >= branch.min_price
            )
        if branch.max_price is not None:
            conditions.append(
                p.price_kzt < branch.max_price
                if constraints.price_max_exclusive
                else p.price_kzt <= branch.max_price
            )
        if branch.quantity is not None:
            conditions.append(
                p.stock > branch.quantity
                if constraints.quantity_min_exclusive
                else p.stock >= branch.quantity
            )
        if constraints.availability_only:
            conditions.append(p.stock > 0)
        if constraints.quantity_max is not None:
            conditions.append(
                p.stock < constraints.quantity_max
                if constraints.quantity_max_exclusive
                else p.stock <= constraints.quantity_max
            )
        for field in ("capacity_ml", "capacity_mah", "storage_gb"):
            value = getattr(constraints, field)
            if value is not None:
                # Exact published technical evidence, never another number in
                # a model, price or requested stock count.
                metadata = o.payload["metadata"][field].as_string()
                capacity = o.payload["capacity"].as_string()
                evidence = [
                    metadata == str(value),
                    capacity == str(value),
                    capacity.in_([f"{value} мл", f"{value} мАч"]),
                ]
                if db.bind.dialect.name == "postgresql":
                    units = {"capacity_mah": "(?:м[Аа]ч|mAh)", "capacity_ml": "(?:мл|ml)", "storage_gb": "(?:ГБ|GB)"}[field]
                    pattern = rf"(^|[^0-9]){value}[[:space:]]*{units}([^[:alpha:]]|$)"
                    published = [title, cast(o.payload["metadata"]["attributes"], String)]
                    evidence.extend(column.op("~*")(pattern) for column in published)
                    if field == "capacity_ml":
                        litres = format(value / 1000, "g").replace(".", "[.,]")
                        pattern = rf"(^|[^0-9]){litres}[[:space:]]*(?:л|l)([^[:alpha:]]|$)"
                        evidence.extend(column.op("~*")(pattern) for column in published)
                conditions.append(or_(*evidence))
        for connector in constraints.connectors:
            if connector == "USB-C":
                conditions.append(or_(document.ilike("%usb-c%"), document.ilike("%usb c%"),
                                      title.contains("USB-C"), title.contains("USB C")))
        if constraints.sizes:
            conditions.append(func.upper(o.payload["metadata"]["size"].as_string()).in_(constraints.sizes))
        if constraints.sku:
            conditions.append(func.upper(o.payload["metadata"]["sku"].as_string()) == constraints.sku.upper())
        if constraints.subtypes:
            conditions.append(
                or_(
                    *(
                        or_(
                            o.payload["metadata"]["subtype"].as_string() == subtype.casefold(),
                            title.ilike("%" + ("ежедневник" if subtype == "DIARY" else "блокнот") + "%"),
                            title.contains("Ежедневник" if subtype == "DIARY" else "Блокнот"),
                        )
                        for subtype in constraints.subtypes
                    )
                )
            )
        for feature in constraints.features:
            if feature == "WITHOUT_VISOR":
                conditions.append(or_(title.ilike("%без козырька%"),title.contains("Без козырька"),title.ilike("%without visor%")))
                continue
            if feature == "ETERNAL":
                conditions.append(
                    or_(title.ilike("%вечн%"), title.contains("Вечн"), title.ilike("%eternal%"))
                )
            elif feature == "HYBRID_2_IN_1":
                conditions.append(
                    title.op("~*")(r"2[ -]*(?:в|in)[ -]*1")
                    if db.bind.dialect.name == "postgresql"
                    else or_(title.contains("2 в 1"), title.contains("2-in-1"), title.contains("2-в-1"))
                )
            else:
                conditions.append(or_(title.ilike("%" + feature + "%"), title.contains(feature.capitalize())))
        if intent.brands:
            conditions.append(brand_predicate(registry, intent.brands))
        if branch.brands:
            conditions.append(brand_predicate(registry, branch.brands))
        for values in (intent.excluded_brands, branch.excluded_brands):
            if values:
                conditions.append(not_(brand_predicate(registry, values)))
        return conditions

    async def grid_pool(self, research):
        if research.intent.identity_version != IDENTITY_VERSION:
            self.grid_apply_identity(research.intent, await self.grid_identity(research.intent.raw_query))
        registry = await brand_registry(self.index.sessions)
        intent = effective_intent(research)
        pools = []
        funnel = []
        async with self.index.sessions() as db:
            for branch in intent.branches:
                stmt = (
                    select(
                        SupplierOffer.id,
                        SupplierOffer.payload["name"].as_string(),
                        SupplierOffer.payload["metadata"]["supplier_category"].as_string(),
                        SupplierOffer.payload["metadata"]["breadcrumbs"],
                        SupplierOffer.payload["metadata"]["attributes"],
                        func.substr(SupplierOffer.payload["description"].as_string(), 1, 2000),
                        SupplierOffer.payload["brand"].as_string(),
                        SupplierOffer.payload["metadata"]["parent_name"].as_string(),
                        func.coalesce(
                            SupplierOffer.payload["metadata"]["model"].as_string(),
                            SupplierOffer.payload["model_name"].as_string(),
                            "",
                        ),
                    )
                    .join(ProductObservation, ProductObservation.offer_id == SupplierOffer.id)
                    .join(IndexedProduct, IndexedProduct.id == SupplierOffer.product_id)
                    .where(*self.grid_conditions(db, intent, branch, registry))
                    .order_by(SupplierOffer.id)
                )
                rows = (await db.execute(stmt)).all()
                accepted = [
                    row for row in rows if identity_purpose(identity_inputs(*row[1:7]), branch)["accepted"]
                ]
                models = tuple(intent.models + branch.models)
                matched = [
                    row[0] for row in accepted if model_evidence(models, row[7], row[1], row[8])["accepted"]
                ]
                pools.append(matched)
                branch.resolved = (
                    not branch.category.startswith("dynamic:")
                    or branch.scope != "STANDALONE"
                    or bool(matched)
                )
                if not branch.resolved:
                    probe_branch = branch.model_copy(
                        update={
                            "colors": [],
                            "materials": [],
                            "brands": [],
                            "models": [],
                            "excluded_brands": [],
                            "quantity": None,
                            "min_price": None,
                            "max_price": None,
                        }
                    )
                    probe_intent = intent.model_copy(
                        update={
                            "brands": [],
                            "excluded_brands": [],
                            "models": [],
                            "excluded_categories": [],
                            "excluded_materials": [],
                            "excluded_colors": [],
                            "identity_issues": [],
                        }
                    )
                    branch.resolved = bool(
                        await db.scalar(
                            select(
                                select(SupplierOffer.id)
                                .join(ProductObservation, ProductObservation.offer_id == SupplierOffer.id)
                                .join(IndexedProduct, IndexedProduct.id == SupplierOffer.product_id)
                                .where(*self.grid_conditions(db, probe_intent, probe_branch, registry))
                                .exists()
                            )
                        )
                    )
                funnel.append(
                    {
                        "category": branch.category,
                        "sql_candidates": len(rows),
                        "purpose_matches": len(accepted),
                        "model_matches": len(matched),
                        "models": list(models),
                    }
                )
                if not matched:
                    funnel[-1].update(await self.grid_empty_diagnostic(db, intent, branch, registry))
        for source, effective in zip(research.intent.branches, intent.branches, strict=True):
            source.resolved = effective.resolved
        research.branch_offer_ids = pools
        research.indexed_offer_ids = list(dict.fromkeys(id for pool in pools for id in pool))
        research.pool_offer_ids = list(research.indexed_offer_ids)
        research.purpose_version = PURPOSE_VERSION
        research.timings["identity_funnel"] = funnel

    async def grid_empty_diagnostic(self, db, intent, branch, registry):
        """One aggregate SQL pass, only for empty branches. No DTO hydration."""
        from app.domain.query_constraints import AttributeConstraints

        empty = branch.model_copy(
            update={
                "colors": [],
                "materials": [],
                "quantity": None,
                "min_price": None,
                "max_price": None,
                "brands": [],
                "excluded_brands": [],
                "constraints": AttributeConstraints(),
            }
        )
        plain = intent.model_copy(
            update={
                "brands": [],
                "excluded_brands": [],
                "excluded_materials": [],
                "excluded_colors": [],
                "identity_issues": [],
            }
        )
        phases = [("category", plain, empty)]
        branded = empty.model_copy(
            update={"brands": branch.brands, "excluded_brands": branch.excluded_brands}
        )
        phases.append(
            (
                "brand",
                plain.model_copy(update={"brands": intent.brands, "excluded_brands": intent.excluded_brands}),
                branded,
            )
        )
        colored = branded.model_copy(update={"colors": branch.colors})
        scoped = phases[-1][1].model_copy(update={"excluded_colors": intent.excluded_colors})
        phases.append(("color", scoped, colored))
        material = colored.model_copy(update={"materials": branch.materials})
        scoped = scoped.model_copy(update={"excluded_materials": intent.excluded_materials})
        phases.append(("material", scoped, material))
        priced = material.model_copy(
            update={
                "min_price": branch.min_price,
                "max_price": branch.max_price,
                "constraints": branch.constraints.model_copy(
                    update={"quantity_min": None, "quantity_max": None}
                ),
            }
        )
        phases.append(("price_and_technical_attributes", scoped, priced))
        conditions = self.grid_conditions(db, scoped, priced, registry)
        counts = [
            func.count().filter(and_(*self.grid_conditions(db, i, b, registry))).label(name)
            for name, i, b in phases
        ]
        counts += [
            func.count().filter(and_(*conditions, ProductObservation.stock.is_(None))).label("stock_unknown"),
            func.count()
            .filter(and_(*conditions, ProductObservation.stock.is_not(None)))
            .label("stock_known"),
        ]
        row = (
            (
                await db.execute(
                    select(*counts)
                    .select_from(SupplierOffer)
                    .join(ProductObservation, ProductObservation.offer_id == SupplierOffer.id)
                    .join(IndexedProduct, IndexedProduct.id == SupplierOffer.product_id)
                )
            )
            .mappings()
            .one()
        )
        values = dict(row)
        reason = next(
            (name.upper() + "_NO_MATCH" for name, _, _ in phases if not values[name]),
            "PURPOSE_OR_MODEL_NO_MATCH",
        )
        o = SupplierOffer
        candidates = (
            await db.execute(
                select(
                    o.id,
                    o.payload["name"].as_string(),
                    o.payload["metadata"]["supplier_category"].as_string(),
                    o.payload["metadata"]["breadcrumbs"],
                    o.payload["metadata"]["attributes"],
                    func.substr(o.payload["description"].as_string(), 1, 2000),
                    o.payload["brand"].as_string(),
                    o.payload["metadata"]["parent_name"].as_string(),
                    func.coalesce(
                        o.payload["metadata"]["model"].as_string(), o.payload["model_name"].as_string(), ""
                    ),
                    ProductObservation.stock,
                )
                .join(ProductObservation, ProductObservation.offer_id == o.id)
                .join(IndexedProduct, IndexedProduct.id == o.product_id)
                .where(
                    *self.grid_conditions(
                        db, intent, priced.model_copy(update={"models": branch.models}), registry
                    )
                )
            )
        ).all()
        semantic = [
            r
            for r in candidates
            if identity_purpose(identity_inputs(*r[1:7]), branch)["accepted"]
            and model_evidence(tuple(intent.models + branch.models), r[7], r[1], r[8])["accepted"]
        ]
        values["before_quantity_semantic"] = len(semantic)
        values["stock_unknown"] = sum(r[9] is None for r in semantic)
        values["stock_known"] = len(semantic) - values["stock_unknown"]
        if intent.identity_issues:
            reason = "AMBIGUOUS_IDENTITY"
        elif branch.resolved is False:
            reason = "UNRESOLVED_CONCEPT"
        elif semantic and branch.quantity is not None:
            reason = "STOCK_UNKNOWN" if not values["stock_known"] else "QUANTITY_INSUFFICIENT"
        return {
            "zero_reason": reason,
            "diagnostic_sql_counts": values,
            "before_quantity_offer_ids": [r[0] for r in semantic],
            "final_eligible": 0,
        }

    async def grid_start(self, chat_id, query, budget=None, *, persist=True):
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
        if re.search(r"собери|собрать|составь", query, re.I):
            roles = deterministic_hard_request(query)
            research.timings["unsupported_grid_constraints"] = ["set_count"]
            if roles.items_per_position > 1:
                research.timings["unsupported_grid_constraints"].append("items_per_position")
                research.messages[-1]["content"] += (
                    " Требование нескольких предметов каждой категории и уникальности между наборами в товарной сетке не выполняется."
                )
        research.timings.update(
            {
                "initial_search_ms": round((time.perf_counter() - started) * 1000, 2),
                "supplier_http": 0,
                "hydrated_objects": 0,
            }
        )
        if persist:
            await self.grid_prepare_response(research)
            await self.repository.save(research)
            research._prepared_response["revision"] = research.revision
        return research

    async def grid_prepare_response(self, research):
        # DTO construction and JSON encoding must succeed before a publication
        # transaction starts. A failed render must leave the active workspace intact.
        from fastapi.encoders import jsonable_encoder

        response = jsonable_encoder(await self.grid_response(research))
        json.dumps(response, ensure_ascii=False, allow_nan=False)
        research._prepared_response = response

    async def grid_change(self, identifier, **changes):
        async with self.repository.mutation_lock(identifier):
            r = await self.repository.get(identifier)
            if changes.get("version") is not None and changes["version"] != r.view.version:
                raise ValueError("STATE_CONFLICT")
            selected = changes.get("selected_ids")
            if selected is not None and not set(selected) <= set(r.pool_offer_ids) | set(r.view.selected_ids):
                raise ValueError("UNKNOWN_PRODUCT")
            version = r.view.version + 1
            category_version = r.view.category_version
            previous_overrides = list(r.view.effective_overrides)
            pool_updated = False
            if changes.get("restore") == "undo" and r.history:
                previous = r.history.pop()
                r.view = ResearchView.model_validate(previous["view"])
                if previous.get("intent"):
                    r.intent = type(r.intent).model_validate(previous["intent"])
                    await self.grid_pool(r)
                    pool_updated = True
            else:
                r.history.append(
                    {"view": r.view.model_dump(mode="json"), "intent": r.intent.model_dump(mode="json")}
                )
                if changes.get("restore") == "reset":
                    r.view = ResearchView(
                        selected_ids=r.view.selected_ids, hidden_branch_ids=r.view.hidden_branch_ids
                    )
                if changes.get("filters") is not None:
                    f = changes["filters"].model_copy(deep=True)
                    for field in f.model_fields_set - {"origins"}:
                        if getattr(f, field) != getattr(r.view.filters, field):
                            f.origins[field] = changes.get("origin", "manual")
                    r.view.filters = f
                if changes.get("effective_changes"):
                    apply_filter_edits(r, changes["effective_changes"])
                if changes.get("sorting"):
                    r.view.sorting = changes["sorting"]
                if selected is not None:
                    r.view.selected_ids = selected
                if changes.get("intent") is not None:
                    previous = {b.id: b for b in r.intent.branches}
                    for branch in changes["intent"].branches:
                        old = previous.get(branch.id)
                        if not old:
                            continue
                        for outer, inner in [
                            ("quantity", "quantity_min"),
                            ("min_price", "price_min"),
                            ("max_price", "price_max"),
                            ("materials", "materials"),
                            ("colors", "colors"),
                        ]:
                            if getattr(branch, outer) != getattr(old, outer):
                                setattr(branch.constraints, inner, getattr(branch, outer))
                                if outer in ("quantity", "min_price", "max_price"):
                                    setattr(branch.constraints, inner + "_exclusive", False)
                                if outer == "colors":
                                    branch.constraints.allow_near_colors = False
                    r.intent = changes["intent"]
                    if changes.get("clear_effective_overrides"):
                        r.view.effective_overrides = []
                    await self.grid_pool(r)
                    pool_updated = True
                if changes.get("branch_actions"):
                    if apply_branch_actions(r, changes["branch_actions"]):
                        await self.grid_pool(r)
                        pool_updated = True
                if changes.get("preferences") is not None:
                    r.view.preferences = changes["preferences"]
            if previous_overrides != r.view.effective_overrides and not pool_updated:
                await self.grid_pool(r)
            r.view.version = version
            r.view.category_version = category_version + int(
                bool(
                    changes.get("branch_actions")
                    or changes.get("intent") is not None
                    or changes.get("restore") == "undo"
                )
            )
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
            await self.grid_prepare_response(r)
            await self.repository.save(r, db=changes.get("save_db"))
            r._prepared_response["revision"] = r.revision
            return r

    async def grid_response(self, r, cursor=None, limit=100):
        if getattr(self, "collections", None):
            state = await self.collections.for_chat(r.chat_id)
            if state is not None:
                r.view.selected_ids = state["selected_ids"]
        from app.api.research import product_view
        from app.application.research_analysis import ProductGroupingService

        r = r.model_copy(update={"intent": effective_intent(r)})

        started = time.perf_counter()
        registry = await brand_registry(self.index.sessions)
        o, p = SupplierOffer, ProductObservation
        # Scalar projection with bounded purpose excerpts across the snapshot.
        # No Product/availability DTOs are hydrated for totals or facets.
        async with self.index.sessions() as db:
            # Replacement controls must be able to choose values outside the
            # current constrained pool. Only distinct public scalar attributes
            # cross this query; their counts remain the current pool's facets.
            choices = (
                await db.execute(
                    select(o.payload["material"].as_string(), o.payload["metadata"]["size"].as_string())
                    .where(o.lifecycle != "REMOVED")
                    .distinct()
                )
            ).all()
            branch_conditions = [
                and_(grid_ids(db, ids), *self.grid_conditions(db, r.intent, branch, registry))
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
                    o.payload["metadata"]["parent_name"].as_string().label("identity_parent"),
                    func.coalesce(
                        o.payload["metadata"]["model"].as_string(), o.payload["model_name"].as_string(), ""
                    ).label("identity_model"),
                )
                .join(p, p.offer_id == o.id)
                .join(IndexedProduct, IndexedProduct.id == o.product_id)
                .where(or_(*branch_conditions) if branch_conditions else o.id.in_([]))
            )
            rows = (await db.execute(stmt)).all()
            filtered_rows = (await db.execute(stmt.where(*self.grid_view_conditions(db, r, registry)))).all()
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
                i: identity_purpose(identity, b)
                for i, b in enumerate(r.intent.branches)
                if row[9 + i]
                and identity_purpose(identity, b)["accepted"]
                and model_evidence(
                    tuple(r.intent.models + b.models), row.identity_parent, row[8], row.identity_model
                )["accepted"]
            }
            purpose_by_id[row[0]] = accepted
            return bool(accepted)

        rows = [row for row in rows if verify(row)]
        filtered_rows = [row for row in filtered_rows if purpose_by_id.get(row[0])]
        f = r.view.filters

        def branch_matches(row, visible=False):
            return [
                i
                for i, b in enumerate(r.intent.branches)
                if i in purpose_by_id.get(row[0], {})
                and (not visible or b.id not in r.view.hidden_branch_ids)
                and (
                    not f.branch_brands.get(b.category)
                    or brand_identity(row[1]) in {brand_key(v) for v in f.branch_brands[b.category]}
                )
                and model_evidence(
                    tuple(f.branch_models.get(b.category, [])),
                    row.identity_parent,
                    row[8],
                    row.identity_model,
                )["accepted"]
            ]

        def eligible(row, omit=None):
            id, brand, material, size, colors, price, stock, category, name = row[:9]
            return (
                (omit == "brand" or not f.brands or brand_identity(brand) in {brand_key(v) for v in f.brands})
                and (
                    omit == "brand" or brand_identity(brand) not in {brand_key(v) for v in f.excluded_brands}
                )
                and model_evidence(tuple(f.models), row.identity_parent, name, row.identity_model)["accepted"]
                and bool(branch_matches(row))
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
                        for token in MATERIAL_ALIASES.get(value, PREFERENCE_TERMS.get(value, (value,)))
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
                        key = brand_identity(value) if field == "brand" else value
                        counts[key] += 1
                        display.setdefault(
                            key,
                            (registry.display(value) if key else "Бренд не указан")
                            if field == "brand"
                            else value,
                        )
            facets[field] = [
                {"value": key, "label": display[key], "count": count}
                for key, count in sorted(counts.items())
                if key or field == "brand"
            ]
        branch_by_id = {}
        matches = {}
        eligible_rows = [row for row in filtered_rows if eligible(row)]
        branch_counts = Counter(i for row in eligible_rows for i in branch_matches(row))
        for row in rows:
            for i in branch_matches(row, visible=True):
                branch_by_id.setdefault(row[0], i)
                matches.setdefault(row[0], []).append(r.intent.branches[i].category)
        filtered = [row for row in eligible_rows if row[0] in branch_by_id]

        def order(row):
            price = row[5]
            return (
                branch_by_id.get(row[0], 0),
                int(next(iter(purpose_by_id[row[0]].values()))["category_match"] != "EXACT")
                if r.view.sorting == "relevance"
                else 0,
                -sum(
                    any(
                        token.casefold()
                        in ((row[1] or "") + " " + (row[2] or "") + " " + (row[8] or "")).casefold()
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
                    r.view.hidden_branch_ids,
                    r.view.category_version,
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
            product = by_id[id]
            branch = r.intent.branches[branch_by_id[id]] if id in branch_by_id else None
            models = (
                r.intent.models
                + f.models
                + (branch.models + f.branch_models.get(branch.category, []) if branch else [])
            )
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
                "identity_match": {
                    "brand": by_id[id].brand,
                    "brand_key": brand_identity(product.brand),
                    "models": list(dict.fromkeys(models)),
                    "brand_source": "VTO_STRUCTURED_BRAND" if brand_identity(product.brand) else None,
                    "model_evidence": model_evidence(
                        tuple(models),
                        product.metadata.get("parent_name"),
                        product.name,
                        product.metadata.get("model") or getattr(product, "model_name", "") or "",
                    ),
                },
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
        category_branches = [b for b in r.intent.branches if b.category != "*"]
        unrestricted = any(b.category == "*" for b in r.intent.branches)
        all_hidden = bool(category_branches) and all(
            b.id in r.view.hidden_branch_ids for b in category_branches
        )
        effective_title = ", ".join(
            branch_label(b) + (" · " + ", ".join(b.brands + b.models) if b.brands or b.models else "")
            for b in category_branches
        )
        if not category_branches:
            effective_title = "Все категории" if unrestricted else "В запросе не осталось категорий"
        qualifiers = r.intent.brands + r.intent.models
        if qualifiers:
            effective_title += " · " + ", ".join(qualifiers)
        notices = list(r.intent.identity_issues)
        notices.extend(f"Не удалось распознать товарную позицию «{word}». Уточните её название; остальные категории сохранены."
                       for word in r.intent.query_understanding.get("unresolved_products", []))
        notices.extend(f"Условие «{word}» не удалось проверить. Уточните характеристику товара."
                       for word in r.intent.query_understanding.get("unresolved_attributes", []))
        explanations = {
            "CATEGORY_NO_MATCH": "В текущем индексе нет предложений этой категории.",
            "BRAND_NO_MATCH": "Нет предложений с указанным брендом.",
            "COLOR_NO_MATCH": "Нет вариантов указанного цвета.",
            "MATERIAL_NO_MATCH": "Нет вариантов с указанными условиями материала.",
            "PRICE_AND_TECHNICAL_ATTRIBUTES_NO_MATCH": "Нет вариантов с указанной ценой или техническими характеристиками.",
            "PURPOSE_OR_MODEL_NO_MATCH": "Нет подтверждённого соответствия назначению или модели.",
            "UNRESOLVED_CONCEPT": "Товарное понятие не распознано. Уточните категорию.",
            "STOCK_UNKNOWN": "У подходящих предложений нет числовых данных об остатке; тираж подтвердить нельзя.",
            "QUANTITY_INSUFFICIENT": "Нет подтверждённого количества для указанного тиража; неизвестные остатки не считаются доступными.",
        }
        if not ids and not all_hidden:
            if rows:
                notices.append("Дополнительные фильтры исключили все найденные варианты. Измените фильтры.")
            else:
                for branch, diagnostic in zip(r.intent.branches, r.timings.get("identity_funnel", [])):
                    if message := explanations.get(diagnostic.get("zero_reason")):
                        notices.append(branch_label(branch) + ": " + message)
        if (
            not ids
            and all(b.category.startswith("dynamic:") for b in r.intent.branches)
            and re.fullmatch(r"[\w-]+", r.intent.raw_query)
        ):
            notices.append(
                f"Бренд, модель или категория «{r.intent.raw_query}» не подтверждены текущим индексом. Уточните запрос."
            )
        requested_brands = (
            r.intent.brands
            + f.brands
            + [v for b in r.intent.branches for v in b.brands]
            + [v for values in f.branch_brands.values() for v in values]
        )
        notices.extend(
            f"Бренд «{v}» не найден в текущем каталоге."
            for v in requested_brands
            if brand_key(v) not in registry.labels
        )
        requested_models = (
            r.intent.models
            + f.models
            + [v for b in r.intent.branches for v in b.models]
            + [v for values in f.branch_models.values() for v in values]
        )
        if not ids and requested_models:
            notices.append(
                f"Нет вариантов модели «{', '.join(dict.fromkeys(requested_models))}», соответствующих всем условиям. Расширьте поиск явной командой."
            )
        return {
            "id": r.id,
            "chat_id": r.chat_id,
            "intent": r.intent.model_dump(mode="json"),
            "effective_title": effective_title,
            "color_state": effective_color_state(r.intent, r.view.filters),
            "effective_filters": {
                **effective_filter_state(r, r.intent),
                "options": {
                    "colors": [str(c) for c in ColorGroup if c != ColorGroup.UNKNOWN],
                    "brands": list(registry.labels),
                    "materials": sorted({material for material, _ in choices if material}),
                    "sizes": sorted({size.upper() for _, size in choices if size}),
                },
            },
            "query_categories": [
                {
                    "id": b.id,
                    "concept": b.category if b.resolved is not False else None,
                    "label": branch_label(b),
                    "source_text": b.source_text or r.intent.original_query or r.intent.raw_query,
                    "order": i,
                    "hidden": b.id in r.view.hidden_branch_ids,
                    "count": branch_counts[i],
                    "resolved": b.resolved is not False,
                    "constraints": b.model_dump(mode="json", exclude={"id", "source_text", "label"}),
                }
                for i, b in enumerate(r.intent.branches)
                if b.category != "*"
            ],
            "categories_unrestricted": unrestricted,
            "category_notice": removal_notice(r),
            "eligible_total": len(eligible_rows),
            "visible_total": len(ids),
            "brand_labels": registry.labels,
            "identity_notices": list(dict.fromkeys(notices)),
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
            "result_state": "ALL_CATEGORIES_HIDDEN"
            if all_hidden
            else "NO_QUERY_CATEGORIES"
            if not r.intent.branches
            else "INDEX_RESULTS"
            if ids
            else "COMPLETED_NO_MATCHES",
            "empty_branches": [
                branch_label(b)
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
