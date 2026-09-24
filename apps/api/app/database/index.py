"""Latest observations, text retrieval, and a persistent supplier-leased queue."""

import hashlib
import json
from datetime import timedelta

from sqlalchemy import and_, delete, func, or_, select, update

from app.application.research_analysis import PREFERENCE_TERMS, ProductGroupingService, facts
from app.database.models import (
    AvailabilityObservation,
    CatalogSyncJob,
    IndexedProduct,
    ProductConcept,
    ProductConceptOffer,
    ProductObservation,
    SupplierOffer,
    SupplierSyncState,
    TaxonomySuggestion,
)
from app.domain.models import Product, utcnow
from app.domain.services import ColorNormalizer, CurrencyService, normalize_text
from app.domain.source_urls import validated_product_url
from app.domain.taxonomy import QueryVocabulary, UniversalCategoryResolver, terms


class IndexRepository:
    def __init__(self, sessions, settings):
        self.sessions, self.settings = sessions, settings

    def insert(self, db, model):
        if db.bind.dialect.name == "postgresql":
            from sqlalchemy.dialects.postgresql import insert
        else:
            from sqlalchemy.dialects.sqlite import insert
        return insert(model)

    async def ingest(self, products):
        summary = {"products_changed": 0, "products_unchanged": 0,
                   "observations_created": 0, "observations_deduplicated": 0}
        async with self.sessions() as db, db.begin():
            for product in products:
                if product.category:
                    product.metadata.setdefault('supplier_category', product.category)
                product.colors = [ColorNormalizer().normalize(c.original_color) for c in product.colors]
                if product.original_price is not None and product.original_currency:
                    product.price_kzt = CurrencyService(self.settings.rub_kzt_rate).to_kzt(
                        product.original_price, product.original_currency
                    )
                resolved = UniversalCategoryResolver().resolve(product.name)
                product.category = resolved[0] if resolved else None
                label = str(product.metadata.get('supplier_category') or '')
                if not resolved and label:
                    suggestion_id = hashlib.sha256((product.supplier + ':' + label).encode()).hexdigest()
                    suggestion = await db.get(TaxonomySuggestion, suggestion_id)
                    if suggestion is None:
                        db.add(TaxonomySuggestion(id=suggestion_id, supplier=product.supplier,
                            supplier_label=label, supplier_path=str(product.metadata.get('breadcrumbs', '')),
                            samples=[product.id], confidence=0, status='PENDING_REVIEW'))
                    elif product.id not in suggestion.samples and len(suggestion.samples) < 5:
                        suggestion.samples = suggestion.samples + [product.id]
                breadcrumbs = product.metadata.get("breadcrumbs", [])
                if isinstance(breadcrumbs, str):
                    breadcrumbs = [part.strip() for part in breadcrumbs.split(">") if part.strip()]
                concept_labels = list(dict.fromkeys(
                    [str(product.metadata.get("supplier_category") or ""), *map(str, breadcrumbs)]
                ))
                concept_labels = [label for label in concept_labels if label]
                product.metadata["product_concepts"] = concept_labels
                document = ' '.join([product.name, product.name, product.name, facts(product),
                    product.category or '', *concept_labels, *concept_labels,
                    product.dimensions or '', product.capacity or '',
                    str(product.metadata.get('supplier_category','')),
                    str(product.metadata.get('breadcrumbs','')), str(product.metadata.get('supplier_keywords','')),
                    *[c.original_color for c in product.colors]])
                product.metadata['semantic_document'] = document[:2400]
                tags = [
                    key for key, patterns in PREFERENCE_TERMS.items() if any(t in document for t in patterns)
                ]
                product.metadata["semantic_tags"] = {"values": tags, "provenance": "RULE_BASED"}
                key = ProductGroupingService().groups([product])[0]["id"]
                stable = product.model_dump(
                    mode="json",
                    exclude={
                        "stock_quantity",
                        "incoming_quantity",
                        "reserved_quantity",
                        "total_quantity",
                        "incoming_date",
                        "availability_status",
                        "incoming_status",
                        "source_availability_payload",
                        "availability_parser_version",
                        "availability_records",
                        "price_kzt",
                        "original_price",
                        "original_currency",
                        "fetched_at",
                    },
                )
                values = dict(
                    id=key,
                    name=product.name,
                    category=product.category,
                    search_document=document,
                    metadata_payload={"tags": tags, "tag_provenance": "RULE_BASED"},
                )
                stmt = self.insert(db, IndexedProduct).values(**values)
                await db.execute(
                    stmt.on_conflict_do_update(
                        index_elements=["id"], set_={k: v for k, v in values.items() if k != "id"}
                    )
                )
                concept_ids = []
                for display_name in concept_labels:
                    canonical = normalize_text(display_name)
                    concept_id = hashlib.sha256(canonical.encode()).hexdigest()
                    concept_values = dict(
                        id=concept_id, canonical_name=canonical, display_name=display_name,
                        aliases=[display_name],
                        evidence={"supplier": product.supplier, "source": "catalog_navigation"},
                        products_count=0, last_seen=product.fetched_at,
                    )
                    stmt = self.insert(db, ProductConcept).values(**concept_values)
                    await db.execute(stmt.on_conflict_do_update(
                        index_elements=["id"],
                        set_={"display_name": display_name, "last_seen": product.fetched_at},
                    ))
                    concept_ids.append(concept_id)
                metadata_hash = hashlib.sha256(json.dumps(
                    stable, sort_keys=True, ensure_ascii=False, default=str
                ).encode()).hexdigest()
                commercial = {
                    "price": str(product.original_price) if product.original_price is not None else None,
                    "currency": product.original_currency,
                    "stock": product.stock_quantity,
                    "incoming": product.incoming_quantity,
                    "reserved": product.reserved_quantity,
                    "total": product.total_quantity,
                    "incoming_at": product.incoming_date.isoformat() if product.incoming_date else None,
                    "availability": product.availability_status,
                }
                commercial_hash = hashlib.sha256(json.dumps(
                    commercial, sort_keys=True, ensure_ascii=False
                ).encode()).hexdigest()
                current_offer = await db.get(SupplierOffer, product.id)
                changed = current_offer is None or current_offer.commercial_hash != commercial_hash
                summary["products_changed" if changed else "products_unchanged"] += 1
                summary["observations_created" if changed else "observations_deduplicated"] += 1
                values = dict(
                    id=product.id,
                    product_id=key,
                    supplier=product.supplier,
                    source_url=validated_product_url(product.supplier, product.source_url) or "",
                    payload=stable,
                    metadata_hash=metadata_hash,
                    commercial_hash=commercial_hash,
                    unchanged_refreshes=0 if changed else (current_offer.unchanged_refreshes + 1),
                    last_commercial_change_at=(product.fetched_at if changed else current_offer.last_commercial_change_at),
                )
                stmt = self.insert(db, SupplierOffer).values(**values)
                await db.execute(
                    stmt.on_conflict_do_update(
                        index_elements=["id"], set_={k: v for k, v in values.items() if k != "id"}
                    )
                )
                for concept_id in concept_ids:
                    await db.execute(self.insert(db, ProductConceptOffer).values(
                        concept_id=concept_id, offer_id=product.id,
                    ).on_conflict_do_nothing())
                    count = await db.scalar(select(func.count()).select_from(ProductConceptOffer).where(
                        ProductConceptOffer.concept_id == concept_id
                    ))
                    await db.execute(update(ProductConcept).where(ProductConcept.id == concept_id).values(
                        products_count=count
                    ))
                records = list(product.availability_records)
                if not records and (
                    product.stock_quantity is not None or product.incoming_quantity is not None
                ):
                    from app.domain.models import AvailabilityRecord

                    source = product.metadata.get('stock_source') or product.evidence.get(
                        'stock_quantity', {}
                    ).get('source') or "normalized product availability"
                    if product.stock_quantity is not None:
                        records.append(AvailabilityRecord(
                            state='ON_HAND', free_quantity=product.stock_quantity,
                            observed_at=product.fetched_at,
                            parser_version=product.availability_parser_version,
                            source_label=str(source), source_evidence={'value': product.stock_quantity},
                            confidence="MEDIUM",
                        ))
                    if product.incoming_quantity is not None:
                        records.append(AvailabilityRecord(
                            state="INCOMING", free_quantity=product.incoming_quantity,
                            expected_at=product.incoming_date, observed_at=product.fetched_at,
                            parser_version=product.availability_parser_version,
                            source_label="normalized product incoming",
                            source_evidence={"value": product.incoming_quantity},
                            confidence="MEDIUM",
                        ))
                # Replace the latest normalized set atomically. A failed
                # transaction keeps the previous observation set available.
                if records:
                    await db.execute(
                        delete(AvailabilityObservation).where(
                            AvailabilityObservation.offer_id == product.id
                        )
                    )
                for record in records:
                    identity = '|'.join(map(str, (
                        product.id, record.location_code, record.state,
                        record.expected_at.isoformat() if record.expected_at else '', record.source_label,
                    )))
                    values = dict(id=hashlib.sha256(identity.encode()).hexdigest(),
                                  offer_id=product.id, **record.model_dump(mode='python'))
                    stmt = self.insert(db, AvailabilityObservation).values(**values)
                    await db.execute(stmt.on_conflict_do_update(
                        index_elements=['id'], set_={k: v for k, v in values.items() if k != 'id'}
                    ))
                observation = product.model_dump(
                    mode="json",
                    include={
                        "stock_quantity",
                        "price_kzt",
                        "original_price",
                        "original_currency",
                        "fetched_at",
                    },
                )
                observation['conversion_rate'] = str(self.settings.rub_kzt_rate) if product.original_currency == 'RUB' else '1'
                values = dict(
                    offer_id=product.id,
                    price_kzt=product.price_kzt,
                    stock=product.stock_quantity,
                    incoming=product.incoming_quantity,
                    reserved=product.reserved_quantity,
                    total=product.total_quantity,
                    incoming_at=product.incoming_date,
                    availability_status=product.availability_status,
                    incoming_status=product.incoming_status,
                    parser_version=product.availability_parser_version,
                    source_availability=(product.source_availability_payload or (
                        {"available_now": product.stock_quantity,
                         "source": product.metadata.get("stock_source")}
                        if product.stock_quantity is not None else {}
                    )),
                    observed_at=product.fetched_at,
                    last_verified_at=product.fetched_at,
                    expires_at=product.fetched_at
                    + timedelta(minutes=self.settings.observation_refresh_minutes),
                    status="FRESH",
                    payload=observation,
                )
                stmt = self.insert(db, ProductObservation).values(**values)
                await db.execute(
                    stmt.on_conflict_do_update(
                        index_elements=["offer_id"], set_={k: v for k, v in values.items() if k != "offer_id"}
                    )
                )
        return summary

    async def candidates(self, intent):
        async with self.sessions() as db:
            stmt = (
                select(SupplierOffer.id, SupplierOffer.payload, ProductObservation)
                .join(ProductObservation, ProductObservation.offer_id == SupplierOffer.id)
                .join(IndexedProduct, IndexedProduct.id == SupplierOffer.product_id)
            )
            known = [c for c in intent.categories if not c.startswith("dynamic:")]
            criteria = [IndexedProduct.category.in_(known)] if known else []
            # Taxonomy is evidence, not a gate. Even a known broad category such
            # as accessory must retain exact supplier text matches.
            query = UniversalCategoryResolver().concept(intent.raw_query)
            query_forms = [query] + [
                " ".join(alias) for alias in QueryVocabulary.alternatives(terms(query))
            ]
            if db.bind.dialect.name == "postgresql":
                lexical = or_(*[
                    func.to_tsvector("russian", IndexedProduct.search_document).op("@@")(
                        func.plainto_tsquery("russian", form)
                    ) for form in query_forms if form
                ])
                literal = and_(*(IndexedProduct.search_document.ilike(f"%{t}%") for t in terms(query)))
                fuzzy = IndexedProduct.name.op("%") (query)
                criteria.append(or_(lexical, literal, fuzzy))
            else:
                criteria.append(
                    and_(
                        *(
                            or_(
                                IndexedProduct.search_document.contains(t, autoescape=True),
                                IndexedProduct.search_document.contains(t[:4], autoescape=True),
                                IndexedProduct.search_document.contains(t[:3], autoescape=True),
                            )
                            for t in terms(query)
                        )
                    )
                )
            stmt = stmt.where(or_(*criteria), SupplierOffer.lifecycle != 'REMOVED')
            # Commercial freshness is a hard constraint only when the user asks
            # for a confirmed quantity. Stable product metadata remains searchable
            # while an observation is stale; the API marks that observation stale.
            if intent.budget:
                stmt = stmt.where(ProductObservation.price_kzt.is_not(None))
                if intent.budget.min is not None:
                    stmt = stmt.where(ProductObservation.price_kzt >= intent.budget.min)
                if intent.budget.max is not None:
                    stmt = stmt.where(ProductObservation.price_kzt <= intent.budget.max)
            rows = (await db.execute(stmt)).all()
            offer_ids = [offer_id for offer_id, _, _ in rows]
            availability = {}
            if offer_ids:
                availability_rows = (
                    await db.scalars(
                        select(AvailabilityObservation).where(
                            AvailabilityObservation.offer_id.in_(offer_ids)
                        )
                    )
                ).all()
                for record in availability_rows:
                    availability.setdefault(record.offer_id, []).append(
                        {
                            "location_code": record.location_code,
                            "location_label": record.location_label,
                            "state": record.state,
                            "total_quantity": record.total_quantity,
                            "free_quantity": record.free_quantity,
                            "reserved_quantity": record.reserved_quantity,
                            "expected_at": record.expected_at,
                            "lead_time_min_days": record.lead_time_min_days,
                            "lead_time_max_days": record.lead_time_max_days,
                            "observed_at": record.observed_at,
                            "parser_version": record.parser_version,
                            "source_label": record.source_label,
                            "source_evidence": record.source_evidence,
                            "confidence": record.confidence,
                        }
                    )
            result = []
            now = utcnow()
            for offer_id, stable, observation in rows:
                payload = dict(stable | observation.payload | {
                    "availability_records": availability.get(offer_id, [])
                })
                metadata = dict(payload.get("metadata", {}))
                expires = observation.expires_at.replace(tzinfo=now.tzinfo)
                metadata["availability_stale"] = observation.status != "FRESH" or expires <= now
                payload["metadata"] = metadata
                result.append(Product.model_validate(payload))
            return result

    async def offers(self, supplier):
        async with self.sessions() as db:
            return [
                (r.id, r.source_url)
                for r in (
                    await db.scalars(select(SupplierOffer).where(SupplierOffer.supplier == supplier))
                ).all()
            ]

    async def mark_usage(self, offer_ids, event="searched"):
        """Record refresh-priority signals without changing relevance."""
        if not offer_ids:
            return
        field = {"searched": "last_searched_at", "opened": "last_opened_at",
                 "selected": "last_selected_at"}[event]
        values = {field: utcnow()}
        if event == "searched":
            values["research_hit_count"] = SupplierOffer.research_hit_count + 1
        async with self.sessions() as db, db.begin():
            await db.execute(update(SupplierOffer).where(SupplierOffer.id.in_(offer_ids)).values(**values))

    async def adaptive_refresh_candidates(self, supplier, offer_ids=None, limit=None):
        """Return due HOT/WARM/COLD URLs; DORMANT offers wait for catalog sync."""
        now = utcnow()
        async with self.sessions() as db:
            stmt = (select(SupplierOffer, ProductObservation)
                    .outerjoin(ProductObservation, ProductObservation.offer_id == SupplierOffer.id)
                    .where(SupplierOffer.supplier == supplier, SupplierOffer.lifecycle != "REMOVED"))
            if offer_ids:
                stmt = stmt.where(SupplierOffer.id.in_(offer_ids))
            rows = (await db.execute(stmt)).all()
        due, counts = [], {"HOT": 0, "WARM": 0, "COLD": 0, "DORMANT": 0}
        for offer, observation in rows:
            signals = [x for x in (offer.last_searched_at, offer.last_opened_at, offer.last_selected_at) if x]
            used = max((x.replace(tzinfo=now.tzinfo) for x in signals), default=None)
            if used and used >= now - timedelta(hours=self.settings.hot_refresh_hours):
                tier, hours = "HOT", self.settings.hot_refresh_hours
            elif used and used >= now - timedelta(days=self.settings.warm_usage_days):
                tier, hours = "WARM", self.settings.warm_refresh_hours
            elif not used or used >= now - timedelta(days=self.settings.dormant_usage_days):
                tier, hours = "COLD", self.settings.cold_refresh_hours
            else:
                tier, hours = "DORMANT", None
            counts[tier] += 1
            verified = observation.last_verified_at if observation and observation.last_verified_at else (
                observation.observed_at if observation else None
            )
            verified = verified.replace(tzinfo=now.tzinfo) if verified else None
            if tier != "DORMANT" and (not verified or verified <= now - timedelta(hours=hours)):
                due.append((offer.id, offer.source_url, tier, verified))
        due.sort(key=lambda item: ({"HOT": 0, "WARM": 1, "COLD": 2}[item[2]], item[3] or now - timedelta(days=9999)))
        return due[:limit] if limit else due, counts

    async def source_urls_known(self, supplier, urls):
        if not urls:
            return set()
        async with self.sessions() as db:
            return set(await db.scalars(select(SupplierOffer.source_url).where(
                SupplierOffer.supplier == supplier, SupplierOffer.source_url.in_(urls)
            )))

    async def observation_status(self, supplier, status):
        async with self.sessions() as db, db.begin():
            await db.execute(
                update(ProductObservation)
                .where(
                    ProductObservation.offer_id.in_(
                        select(SupplierOffer.id).where(SupplierOffer.supplier == supplier)
                    )
                )
                .values(status=status)
            )

    async def states(self):
        async with self.sessions() as db:
            rows = (await db.scalars(select(SupplierSyncState))).all()
            return [
                {"supplier": r.supplier, "error": r.error, "next_refresh_at": r.next_refresh_at.isoformat()}
                for r in rows
            ]

    async def enqueue(self, supplier, kind, checkpoint=None, priority=0, available_at=None):
        checkpoint = checkpoint or {}
        key = hashlib.sha256(f"{supplier}:{kind}:{checkpoint.get('query', '')}".encode()).hexdigest()
        async with self.sessions() as db, db.begin():
            await db.execute(
                self.insert(db, SupplierSyncState).values(supplier=supplier).on_conflict_do_nothing()
            )
            equivalent = {
                "FULL_CATALOG_RECONCILIATION": {"FULL_CATALOG_RECONCILIATION", "CATALOG_DISCOVERY"},
                "CATALOG_DISCOVERY": {"FULL_CATALOG_RECONCILIATION", "CATALOG_DISCOVERY"},
                "AVAILABILITY_REFRESH": {"AVAILABILITY_REFRESH", "OBSERVATION_REFRESH"},
                "OBSERVATION_REFRESH": {"AVAILABILITY_REFRESH", "OBSERVATION_REFRESH"},
            }.get(kind, {kind})
            active_query = select(CatalogSyncJob).where(
                CatalogSyncJob.supplier == supplier,
                CatalogSyncJob.kind.in_(equivalent),
                CatalogSyncJob.status.in_(("PENDING", "RUNNING")),
            )
            if len(equivalent) == 1:
                active_query = active_query.where(CatalogSyncJob.dedup_key == key)
            active = await db.scalar(
                active_query.order_by(CatalogSyncJob.updated_at.desc()).with_for_update()
            )
            if active:
                existing = dict(active.checkpoint or {})
                parser_changed = checkpoint.get("parser_version") and checkpoint.get(
                    "parser_version"
                ) != existing.get("parser_version")
                if active.error == "PARSER_ANOMALY" and not parser_changed:
                    return active.id
                incoming = checkpoint.get("pending", [])
                if incoming:
                    existing["pending"] = list(dict.fromkeys([*existing.get("pending", []), *incoming]))
                existing["requested_at"] = utcnow().isoformat()
                existing["reason"] = checkpoint.get("reason", existing.get("reason", "coalesced"))
                if parser_changed:
                    existing["parser_version"] = checkpoint["parser_version"]
                    active.error = None
                    active.status = "PENDING"
                active.checkpoint = existing
                active.priority = max(active.priority, priority)
                requested = available_at or utcnow()
                current = active.available_at
                if current.tzinfo is None:
                    current = current.replace(tzinfo=requested.tzinfo)
                active.available_at = requested if parser_changed else min(current, requested)
                return active.id
            job = None
            if len(equivalent) > 1:
                job = await db.scalar(select(CatalogSyncJob).where(
                    CatalogSyncJob.supplier == supplier,
                    CatalogSyncJob.kind.in_(equivalent),
                ).order_by(CatalogSyncJob.updated_at.desc()).with_for_update())
            if job is None:
                values = dict(
                    supplier=supplier,
                    kind=kind,
                    dedup_key=key,
                    checkpoint=checkpoint,
                    metrics={},
                    priority=priority,
                    available_at=available_at or utcnow(),
                    status="PENDING",
                )
                await db.execute(self.insert(db, CatalogSyncJob).values(**values).on_conflict_do_nothing())
                job = await db.scalar(
                    select(CatalogSyncJob).where(CatalogSyncJob.dedup_key == key).with_for_update()
                )
            if job.status == 'PENDING' and priority > job.priority:
                job.priority, job.available_at = priority, available_at or utcnow()
            if (job.status == "PENDING" and kind == "TARGETED_RESEARCH"
                    and not (job.checkpoint or {}).get("branches")
                    and checkpoint.get("category_graph_hit")):
                # A newer interactive request may carry a graph hit that the
                # older zero-result job did not know yet.
                job.checkpoint = checkpoint
            parser_changed = checkpoint.get("parser_version") and checkpoint.get("parser_version") != (
                job.checkpoint or {}
            ).get("parser_version")
            quarantined_same_parser = job.error == "PARSER_ANOMALY" and not parser_changed
            if ((job.status in ("COMPLETED", "FAILED") or job.status == "CANCELLED" and priority > 0)
                    and not quarantined_same_parser):
                job.status, job.checkpoint, job.metrics = "PENDING", checkpoint, {}
                job.error = None
                job.available_at, job.priority = available_at or utcnow(), priority
            return job.id

    async def category_route(self, supplier, query):
        """Find an observed catalog branch; never invent a supplier URL."""
        wanted = set(terms(UniversalCategoryResolver().concept(query)))
        if not wanted:
            return None
        async with self.sessions() as db:
            jobs = (await db.scalars(
                select(CatalogSyncJob)
                .where(CatalogSyncJob.supplier == supplier)
                .order_by(CatalogSyncJob.updated_at.desc())
            )).all()
        best = None
        for job in jobs:
            checkpoint = job.checkpoint or {}
            nodes = list(checkpoint.get("roots", [])) + list(checkpoint.get("branches", []))
            for node in nodes:
                route = node.get("url") or node.get("route")
                label = " ".join(str(node.get(k, "")) for k in ("label", "category", "query"))
                observed = set(terms(label))
                if not route or not observed:
                    continue
                overlap = sum(any(a.startswith(b) or b.startswith(a) for b in observed) for a in wanted)
                score = overlap / len(wanted)
                if score and (best is None or score > best[0]):
                    best = (score, route, label)
        if best and best[0] >= 0.75:
            return {"route": best[1], "label": best[2], "confidence": best[0]}
        return None

    async def claim(self, owner, job_ids=None, kinds=None):
        now = utcnow()
        async with self.sessions() as db, db.begin():
            # Lease recovery is safe across processes; a second process never steals a live lease.
            expired = select(SupplierSyncState.supplier).where(SupplierSyncState.lease_until < now)
            await db.execute(
                update(CatalogSyncJob)
                .where(CatalogSyncJob.status == "RUNNING", CatalogSyncJob.supplier.in_(expired))
                .values(status="PENDING")
            )
            queued = (
                select(CatalogSyncJob)
                .where(CatalogSyncJob.status == "PENDING", CatalogSyncJob.available_at <= now)
                .order_by(CatalogSyncJob.priority.desc(), CatalogSyncJob.available_at)
            )
            if job_ids is not None:
                queued = queued.where(CatalogSyncJob.id.in_(job_ids))
            if kinds is not None:
                queued = queued.where(CatalogSyncJob.kind.in_(kinds))
            jobs = (await db.scalars(queued.with_for_update(skip_locked=True))).all()
            for job in jobs:
                changed = await db.execute(
                    update(SupplierSyncState)
                    .where(
                        SupplierSyncState.supplier == job.supplier,
                        SupplierSyncState.lease_until < now,
                        SupplierSyncState.retry_after <= now,
                    )
                    .values(
                        lease_owner=owner,
                        lease_until=now + timedelta(seconds=self.settings.research_job_timeout_seconds + 60),
                    )
                )
                if changed.rowcount:
                    job.status, job.updated_at = "RUNNING", now
                    await db.flush()
                    data = {k: getattr(job, k) for k in (
                        "id", "supplier", "kind", "checkpoint", "metrics", "priority", "available_at"
                    )}
                    data["metrics"] = dict(data["metrics"] or {})
                    queued_at = job.available_at
                    if queued_at.tzinfo is None:
                        queued_at = queued_at.replace(tzinfo=now.tzinfo)
                    data["metrics"].setdefault(
                        "targeted_job_queue_ms",
                        round(max(0, (now - queued_at).total_seconds() * 1000), 2),
                    )
                    if job.kind == "TARGETED_RESEARCH":
                        data["metrics"].setdefault(
                            "targeted_job_start_ms", data["metrics"]["targeted_job_queue_ms"]
                        )
                    return data
        return None

    async def checkpoint(self, job_id, checkpoint, metrics):
        async with self.sessions() as db, db.begin():
            await db.execute(
                update(CatalogSyncJob)
                .where(CatalogSyncJob.id == job_id)
                .values(checkpoint=checkpoint, metrics=metrics, updated_at=utcnow())
            )

    async def cancelled(self, job_id):
        async with self.sessions() as db:
            return (await db.get(CatalogSyncJob, job_id)).status == "CANCELLED"

    async def higher_priority_pending(self, priority):
        async with self.sessions() as db:
            return bool(await db.scalar(
                select(CatalogSyncJob.id).where(
                    CatalogSyncJob.status == "PENDING",
                    CatalogSyncJob.available_at <= utcnow(),
                    CatalogSyncJob.priority > priority,
                ).limit(1)
            ))

    async def active_job(self, supplier, kinds):
        async with self.sessions() as db:
            return await db.scalar(select(CatalogSyncJob).where(
                CatalogSyncJob.supplier == supplier,
                CatalogSyncJob.kind.in_(kinds),
                CatalogSyncJob.status.in_(("PENDING", "RUNNING")),
            ).order_by(CatalogSyncJob.updated_at.desc()).limit(1))

    async def cancel(self, job_id):
        async with self.sessions() as db, db.begin():
            await db.execute(
                update(CatalogSyncJob).where(CatalogSyncJob.id == job_id).values(status="CANCELLED")
            )

    async def finish(self, job, owner, status, error=None):
        now = utcnow()
        async with self.sessions() as db, db.begin():
            state = await db.get(SupplierSyncState, job["supplier"])
            if state.lease_owner != owner:
                return
            supplier_error = error and error != "TIME_BUDGET"
            state.failures = state.failures + 1 if supplier_error else 0
            base = self.settings.supplier_failure_backoff_minutes * 60
            ceiling = self.settings.supplier_failure_backoff_max_hours * 3600
            delay = min(ceiling, base * 2 ** max(0, state.failures - 1)) if supplier_error else 5
            if error == "SUPPLIER_CAPTCHA_REQUIRED":
                delay = 3600
            if error == "PARSER_ANOMALY":
                delay = ceiling
            if error == "SUPPLIER_HTTP_ERROR" and state.failures >= self.settings.supplier_circuit_failure_threshold:
                state.circuit_open_until = now + timedelta(seconds=delay)
                error = "TEMPORARILY_UNAVAILABLE"
            elif not error:
                state.circuit_open_until = None
            state.error, state.lease_owner, state.lease_until = error, None, now
            state.retry_after = now + timedelta(seconds=delay) if error else now
            row = await db.get(CatalogSyncJob, job["id"])
            if row.status != "CANCELLED":
                row.status, row.error, row.available_at = status, error, now + timedelta(seconds=delay)
            if status == "COMPLETED":
                if job["kind"] in ("OBSERVATION_REFRESH", "AVAILABILITY_REFRESH", "TARGETED_REFRESH"):
                    state.next_refresh_at = now + timedelta(minutes=self.settings.observation_refresh_minutes)
                if job["kind"] == "INCREMENTAL_CATALOG_SYNC":
                    state.next_incremental_at = now + timedelta(hours=self.settings.incremental_sync_hours)
                if job["kind"] in ("CATALOG_DISCOVERY", "FULL_CATALOG_RECONCILIATION"):
                    state.next_discovery_at = now + timedelta(days=self.settings.full_reconciliation_days)
                    state.next_reconciliation_at = state.next_discovery_at
            row.updated_at = now
