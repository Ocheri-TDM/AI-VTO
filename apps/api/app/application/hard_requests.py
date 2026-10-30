import asyncio
import hashlib
import time
from collections import OrderedDict, defaultdict, deque
from contextlib import asynccontextmanager
from decimal import Decimal
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import delete, func, select, update
from sqlalchemy import text as sql_text

from app.ai.local import ModelUnavailable
from app.application.availability import ProcurementAvailabilityService
from app.database.models import (
    Chat,
    HardRequestCandidate,
    HardRequestCase,
    HardRequestPosition,
    HardRequestRecord,
    IndexedProduct,
    Message,
    ProductConcept,
    ProductObservation,
    Project,
    ProjectQuery,
    SupplierOffer,
)
from app.domain.hard_request import (
    HardRequestIntent,
    PositionConstraint,
    deterministic_hard_request,
    evaluate_intent_completeness,
)
from app.domain.intent_mutation import IntentMutationService, PatchOperation
from app.domain.models import utcnow
from app.domain.source_urls import validated_product_url
from app.domain.taxonomy import CategoryMatch, ProductCategoryMatcher


def _id():
    return str(uuid4())


class HardRequestService:
    """Archived compatibility implementation; never registered by create_app.

    Kept for historical-format regression coverage. Public assembly writes are
    retired (410); new requests use ProjectQueryService and product retrieval.
    """

    def __init__(self, sessions, research, index, model=None, query_understanding=None):
        self.sessions, self.research, self.index, self.model = sessions, research, index, model
        self.tasks: dict[str, asyncio.Task] = {}
        self.intent_cache: OrderedDict[str, dict] = OrderedDict()
        self.query_understanding = query_understanding
        self.intent_mutations = IntentMutationService()
        self.mutation_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def parse(self, text: str) -> HardRequestIntent:
        parse_started = time.perf_counter()
        cache_key = " ".join(text.casefold().split())
        cached = self.intent_cache.get(cache_key)
        if cached is not None:
            self.intent_cache.move_to_end(cache_key)
            return HardRequestIntent.model_validate(cached)
        understanding = (
            await self.query_understanding.understand(text)
            if self.query_understanding
            else None
        )
        # Composite parsing must keep the user's exact UTF-8 text. Query
        # Understanding is useful evidence for retrieval, but an LLM-produced
        # rewrite must never become the source of truth for set counts, role
        # scope, or explicitly named positions.
        understood_text = understanding.search_query if understanding else text
        parsed_text = text
        composite_started = time.perf_counter()
        deterministic = deterministic_hard_request(parsed_text)
        # A safe correction may replace the raw parse only when it actually
        # resolves an otherwise incomplete intent. This prevents a rewrite from
        # dropping positions or set counts that the raw parser already proved.
        if understood_text != text and "\ufffd" not in understood_text:
            raw_state = evaluate_intent_completeness(deterministic, text, 1.0)
            corrected = deterministic_hard_request(understood_text)
            corrected_state = evaluate_intent_completeness(corrected, understood_text, 1.0)
            if raw_state.requires_llm and not corrected_state.requires_llm:
                parsed_text, deterministic = understood_text, corrected
        composite_parse_ms = round((time.perf_counter() - composite_started) * 1000, 2)
        deterministic.original_query = text
        deterministic.normalized_query = parsed_text
        deterministic.query_understanding = (
            understanding.model_dump(mode="json") if understanding else {}
        )
        deterministic.query_understanding["composite_parse_ms"] = composite_parse_ms
        completeness_started = time.perf_counter()
        completeness = evaluate_intent_completeness(
            deterministic,
            parsed_text,
            understanding.confidence if understanding else 1.0,
        )
        deterministic.query_understanding["intent_completeness"] = completeness.model_dump(
            mode="json"
        )
        deterministic.query_understanding["escalation_decision_ms"] = round(
            (time.perf_counter() - completeness_started) * 1000, 2
        )
        deterministic.query_understanding.setdefault("llm_used", False)
        deterministic.query_understanding.setdefault("llm_latency_ms", None)
        deterministic.query_understanding.setdefault("llm_validation_result", "NOT_USED")
        deterministic.query_understanding["llm_reason"] = completeness.llm_reason
        use_model = completeness.requires_llm
        if use_model and self.model and hasattr(self.model, "complete"):
            llm_started = time.perf_counter()
            try:
                inferred = await self.model.complete(
                    [
                        {
                            "role": "system",
                            "content": "Parse one procurement request. Preserve explicitly listed category labels verbatim. Never invent categories, products, stock, prices or SKUs.",
                        },
                        {"role": "user", "content": parsed_text},
                    ],
                    HardRequestIntent,
                )
                # Explicit numeric and category evidence in the user's text is
                # authoritative. The model may enrich ambiguity/material fields,
                # but it cannot omit or replace the user's list.
                if deterministic.categories:
                    inferred.categories = deterministic.categories
                    inferred.positions = deterministic.positions
                    inferred.category_mode = "EXPLICIT"
                if deterministic.declared_categories_count is not None:
                    inferred.declared_categories_count = deterministic.declared_categories_count
                inferred.target_sets = deterministic.target_sets
                inferred.quantity = deterministic.quantity
                if deterministic.price_max is not None:
                    inferred.price_max = deterministic.price_max
                inferred.color = deterministic.color
                inferred.exclusions = deterministic.exclusions
                inferred.excluded_materials = deterministic.excluded_materials
                inferred.excluded_colors = deterministic.excluded_colors
                inferred.semantic_preferences = deterministic.semantic_preferences
                inferred.sort_preferences = deterministic.sort_preferences
                inferred.materials = deterministic.materials
                inferred.request_mode = deterministic.request_mode
                if deterministic.purpose:
                    inferred.purpose = deterministic.purpose
                    inferred.audience = deterministic.audience
                    if not deterministic.categories:
                        inferred.categories = []
                        inferred.positions = []
                        inferred.category_mode = "AUTO_DISCOVER"
                        inferred.request_mode = "SIMPLE"
                inferred.unsupported_constraints = deterministic.unsupported_constraints
                inferred.items_per_position = deterministic.items_per_position
                inferred.require_cross_set_uniqueness = deterministic.require_cross_set_uniqueness
                inferred.candidates_per_position = 3
                inferred.intent_parser_version = "composite-intent-v2"
                inferred.case_assembler_version = "case-assembler-v2"
                inferred.availability_policy = deterministic.availability_policy
                inferred.availability_policy_version = "last-known-stock-v3"
                inferred.original_query = text
                inferred.normalized_query = parsed_text
                inferred.query_understanding = deterministic.query_understanding
                inferred.query_understanding["llm_used"] = True
                inferred.query_understanding["llm_reason"] = completeness.llm_reason
                inferred.query_understanding["llm_latency_ms"] = round(
                    (time.perf_counter() - llm_started) * 1000, 2
                )
                inferred.query_understanding["llm_validation_result"] = "ACCEPTED_WITH_DETERMINISTIC_GUARDS"
                inferred.query_understanding["query_understanding_ms"] = round(
                    (time.perf_counter() - parse_started) * 1000, 2
                )
                result = HardRequestIntent.model_validate(inferred.model_dump())
                self._cache_intent(cache_key, result)
                return result
            except (ModelUnavailable, ValidationError, ValueError) as exc:
                deterministic.query_understanding["llm_used"] = True
                deterministic.query_understanding["llm_reason"] = completeness.llm_reason
                deterministic.query_understanding["llm_latency_ms"] = round(
                    (time.perf_counter() - llm_started) * 1000, 2
                )
                deterministic.query_understanding["llm_validation_result"] = type(exc).__name__
        deterministic.query_understanding["query_understanding_ms"] = round(
            (time.perf_counter() - parse_started) * 1000, 2
        )
        self._cache_intent(cache_key, deterministic)
        return deterministic

    def _cache_intent(self, key: str, value: HardRequestIntent) -> None:
        self.intent_cache[key] = value.model_dump(mode="json")
        self.intent_cache.move_to_end(key)
        while len(self.intent_cache) > 128:
            self.intent_cache.popitem(last=False)

    async def create_standard_query(self, project_id, text, user_id):
        async with self.sessions() as db:
            row = (
                await db.execute(
                    select(Project, Chat)
                    .join(Chat, Chat.project_id == Project.id)
                    .where(Project.id == project_id, Project.user_id == user_id)
                )
            ).first()
        if not row:
            return None
        project, chat = row
        research = await self.research.start(chat.id, text)
        understanding = research.intent.query_understanding
        now, query_id = utcnow(), _id()
        async with self.sessions.begin() as db:
            await db.execute(
                update(ProjectQuery)
                .where(
                    ProjectQuery.project_id == project_id,
                    ProjectQuery.status == "ACTIVE",
                )
                .values(status="ARCHIVED", updated_at=now)
            )
            db.add(
                ProjectQuery(
                    id=query_id,
                    project_id=project_id,
                    research_session_id=research.id,
                    type="STANDARD",
                    query_text=text,
                    normalized_query=research.intent.normalized_query,
                    query_understanding=understanding,
                    status="ACTIVE",
                    created_at=now,
                    updated_at=now,
                )
            )
            current = await db.get(Project, project_id)
            current.active_query_id = query_id
        return {"query": await self.query(query_id, user_id), "research": research}

    async def list_queries(self, project_id, user_id):
        async with self.sessions() as db:
            owner = await db.scalar(
                select(Project.id).where(Project.id == project_id, Project.user_id == user_id)
            )
            if not owner:
                return None
            project = await db.get(Project, project_id)
            rows = (
                await db.scalars(
                    select(ProjectQuery)
                    .where(ProjectQuery.project_id == project_id)
                    .order_by(ProjectQuery.created_at.desc())
                )
            ).all()
            return [self.query_view(x, project.active_query_id) for x in rows]

    async def query(self, query_id, user_id):
        async with self.sessions() as db:
            row = await db.scalar(
                select(ProjectQuery)
                .join(Project)
                .where(ProjectQuery.id == query_id, Project.user_id == user_id)
            )
            project = await db.get(Project, row.project_id) if row else None
            return self.query_view(row, project.active_query_id) if row else None

    @staticmethod
    def query_view(row, active_query_id=None):
        public_status = (
            "UNSUPPORTED_CONSTRAINT" if row.status == "UNSUPPORTED" else row.status
        )
        return {
            "id": row.id,
            "project_id": row.project_id,
            "research_session_id": row.research_session_id,
            "type": row.type,
            "query_text": row.query_text,
            "normalized_query": row.normalized_query,
            "query_understanding": row.query_understanding,
            "status": public_status,
            "created_at": row.created_at.isoformat(),
            "updated_at": row.updated_at.isoformat(),
            "is_active": row.id == active_query_id,
        }

    async def activate(self, project_id, query_id, user_id):
        async with self.sessions.begin() as db:
            project = await db.scalar(
                select(Project).where(Project.id == project_id, Project.user_id == user_id)
            )
            query = await db.scalar(
                select(ProjectQuery).where(
                    ProjectQuery.id == query_id,
                    ProjectQuery.project_id == project_id,
                    ProjectQuery.type == "STANDARD",
                )
            )
            if not project or not query:
                return None
            now = utcnow()
            await db.execute(
                update(ProjectQuery)
                .where(
                    ProjectQuery.project_id == project_id,
                    ProjectQuery.type == "STANDARD",
                    ProjectQuery.status == "ACTIVE",
                )
                .values(status="ARCHIVED", updated_at=now)
            )
            query.status, query.updated_at, project.active_query_id = "ACTIVE", now, query.id
        return await self.query(query_id, user_id)

    async def create_hard(
        self,
        project_id,
        text,
        user_id,
        plan=None,
        parent_request_id=None,
        operation=None,
        recovery_generation=0,
    ):
        async with self.sessions() as db:
            owner = await db.scalar(
                select(Project.id).where(Project.id == project_id, Project.user_id == user_id)
            )
        if not owner:
            return None
        plan = plan or await self.parse(text)
        now, query_id, request_id = utcnow(), _id(), _id()
        async with self.sessions.begin() as db:
            chat_id = await db.scalar(select(Chat.id).where(Chat.project_id == project_id))
            db.add(
                ProjectQuery(
                    id=query_id,
                    project_id=project_id,
                    type="HARD",
                    query_text=text,
                    normalized_query=plan.normalized_query,
                    query_understanding=plan.query_understanding,
                    status="RUNNING",
                    created_at=now,
                    updated_at=now,
                )
            )
            await db.flush()
            project = await db.get(Project, project_id)
            previous_active_query_id = project.active_query_id
            db.add(
                HardRequestRecord(
                    id=request_id,
                    project_id=project_id,
                    query_id=query_id,
                    original_text=text,
                    parsed_plan=plan.model_dump(mode="json"),
                    status="RUNNING",
                    target_cases=plan.target_sets,
                    metrics={
                        "recovery_generation": recovery_generation,
                        "previous_active_query_id": previous_active_query_id,
                        "operation": operation,
                    },
                    parent_request_id=parent_request_id,
                    revision_number=(
                        1
                        if not parent_request_id
                        else (
                            await db.scalar(
                                select(func.coalesce(func.max(HardRequestRecord.revision_number), 0)).where(
                                    (HardRequestRecord.id == parent_request_id)
                                    | (HardRequestRecord.parent_request_id == parent_request_id)
                                )
                            )
                        )
                        + 1
                    ),
                    created_at=now,
                    updated_at=now,
                )
            )
            if chat_id:
                db.add(
                    Message(
                        id=_id(),
                        chat_id=chat_id,
                        role="user",
                        content=text,
                        project_query_id=query_id,
                        hard_request_id=request_id,
                        structured_operation=operation,
                        created_at=now,
                    )
                )
        try:
            await self._prepare_and_build(request_id, initial=5)
        except Exception as exc:
            await self._mark_failed(request_id, exc, "INITIAL_ASSEMBLY")
            raise
        async with self.sessions.begin() as db:
            project = await db.get(Project, project_id)
            previous_query_id = (await db.get(HardRequestRecord, request_id)).metrics.get(
                "previous_active_query_id"
            )
            if previous_query_id:
                previous = await db.get(ProjectQuery, previous_query_id)
                if previous and previous.status == "ACTIVE":
                    previous.status, previous.updated_at = "ARCHIVED", utcnow()
            project.active_query_id = query_id
        async with self.sessions.begin() as db:
            built = await db.get(HardRequestRecord, request_id)
            chat_id = await db.scalar(select(Chat.id).where(Chat.project_id == project_id))
            if chat_id:
                db.add(
                    Message(
                        id=_id(),
                        chat_id=chat_id,
                        role="assistant",
                        content=f"Собрано {built.completed_cases} из {built.target_cases} наборов.",
                        project_query_id=query_id,
                        hard_request_id=request_id,
                        result_revision=built.revision_number,
                        created_at=utcnow(),
                    )
                )
        return await self.get(request_id, user_id)

    async def messages(self, project_id, user_id):
        async with self.sessions() as db:
            chat_id = await db.scalar(
                select(Chat.id)
                .join(Project)
                .where(Project.id == project_id, Project.user_id == user_id, Chat.project_id == Project.id)
            )
            if not chat_id:
                return None
            rows = (
                await db.scalars(
                    select(Message).where(Message.chat_id == chat_id).order_by(Message.created_at)
                )
            ).all()
            return [
                {
                    "id": row.id,
                    "role": row.role,
                    "content": row.content,
                    "hard_request_id": row.hard_request_id,
                    "result_revision": row.result_revision,
                    "structured_operation": row.structured_operation,
                    "created_at": row.created_at.isoformat(),
                }
                for row in rows
            ]

    async def active_hard(self, project_id, user_id):
        async with self.sessions() as db:
            row = await db.scalar(
                select(HardRequestRecord)
                .join(ProjectQuery)
                .join(Project)
                .where(
                    Project.id == project_id,
                    Project.user_id == user_id,
                    Project.active_query_id == ProjectQuery.id,
                    HardRequestRecord.query_id == ProjectQuery.id,
                )
            )
            return self.view(row) if row else None

    @asynccontextmanager
    async def _project_mutation_lock(self, project_id: str):
        """Serialize a project's read/apply/build/activate chain across API processes."""
        started = time.perf_counter()
        async with self.sessions() as db:
            dialect = db.bind.dialect.name if db.bind is not None else ""
            if dialect == "postgresql":
                await db.execute(
                    sql_text("SELECT pg_advisory_lock(hashtext(:project_id))"),
                    {"project_id": str(project_id)},
                )
            try:
                yield round((time.perf_counter() - started) * 1000, 2)
            finally:
                if dialect == "postgresql":
                    await db.execute(
                        sql_text("SELECT pg_advisory_unlock(hashtext(:project_id))"),
                        {"project_id": str(project_id)},
                    )

    @asynccontextmanager
    async def project_transition_lock(self, project_id: str):
        """Protect one unified workspace transition in this and other API processes."""
        async with self.mutation_locks[str(project_id)]:
            async with self._project_mutation_lock(str(project_id)) as wait_ms:
                yield wait_ms

    async def patch_active(
        self, project_id, text, user_id, *, lock_held=False, db_lock_wait_ms=0.0
    ):
        # Serialize mutations per project so concurrent follow-ups cannot branch
        # from the same stale active revision and silently lose one update.
        if not lock_held:
            async with self.project_transition_lock(str(project_id)) as db_lock_wait_ms:
                return await self._patch_active_locked(
                    project_id, text, user_id, db_lock_wait_ms
                )
        return await self._patch_active_locked(project_id, text, user_id, db_lock_wait_ms)

    async def _patch_active_locked(self, project_id, text, user_id, db_lock_wait_ms):
        current = await self.active_hard(project_id, user_id)
        if not current:
            return None
        plan = HardRequestIntent.model_validate(current["plan"])
        understanding = (
            await self.query_understanding.understand(text)
            if self.query_understanding
            else None
        )
        interpreted_text = understanding.search_query if understanding else text
        mutation = self.intent_mutations.plan(plan, text, interpreted_text)
        if not mutation.patches:
            return None
        if mutation.patches[0].operation == PatchOperation.UNDO:
            return await self._undo_active_hard(project_id, user_id, current, text)
        revised = self.intent_mutations.apply(plan, mutation)
        revised.original_query = text
        revised.normalized_query = interpreted_text
        revised.query_understanding = (
            understanding.model_dump(mode="json") if understanding else {}
        )
        revised.query_understanding["mutation"] = mutation.model_dump(mode="json")
        revised.query_understanding["db_lock_wait_ms"] = db_lock_wait_ms
        operation = {
            "type": "INTENT_PATCH",
            "patches": [patch.model_dump(mode="json") for patch in mutation.patches],
            "base_revision": current["revision_number"],
            "db_lock_wait_ms": db_lock_wait_ms,
        }
        root = current.get("parent_request_id") or current["id"]
        return await self.create_hard(project_id, text, user_id, revised, root, operation)

    async def _undo_active_hard(self, project_id, user_id, current, text):
        """Publish an immutable revision restoring the previous effective intent."""
        root = current.get("parent_request_id") or current["id"]
        current_operation = (current.get("metrics") or {}).get("operation") or {}
        upper_revision = (
            int(current_operation["restored_revision"])
            if current_operation.get("type") == "UNDO"
            else int(current["revision_number"])
        )
        async with self.sessions() as db:
            rows = (
                await db.scalars(
                    select(HardRequestRecord)
                    .join(Project)
                    .where(
                        HardRequestRecord.project_id == project_id,
                        Project.user_id == user_id,
                        (HardRequestRecord.id == root) | (HardRequestRecord.parent_request_id == root),
                        HardRequestRecord.status.in_([
                            "COMPLETE", "COMPLETED", "PARTIAL", "COMPLETED_FULL",
                            "COMPLETED_PARTIAL", "NO_MATCH", "UNSUPPORTED", "UNSUPPORTED_CONSTRAINT",
                        ]),
                        HardRequestRecord.revision_number < upper_revision,
                    )
                    .order_by(HardRequestRecord.revision_number.desc())
                )
            ).all()
        target = next(
            (
                row
                for row in rows
                if (((row.metrics or {}).get("operation") or {}).get("type") != "UNDO")
            ),
            None,
        )
        if target is None:
            return None
        plan = HardRequestIntent.model_validate(target.parsed_plan)
        plan.original_query = text
        operation = {"type": "UNDO", "restored_request_id": target.id,
                     "restored_revision": target.revision_number}
        return await self.create_hard(project_id, text, user_id, plan, root, operation)

    async def _prepare_and_build(self, request_id, initial=5):
        started = time.perf_counter()
        async with self.sessions() as db:
            record = await db.get(HardRequestRecord, request_id)
        plan = HardRequestIntent.model_validate(record.parsed_plan)
        if plan.unsupported_constraints:
            async with self.sessions.begin() as db:
                current = await db.get(HardRequestRecord, request_id)
                # The existing status columns are VARCHAR(20); expose the full
                # logical name from view() while persisting a compact token.
                current.status = "UNSUPPORTED"
                current.metrics = dict(current.metrics or {}) | {
                    "unsupported_constraints": plan.unsupported_constraints,
                    "requested_cases": plan.target_sets,
                    "assembled_cases": 0,
                    "assembly_status": "UNSUPPORTED_CONSTRAINT",
                }
                current.updated_at, current.revision = utcnow(), current.revision + 1
                query = await db.get(ProjectQuery, current.query_id)
                query.status, query.updated_at = "UNSUPPORTED", utcnow()
            return
        if plan.category_mode == "AUTO_DISCOVER":
            async with self.sessions() as db:
                concepts = (
                    await db.scalars(
                        select(ProductConcept)
                        .where(ProductConcept.products_count > 0)
                        .order_by(ProductConcept.products_count.desc())
                        .limit(plan.declared_categories_count or 5)
                    )
                ).all()
            plan.categories = [c.display_name for c in concepts]
            plan.parsed_categories_count = len(plan.categories)
        recovery_generation = int((record.metrics or {}).get("recovery_generation", 0))
        pools, search_metrics = {}, {}
        retrieval_ms = 0.0
        matcher = ProductCategoryMatcher()
        all_offer_ids = set()
        retrieval_gate = asyncio.Semaphore(4)

        async def retrieve_position(category):
            intent = await self.research.planner.parse(category)
            intent.quantity = None
            constraints = plan.effective(category)
            normalized_colors = (
                constraints.color.values
                if constraints.color and constraints.color.mode == "REQUIRED"
                else None
            )
            # Apply category evidence in SQL, then hard constraints over the
            # complete pool. A pre-filter LIMIT caused valid refreshed offers
            # to be hidden behind older rows.
            retrieval_started = time.perf_counter()
            async with retrieval_gate:
                raw_ids = await self.index.candidate_ids(intent)
                candidate_ids = (
                    await self.index.candidate_ids(intent, normalized_colors=normalized_colors)
                    if normalized_colors
                    else raw_ids
                )
            duration_ms = (time.perf_counter() - retrieval_started) * 1000
            return category, intent, raw_ids, candidate_ids, duration_ms

        retrieval_started = time.perf_counter()
        position_ids = await asyncio.gather(
            *(retrieve_position(category) for category in plan.categories)
        )
        offer_ids = list(
            dict.fromkeys(offer_id for _, _, _, ids, _ in position_ids for offer_id in ids)
        )
        hydration_started = time.perf_counter()
        hydrated = (
            await self.index.candidates(position_ids[0][1], offer_ids=offer_ids)
            if position_ids and offer_ids
            else []
        )
        hydration_ms = (time.perf_counter() - hydration_started) * 1000
        products_by_id = {product.id: product for product in hydrated}
        position_results = []
        category_validation_started = time.perf_counter()
        for category, intent, raw_ids, candidate_ids, duration_ms in position_ids:
            candidates = [products_by_id[value] for value in candidate_ids if value in products_by_id]
            matches = [
                p
                for p in candidates
                if any(
                    matcher.match(p, concept, category) in {CategoryMatch.EXACT, CategoryMatch.STRONG_RELATED}
                    for concept in intent.categories
                )
            ]
            position_results.append((category, len(raw_ids), candidates, matches, duration_ms))
        category_validation_ms = (time.perf_counter() - category_validation_started) * 1000
        retrieval_wall_ms = (time.perf_counter() - retrieval_started) * 1000
        for category, raw_count, candidates, matches, duration_ms in position_results:
            retrieval_ms += duration_ms
            pools[category] = matches
            all_offer_ids.update(product.id for product in matches)
            search_metrics[category] = {
                "raw_candidates": raw_count,
                "lexical_match": raw_count,
                "concept_matched": raw_count,
                "sql_category_evidence": raw_count,
                "sql_color_matched": len(candidates),
                "category_color_validated": len(matches),
                "has_offer": raw_count,
                "category_matched": len(matches),
            }
        identity_started = time.perf_counter()
        product_ids = {
            product.id: product.metadata.get("indexed_product_id")
            for values in pools.values()
            for product in values
            if product.metadata.get("indexed_product_id")
        }
        missing_ids = all_offer_ids - product_ids.keys()
        if missing_ids:
            # Compatibility path for legacy/test Product DTOs. Production
            # hydration carries this identity and avoids a second large IN query.
            async with self.sessions() as db:
                product_ids.update(
                    dict(
                        (
                            await db.execute(
                                select(SupplierOffer.id, SupplierOffer.product_id).where(
                                    SupplierOffer.id.in_(missing_ids)
                                )
                            )
                        ).all()
                    )
                )
        identity_ms = (time.perf_counter() - identity_started) * 1000
        tier_order = {"A": 0, "B": 1, "C": 2, "D": 3}
        eligible, recovery_by_supplier, recovery_positions = {}, {}, []
        eligibility_started = time.perf_counter()
        for category, products in pools.items():
            constraints = plan.effective(category)
            color_matched = [
                product for product in products if self.color_eligible(product, constraints)
            ]
            color_matched_ids = {product.id for product in color_matched}
            required = constraints.required_quantity
            evaluations = {
                product.id: self.strict_procurement_evaluation(
                    product,
                    required,
                    fresh_only=plan.availability_policy == "FRESH_ONLY",
                )
                for product in color_matched
            }
            quantity_confirmed = [
                product for product in color_matched if evaluations[product.id].result == "PASS"
            ]
            if constraints.price_max is not None:
                quantity_confirmed = [
                    product
                    for product in quantity_confirmed
                    if product.price_kzt is not None and product.price_kzt <= constraints.price_max
                ]
            if constraints.price_min is not None:
                quantity_confirmed = [
                    product
                    for product in quantity_confirmed
                    if product.price_kzt is not None and product.price_kzt >= constraints.price_min
                ]
            if constraints.materials:
                wanted = [value.casefold() for value in constraints.materials]
                quantity_confirmed = [
                    product for product in quantity_confirmed
                    if product.material and any(value in product.material.casefold() for value in wanted)
                ]
            if constraints.excluded_materials:
                excluded = [value.casefold() for value in constraints.excluded_materials]
                quantity_confirmed = [
                    product for product in quantity_confirmed
                    if not product.material
                    or not any(value in product.material.casefold() for value in excluded)
                ]
            best_by_product = {}
            for product in quantity_confirmed:
                product_id = product_ids.get(product.id)
                if not product_id:
                    continue
                previous = best_by_product.get(product_id)
                quantity = constraints.required_quantity
                if (
                    previous is None
                    or tier_order[self.tier(product, quantity)] < tier_order[self.tier(previous, quantity)]
                ):
                    best_by_product[product_id] = product
            eligible[category] = sorted(
                best_by_product.values(),
                key=lambda product: (
                    tier_order[
                        self.tier(product, constraints.required_quantity)
                    ],
                    hashlib.sha256(f"{category}:{product.id}".encode()).hexdigest(),
                ),
            )
            reasons = {}
            for evaluation in evaluations.values():
                reasons[evaluation.reason_code] = reasons.get(evaluation.reason_code, 0) + 1
            if required is not None:
                recoverable = [
                    product
                    for product in color_matched
                    if evaluations[product.id].reason_code
                    in {
                        "UNKNOWN_QUANTITY",
                        "NO_PROCUREMENT_EVIDENCE",
                    }
                ]
                recoverable.sort(
                    key=lambda product: (
                        evaluations[product.id].reason_code != "UNKNOWN_QUANTITY",
                        -max((row.free_quantity or 0 for row in product.availability_records), default=0),
                        product.id,
                    )
                )
                budget = min(
                    self.index.settings.composite_refresh_max_per_position,
                    plan.target_sets * self.index.settings.composite_refresh_safety_factor,
                )
                recovery_positions.append(
                    (len(eligible[category]), category, recoverable[:budget])
                )
            color_unknown = sum(
                not product.colors
                or all(color.normalized_color.value == "UNKNOWN" for color in product.colors)
                for product in products
                if product.id not in color_matched_ids
            )
            raw_count = search_metrics[category]["raw_candidates"]
            category_count = len(products)
            color_mismatch = category_count - len(color_matched) - color_unknown
            if color_unknown:
                reasons["COLOR_UNKNOWN"] = color_unknown
            if color_mismatch:
                reasons["COLOR_MISMATCH"] = color_mismatch
            has_availability = [p for p in color_matched if p.availability_records]
            fresh = [p for p in has_availability if not p.metadata.get("availability_stale")]
            last_known = [p for p in has_availability if p.metadata.get("availability_stale")]
            on_hand = [
                p for p in has_availability if any(r.state == "ON_HAND" for r in p.availability_records)
            ]
            free_known = [
                p
                for p in on_hand
                if any(
                    r.state == "ON_HAND" and r.free_quantity is not None
                    for r in p.availability_records
                )
            ]
            free_sufficient = [
                p
                for p in free_known
                if required is None
                or sum(
                    r.free_quantity or 0
                    for r in p.availability_records
                    if r.state == "ON_HAND" and r.free_quantity is not None
                )
                >= required
            ]

            def stage(name, before, after, reason=None):
                return {
                    "stage": name,
                    "count_before": before,
                    "count_after": after,
                    "removed_count": before - after,
                    "removal_reason": reason,
                }

            if required is None:
                funnel = [
                    stage("RAW_INDEX", raw_count, raw_count),
                    stage("CATEGORY_MATCH", raw_count, category_count, "CATEGORY_MISMATCH"),
                    stage("COLOR_MATCH", category_count, len(color_matched), "COLOR_MISMATCH_OR_UNKNOWN"),
                    {
                        **stage("AVAILABILITY_KNOWN", len(color_matched), len(color_matched)),
                        "observed_count": len(has_availability),
                        "applicability": "INFORMATIONAL",
                    },
                    {
                        **stage("FRESH_AVAILABILITY", len(color_matched), len(color_matched)),
                        "observed_count": len(fresh),
                        "applicability": "INFORMATIONAL",
                    },
                    stage("ELIGIBLE_WITHOUT_QUANTITY", len(color_matched), len(quantity_confirmed)),
                    stage("DEDUPED", len(quantity_confirmed), len(eligible[category]), "DUPLICATE_PRODUCT"),
                    stage("ELIGIBLE", len(eligible[category]), len(eligible[category])),
                ]
            else:
                funnel = [
                    stage("RAW_INDEX", raw_count, raw_count),
                    stage("CATEGORY_MATCH", raw_count, category_count, "CATEGORY_MISMATCH"),
                    stage("COLOR_MATCH", category_count, len(color_matched), "COLOR_MISMATCH_OR_UNKNOWN"),
                    stage("HAS_AVAILABILITY", len(color_matched), len(has_availability), "NO_PROCUREMENT_EVIDENCE"),
                    {
                        **stage("OBSERVATION_CONFIDENCE", len(has_availability), len(has_availability)),
                        "confirmed_count": len(fresh),
                        "last_known_count": len(last_known),
                        "fresh_only": plan.availability_policy == "FRESH_ONLY",
                    },
                    stage("ON_HAND", len(has_availability), len(on_hand), "NON_CURRENT_SUPPLY_ONLY"),
                    stage("HAS_FREE_QUANTITY", len(on_hand), len(free_known), "UNKNOWN_QUANTITY"),
                    stage("FREE_QUANTITY_SUFFICIENT", len(free_known), len(free_sufficient), "FREE_QUANTITY_INSUFFICIENT"),
                    stage("VARIANT_VALID", len(free_sufficient), len(quantity_confirmed), "VARIANT_MISMATCH"),
                    stage("DEDUPED", len(quantity_confirmed), len(eligible[category]), "DUPLICATE_PRODUCT"),
                    stage("ELIGIBLE", len(eligible[category]), len(eligible[category])),
                ]
            search_metrics[category].update(
                {
                    "color_matched": len(color_matched),
                    "has_availability_observation": len(has_availability),
                    "latest_availability_selected": len(has_availability),
                    "fresh_observation": len(fresh),
                    "last_known_observation": len(last_known),
                    "on_hand": len(on_hand),
                    "free_known": sum(
                        any(
                            r.state == "ON_HAND" and r.free_quantity is not None
                            for r in p.availability_records
                        )
                        for p in color_matched
                    ),
                    "quantity_required": required is not None,
                    "required_quantity": required,
                    "quantity_metric_applicability": (
                        "APPLICABLE" if required is not None else "NOT_APPLICABLE"
                    ),
                    "eligible_without_quantity": (
                        len(quantity_confirmed) if required is None else None
                    ),
                    "free_gte_required": None if required is None else sum(
                        any(
                            r.state == "ON_HAND"
                            and r.free_quantity is not None
                            and r.free_quantity
                            >= required
                            for r in p.availability_records
                        )
                        for p in color_matched
                    ),
                    "fresh_on_hand_free_gte_required": None if required is None else sum(
                        not p.metadata.get("availability_stale")
                        and any(
                            r.state == "ON_HAND"
                            and r.free_quantity is not None
                            and r.free_quantity
                            >= required
                            for r in p.availability_records
                        )
                        for p in color_matched
                    ),
                    "quantity_confirmed": len(quantity_confirmed),
                    "confirmed_sufficient": sum(
                        evaluation.reason_code == "CURRENT_FREE_SUFFICIENT"
                        for evaluation in evaluations.values()
                    ),
                    "last_known_sufficient": sum(
                        evaluation.reason_code == "LAST_KNOWN_FREE_SUFFICIENT"
                        for evaluation in evaluations.values()
                    ),
                    "deduped": len(eligible[category]),
                    "canonical_dedup_rejected": len(quantity_confirmed) - len(eligible[category]),
                    "eligible_unique": len(eligible[category]),
                    # Stable SupplierOffer identities make equivalent-query
                    # parity and allocator diagnostics auditable without
                    # confusing UI pagination with the backend pool.
                    "eligible_supplier_offer_ids": [
                        product.id for product in eligible[category]
                    ],
                    "top_reject_reasons": reasons,
                    "funnel": funnel,
                    "effective_constraints": constraints.model_dump(mode="json"),
                    "recoverable_availability": len(recoverable)
                    if required is not None
                    else 0,
                }
            )
        # The smallest eligible pool determines complete-case capacity. Queue
        # its recoverable offers first while preserving per-position budgets.
        for _capacity, _category, products in sorted(recovery_positions):
            for product in products:
                recovery_by_supplier.setdefault(product.supplier, []).append(product.source_url)
        eligibility_ms = (time.perf_counter() - eligibility_started) * 1000
        assembly_started = time.perf_counter()
        assembly = self.assembly_plan(plan, eligible, product_ids)
        assembly_ms = (time.perf_counter() - assembly_started) * 1000
        allocated_by_category = {
            category: {
                product_ids[product.id]
                for positions in assembly
                for selected_category, chosen in positions
                if selected_category == category
                for product in chosen
                if product.id in product_ids
            }
            for category in plan.categories
        }
        for category in plan.categories:
            passed = len(eligible.get(category, []))
            allocated = len(allocated_by_category.get(category, set()))
            search_metrics[category].update(
                {
                    "candidates_passed_to_allocator": passed,
                    "candidates_selected_by_allocator": allocated,
                    "candidates_rejected_by_allocator": max(0, passed - allocated),
                }
            )
        maximum = len(assembly)
        last_known_cases = sum(
            any(
                self.tier(chosen[0], plan.effective(category).required_quantity) == "B"
                for category, chosen in positions
            )
            for positions in assembly
        )
        theoretical = maximum
        capacities = {
            category: len(values) // plan.effective(category).items_required
            for category, values in eligible.items()
        }
        bottleneck_capacity = min(capacities.values(), default=0)
        bottlenecks = [
            category for category, capacity in capacities.items()
            if capacity == bottleneck_capacity
        ]
        elapsed = round((time.perf_counter() - started) * 1000, 2)
        async with self.sessions.begin() as db:
            current = await db.get(HardRequestRecord, request_id)
            current.parsed_plan = plan.model_dump(mode="json")
            # Assembly diagnostics augment lifecycle/conversation metadata written
            # when the immutable revision is created. Replacing this dictionary
            # erased the UNDO target and made a second Undo jump forward again.
            current.metrics = dict(current.metrics or {}) | {
                "recovery_generation": recovery_generation,
                "category_pools": search_metrics,
                "candidate_search_ms": elapsed,
                "sql_candidate_retrieval_ms": round(retrieval_wall_ms, 2),
                "sql_candidate_retrieval_sum_ms": round(retrieval_ms, 2),
                "supplier_offer_hydration_ms": round(hydration_ms, 2),
                "category_validation_ms": round(category_validation_ms, 2),
                "product_identity_ms": round(identity_ms, 2),
                "eligibility_diagnostics_ms": round(eligibility_ms, 2),
                "case_assembly_ms": round(assembly_ms, 2),
                "filter_dedup_assembly_ms": round(max(0, elapsed - retrieval_wall_ms), 2),
                "requested_cases": plan.target_sets,
                "confirmed_stock_cases": maximum - last_known_cases,
                "last_known_stock_cases": last_known_cases,
                "theoretical_max_unique_cases": theoretical,
                "maximum_unique_cases_possible": maximum,
                "bottleneck_capacity": bottleneck_capacity,
                "bottleneck_positions": bottlenecks,
                "assembly_algorithm": "maximum-bipartite-matching-v2",
                "required_items_per_category": {
                    category: plan.effective(category).items_required
                    for category in plan.categories
                },
                "required_slots_per_case": sum(
                    plan.effective(category).items_required for category in plan.categories
                ),
                "case_completeness": "one selected strict-eligible offer per required slot; up to candidates_per_position alternatives per slot",
            }
        recovery_jobs = []
        if maximum < plan.target_sets and recovery_generation == 0:
            for supplier, urls in recovery_by_supplier.items():
                if not urls:
                    continue
                job_id = await self.index.enqueue(
                    supplier,
                    "TARGETED_REFRESH",
                    checkpoint={
                        "pending": list(dict.fromkeys(urls)),
                        "reason": "composite_low_capacity",
                        "composite_request_ids": [request_id],
                    },
                    priority=100,
                )
                job_state = await self.index.job_status(job_id)
                if job_state and job_state["status"] in {"PENDING", "RUNNING"} and not job_state["error"]:
                    recovery_jobs.append(job_id)
        if recovery_jobs:
            async with self.sessions.begin() as db:
                current = await db.get(HardRequestRecord, request_id)
                current.metrics = dict(current.metrics or {}) | {
                    "pending_availability_recovery": len(set(recovery_jobs)),
                    "availability_recovery_jobs": list(dict.fromkeys(recovery_jobs)),
                }
        await self._append_cases(request_id, plan, assembly, product_ids, 1, min(initial, maximum))
        if maximum > initial:
            task = asyncio.create_task(
                self._continue(request_id, plan, assembly, product_ids, initial + 1),
                name=f"hard-{request_id}",
            )
            self.tasks[request_id] = task
        else:
            await self._finish(request_id)

    async def _continue(self, request_id, plan, assembly, product_ids, start):
        try:
            for first in range(start, len(assembly) + 1, 5):
                await self._append_cases(
                    request_id, plan, assembly, product_ids, first, min(first + 4, len(assembly))
                )
                await asyncio.sleep(0)
            await self._finish(request_id)
        except Exception as exc:
            await self._mark_failed(request_id, exc, "BACKGROUND_ASSEMBLY")

    async def _mark_failed(self, request_id, exc, stage):
        """Persist a bounded terminal failure and restore the prior workspace."""
        now = utcnow()
        async with self.sessions.begin() as db:
            row = await db.get(HardRequestRecord, request_id)
            if not row:
                return
            metrics = dict(row.metrics or {})
            previous_query_id = metrics.get("previous_active_query_id")
            metrics.update(
                {
                    "failure_code": type(exc).__name__[:80],
                    "failure_stage": stage,
                    "failed_at": now.isoformat(),
                }
            )
            row.status, row.metrics, row.updated_at = "FAILED", metrics, now
            query = await db.get(ProjectQuery, row.query_id)
            if query:
                query.status, query.updated_at = "FAILED", now
            project = await db.get(Project, row.project_id)
            if project and project.active_query_id == row.query_id:
                project.active_query_id = previous_query_id

    def tier(self, product, quantity):
        if quantity is None:
            if product.metadata.get("availability_stale"):
                return "D"
            records = product.availability_records
            if any(
                record.state == "ON_HAND"
                and record.free_quantity is not None
                and record.free_quantity > 0
                for record in records
            ):
                return "A"
            if any(
                record.state in {"INCOMING", "RECEIVING"}
                and record.free_quantity is not None
                and record.free_quantity > 0
                for record in records
            ):
                return "B"
            if any(
                record.state in {
                    "REMOTE_STOCK",
                    "PREORDER",
                    "FACTORY",
                    "PRODUCTION",
                    "ORDER_ON_DEMAND",
                    "WAITING_SHIPMENT",
                }
                and record.free_quantity is not None
                and record.free_quantity > 0
                for record in records
            ):
                return "C"
            return "D"
        evaluation = self.procurement_evaluation(product, quantity)
        return {"CURRENT_FREE": "A", "LAST_KNOWN": "B", "CURRENT_PLUS_INCOMING": "B", "REMOTE": "C", "ALTERNATIVE": "C"}.get(
            evaluation.tier, "D"
        )

    @staticmethod
    def procurement_evaluation(product, quantity):
        if quantity is None:
            from app.application.availability import ProcurementEvaluation

            return ProcurementEvaluation(1, "PASS", "CURRENT_FREE", None, [], "QUANTITY_NOT_REQUIRED")
        return ProcurementAvailabilityService().evaluate(
            product.availability_records, quantity, stale=bool(product.metadata.get("availability_stale"))
        )

    @staticmethod
    def strict_procurement_evaluation(product, quantity, *, fresh_only=False):
        """Composite quantity eligibility uses numeric ON_HAND free stock."""
        from app.application.availability import ProcurementEvaluation

        if quantity is None:
            return ProcurementEvaluation(1, "PASS", "CATALOG", None, [], "QUANTITY_NOT_REQUIRED")
        return ProcurementAvailabilityService().evaluate_on_hand(
            product.availability_records,
            quantity,
            stale=bool(product.metadata.get("availability_stale")),
            fresh_only=fresh_only,
        )

    def color_eligible(self, product, constraint):
        if isinstance(constraint, HardRequestIntent):
            excluded = set(constraint.excluded_colors)
            constraint = constraint.color
        elif isinstance(constraint, PositionConstraint):
            excluded = set(constraint.excluded_colors)
            constraint = constraint.color
        else:
            excluded = set()
        actual = {
            color.normalized_color.value
            for color in product.colors
            if color.normalized_color.value != "UNKNOWN"
        }
        if actual & excluded:
            return False
        if constraint is None or constraint.mode == "BASE_COLORS":
            return True
        if constraint.mode == "DIVERSE":
            return any(color.normalized_color.value != "UNKNOWN" for color in product.colors)
        requested = set(constraint.values)
        return bool(requested) and bool(actual & requested)

    def quantity_eligible(self, product, plan):
        required = plan.required_quantity
        return required is None or self.tier(product, required) in {"A", "B", "C"}

    def assembly_plan(self, plan, pools, product_ids):
        """Find required primary slots, then attach alternatives to each slot."""

        pool_ids = {}
        offers = {}
        for category, values in pools.items():
            unique = []
            seen = set()
            for product in values:
                product_id = product_ids.get(product.id)
                if not product_id:
                    continue
                offers.setdefault(product_id, product)
                if product_id not in seen:
                    seen.add(product_id)
                    unique.append(product_id)
            pool_ids[category] = unique

        def assignment(case_count):
            slots = [
                (case_number, category, required_item)
                for case_number in range(case_count)
                for category in plan.categories
                for required_item in range(plan.effective(category).items_required)
            ]
            # Hopcroft-Karp preserves the exact maximum-matching semantics while
            # avoiding the recursive full-pool rescan for every case slot.
            slot_match = [-1] * len(slots)
            product_match = {}
            distance = [0] * len(slots)
            next_edge = [0] * len(slots)

            def bfs():
                queue = deque()
                found = False
                for slot_index, matched_product in enumerate(slot_match):
                    if matched_product == -1:
                        distance[slot_index] = 0
                        queue.append(slot_index)
                    else:
                        distance[slot_index] = -1
                while queue:
                    slot_index = queue.popleft()
                    for product_id in pool_ids.get(slots[slot_index][1], []):
                        previous = product_match.get(product_id)
                        if previous is None:
                            found = True
                        elif distance[previous] < 0:
                            distance[previous] = distance[slot_index] + 1
                            queue.append(previous)
                return found

            def dfs(slot_index):
                neighbours = pool_ids.get(slots[slot_index][1], [])
                while next_edge[slot_index] < len(neighbours):
                    product_id = neighbours[next_edge[slot_index]]
                    next_edge[slot_index] += 1
                    previous = product_match.get(product_id)
                    if previous is None or (
                        distance[previous] == distance[slot_index] + 1 and dfs(previous)
                    ):
                        slot_match[slot_index] = product_id
                        product_match[product_id] = slot_index
                        return True
                distance[slot_index] = -1
                return False

            matched_count = 0
            while bfs():
                next_edge[:] = [0] * len(slots)
                for slot_index, matched_product in enumerate(slot_match):
                    if matched_product == -1 and dfs(slot_index):
                        matched_count += 1
            if matched_count != len(slots):
                return None
            per_case = sum(plan.effective(category).items_required for category in plan.categories)
            return [
                [
                    (category, [offers[slot_match[case_number * per_case + offset]]])
                    for offset, (_case, category, _required_item) in enumerate(
                        slots[case_number * per_case:(case_number + 1) * per_case]
                    )
                ]
                for case_number in range(case_count)
            ]

        upper = min(
            plan.target_sets,
            min((
                len(pool_ids.get(category, [])) // plan.effective(category).items_required
                for category in plan.categories
            ), default=0),
        )
        low, high, result = 0, upper, []
        while low <= high:
            middle = (low + high) // 2
            candidate = assignment(middle)
            if candidate is not None:
                result = candidate
                low = middle + 1
            else:
                high = middle - 1
        used = {
            product_ids[chosen[0].id]
            for positions in result
            for _category, chosen in positions
        }
        # Alternatives consume only identities left after every primary selection.
        for positions in result:
            for category, chosen in positions:
                for product in pools.get(category, []):
                    product_id = product_ids.get(product.id)
                    if not product_id or product_id in used:
                        continue
                    chosen.append(product)
                    used.add(product_id)
                    if len(chosen) >= plan.candidates_per_position:
                        break
        return result

    async def _append_cases(self, request_id, plan, assembly, product_ids, first, last):
        tier_order = {"A": 0, "B": 1, "C": 2, "D": 3}
        created = 0
        async with self.sessions.begin() as db:
            record = await db.get(HardRequestRecord, request_id)
            for number in range(first, last + 1):
                case_positions = assembly[number - 1]
                case = HardRequestCase(
                    id=_id(),
                    hard_request_id=request_id,
                    case_number=number,
                    score=Decimal("0"),
                    status="COMPLETE",
                )
                db.add(case)
                await db.flush()
                for pos, (category, chosen) in enumerate(case_positions, 1):
                    position = HardRequestPosition(
                        id=_id(),
                        case_id=case.id,
                        position_number=pos,
                        category_key=category.casefold(),
                        category_label=category,
                        requested_candidates=plan.candidates_per_position,
                    )
                    db.add(position)
                    await db.flush()
                    for rank, product in enumerate(chosen, 1):
                        effective = plan.effective(category)
                        tier = self.tier(product, effective.required_quantity)
                        db.add(
                            HardRequestCandidate(
                                id=_id(),
                                position_id=position.id,
                                indexed_product_id=product_ids[product.id],
                                supplier_offer_id=product.id,
                                rank=rank,
                                availability_tier=tier,
                                score=Decimal(str(5 - tier_order[tier])),
                                selected=rank == 1,
                                user_locked=False,
                            )
                        )
                created += 1
            record.completed_cases += created
            record.revision += 1
            record.updated_at = utcnow()

    async def _finish(self, request_id):
        async with self.sessions.begin() as db:
            record = await db.get(HardRequestRecord, request_id)
            tiers = dict(
                (
                    await db.execute(
                        select(HardRequestCandidate.availability_tier, func.count())
                        .join(HardRequestPosition)
                        .join(HardRequestCase)
                        .where(HardRequestCase.hard_request_id == request_id)
                        .group_by(HardRequestCandidate.availability_tier)
                    )
                ).all()
            )
            unique_products = await db.scalar(
                select(func.count(func.distinct(HardRequestCandidate.indexed_product_id)))
                .join(HardRequestPosition)
                .join(HardRequestCase)
                .where(HardRequestCase.hard_request_id == request_id)
            )
            positions = await db.scalar(
                select(func.count())
                .select_from(HardRequestPosition)
                .join(HardRequestCase)
                .where(HardRequestCase.hard_request_id == request_id)
            )
            duplicate_selected = await db.scalar(
                select(func.count()).select_from(
                    select(HardRequestCandidate.indexed_product_id)
                    .join(HardRequestPosition)
                    .join(HardRequestCase)
                    .where(
                        HardRequestCase.hard_request_id == request_id,
                        HardRequestCandidate.selected.is_(True),
                    )
                    .group_by(HardRequestCandidate.indexed_product_id)
                    .having(func.count() > 1)
                    .subquery()
                )
            )
            record.metrics = dict(record.metrics or {}) | {
                "availability_tiers": tiers,
                "unique_products": unique_products or 0,
                "positions": positions or 0,
                "duplicate_selected_products": duplicate_selected or 0,
                "invalid_color_candidates": 0,
                "unknown_availability_candidates": 0,
            }
            if record.completed_cases == 0:
                status = "NO_MATCH"
            elif record.completed_cases < record.target_cases:
                status = "COMPLETED_PARTIAL"
            else:
                status = "COMPLETED_FULL"
            record.metrics = dict(record.metrics or {}) | {
                "requested_cases": record.target_cases,
                "assembled_cases": record.completed_cases,
                "assembly_status": status,
            }
            record.status, record.updated_at, record.revision = status, utcnow(), record.revision + 1
            query = await db.get(ProjectQuery, record.query_id)
            query.status, query.updated_at = status, utcnow()

    async def get(self, request_id, user_id):
        async with self.sessions() as db:
            row = await db.scalar(
                select(HardRequestRecord)
                .join(Project)
                .where(HardRequestRecord.id == request_id, Project.user_id == user_id)
            )
            return self.view(row) if row else None

    async def list_hard(self, project_id, user_id):
        async with self.sessions() as db:
            if not await db.scalar(
                select(Project.id).where(Project.id == project_id, Project.user_id == user_id)
            ):
                return None
            rows = (
                await db.scalars(
                    select(HardRequestRecord)
                    .where(HardRequestRecord.project_id == project_id)
                    .order_by(HardRequestRecord.created_at.desc())
                )
            ).all()
            return [self.view(x) for x in rows]

    @staticmethod
    def view(row):
        public_status = (
            "UNSUPPORTED_CONSTRAINT" if row.status == "UNSUPPORTED" else row.status
        )
        return {
            "id": row.id,
            "project_id": row.project_id,
            "query_id": row.query_id,
            "original_text": row.original_text,
            "plan": row.parsed_plan,
            "status": public_status,
            "target_cases": row.target_cases,
            "completed_cases": row.completed_cases,
            "revision": row.revision,
            "parent_request_id": row.parent_request_id,
            "revision_number": row.revision_number,
            "metrics": row.metrics,
            "created_at": row.created_at.isoformat(),
            "updated_at": row.updated_at.isoformat(),
        }

    async def cases(self, request_id, user_id, offset=0, limit=10):
        request_view = await self.get(request_id, user_id)
        if not request_view:
            return None
        required_by_category = {
            position["category_text"].casefold(): position.get("constraints", {}).get(
                "items_required", request_view["plan"].get("items_per_position", 1)
            )
            for position in request_view["plan"].get("positions", [])
        }
        async with self.sessions() as db:
            rows = (
                await db.scalars(
                    select(HardRequestCase)
                    .where(HardRequestCase.hard_request_id == request_id)
                    .order_by(HardRequestCase.case_number)
                    .offset(offset)
                    .limit(limit)
                )
            ).all()
            result = []
            for case in rows:
                positions = (
                    await db.scalars(
                        select(HardRequestPosition)
                        .where(HardRequestPosition.case_id == case.id)
                        .order_by(HardRequestPosition.position_number)
                    )
                ).all()
                items = []
                category_ordinals = defaultdict(int)
                for pos in positions:
                    category_ordinals[pos.category_key] += 1
                    candidates = (
                        await db.execute(
                            select(
                                HardRequestCandidate,
                                SupplierOffer,
                                IndexedProduct,
                                ProductObservation,
                            )
                            .join(SupplierOffer, SupplierOffer.id == HardRequestCandidate.supplier_offer_id)
                            .join(
                                ProductObservation,
                                ProductObservation.offer_id == SupplierOffer.id,
                            )
                            .join(
                                IndexedProduct, IndexedProduct.id == HardRequestCandidate.indexed_product_id
                            )
                            .where(HardRequestCandidate.position_id == pos.id)
                            .order_by(HardRequestCandidate.rank)
                        )
                    ).all()
                    items.append(
                        {
                            "id": pos.id,
                            "position": pos.position_number,
                            "category": pos.category_label,
                            "required_item": category_ordinals[pos.category_key],
                            "required_items": required_by_category.get(pos.category_key, 1),
                            "candidates": [
                                {
                                    "id": c.id,
                                    "offer_id": o.id,
                                    "product_id": p.id,
                                    "name": o.payload.get("name") or p.name,
                                    "colors": o.payload.get("colors", []),
                                    "sku": o.payload.get("metadata", {}).get("sku"),
                                    "stock_quantity": observation.stock,
                                    "stock_scope": o.payload.get("metadata", {}).get("stock_scope"),
                                    "supplier": o.supplier,
                                    "source_url": validated_product_url(o.supplier, o.source_url),
                                    "image": o.payload.get("primary_image")
                                    or next(iter(o.payload.get("images") or []), None),
                                    "price": observation.price_kzt,
                                    "tier": c.availability_tier,
                                    "selected": c.selected,
                                    "user_locked": c.user_locked,
                                }
                                for c, o, p, observation in candidates
                            ],
                        }
                    )
                result.append(
                    {"id": case.id, "number": case.case_number, "status": case.status, "positions": items}
                )
            total = await db.scalar(
                select(func.count())
                .select_from(HardRequestCase)
                .where(HardRequestCase.hard_request_id == request_id)
            )
            return {
                "cases": result,
                "total": total or 0,
                "offset": offset,
                "next_offset": offset + limit if offset + limit < (total or 0) else None,
            }

    async def select_candidate(self, request_id, candidate_id, user_id):
        if not await self.get(request_id, user_id):
            return False
        async with self.sessions.begin() as db:
            candidate = await db.scalar(
                select(HardRequestCandidate)
                .join(HardRequestPosition)
                .join(HardRequestCase)
                .where(HardRequestCandidate.id == candidate_id, HardRequestCase.hard_request_id == request_id)
            )
            if not candidate:
                return False
            await db.execute(
                update(HardRequestCandidate)
                .where(HardRequestCandidate.position_id == candidate.position_id)
                .values(selected=False)
            )
            candidate.selected = True
            candidate.user_locked = True
        return True

    async def rebuild(self, request_id, user_id):
        row = await self.get(request_id, user_id)
        if not row:
            return None
        reparsed = await self.parse(row["original_text"])
        root = row.get("parent_request_id") or row["id"]
        return await self.create_hard(
            row["project_id"],
            row["original_text"],
            user_id,
            reparsed,
            root,
            {"type": "REBUILD"},
        )

    async def close(self):
        for task in self.tasks.values():
            task.cancel()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)

    async def recover(self):
        """Resume interrupted assembly without touching a user's locked choice."""
        async with self.sessions() as db:
            rows = (
                await db.scalars(select(HardRequestRecord).where(HardRequestRecord.status == "RUNNING"))
            ).all()
        for row in rows:
            async with self.sessions.begin() as db:
                locked = await db.scalar(
                    select(func.count())
                    .select_from(HardRequestCandidate)
                    .join(HardRequestPosition)
                    .join(HardRequestCase)
                    .where(
                        HardRequestCase.hard_request_id == row.id,
                        HardRequestCandidate.user_locked.is_(True),
                    )
                )
                current = await db.get(HardRequestRecord, row.id)
                if locked:
                    current.status = "PARTIAL"
                    continue
                await db.execute(delete(HardRequestCase).where(HardRequestCase.hard_request_id == row.id))
                current.completed_cases = 0
            self.tasks[row.id] = asyncio.create_task(
                self._recover_build(row.id), name=f"hard-recover-{row.id}"
            )

    async def _recover_build(self, request_id):
        try:
            await self._prepare_and_build(request_id, initial=5)
        except Exception as exc:
            await self._mark_failed(request_id, exc, "RECOVERY_ASSEMBLY")

    async def on_index_update(self, job):
        """Publish one bounded recovery revision when refreshed evidence arrives."""
        for request_id in job.get("checkpoint", {}).get("composite_request_ids", []):
            async with self.sessions() as db:
                record = await db.get(HardRequestRecord, request_id)
                if not record:
                    continue
                project = await db.get(Project, record.project_id)
                user_id = project.user_id if project else None
            if not project or not user_id:
                continue
            # Serialize the final active-query check and publication with user
            # mutations. The earlier check alone left a TOCTOU window in which
            # an old recovery could reactivate a historical Composite.
            async with self.mutation_locks[record.project_id]:
                async with self._project_mutation_lock(record.project_id):
                    async with self.sessions() as db:
                        current = await db.get(Project, record.project_id)
                        active = current and current.active_query_id == record.query_id
                    if not active:
                        continue
                    plan = HardRequestIntent.model_validate(record.parsed_plan)
                    root = record.parent_request_id or record.id
                    await self.create_hard(
                        record.project_id,
                        record.original_text,
                        user_id,
                        plan,
                        root,
                        {"type": "AVAILABILITY_RECOVERY"},
                        recovery_generation=1,
                    )
