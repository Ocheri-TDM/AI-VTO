"""Project documents, immutable selection snapshots and an independent durable queue."""

import asyncio
import copy
import json
import re
import subprocess
import sys
import time
from contextlib import nullcontext, suppress
from datetime import timedelta
from uuid import uuid4

import psutil
from sqlalchemy import func, or_, select, text, update

from app.application.presentation_assets import safe_path, store_upload
from app.application.presentation_materials import MATERIAL_JOB, PresentationMaterials
from app.config import ROOT
from app.database.models import PresentationDocument as Document
from app.database.models import PresentationRevision as Revision
from app.database.models import ProductObservation, Project, SupplierOffer
from app.domain.intent import CATEGORY_TERMS
from app.domain.models import utcnow
from app.domain.presentation import ACTIVE, Theme, short_display_name
from app.domain.product_purpose import identity_inputs
from app.domain.taxonomy import SEEDS


async def stop_render_process(process):
    # On Windows the venv launcher can spawn the actual interpreter. Terminate
    # only this owned process tree, so cancellation cannot orphan inference.
    children = []
    with suppress(psutil.Error):
        children = psutil.Process(process.pid).children(recursive=True)
        for child in reversed(children):
            with suppress(psutil.Error):
                child.kill()
    if process.returncode is None:
        with suppress(ProcessLookupError):
            process.kill()
    await process.wait()
    if children:
        await asyncio.to_thread(psutil.wait_procs, children, timeout=3)


class PresentationService(PresentationMaterials):
    def __init__(self, sessions, settings, research_repository, collections=None, model=None):
        self.sessions, self.settings, self.research_repository = sessions, settings, research_repository
        self.root = settings.presentation_storage.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.task = None
        self.local_lock = asyncio.Lock()
        self.collections, self.model = collections, model
        self.offer_now = utcnow  # Per-service clock; tests can control UTC without changing the machine.

    async def owned(self, identifier, user):
        async with self.sessions() as db:
            return await db.scalar(
                select(Document)
                .join(Project)
                .where(Document.id == identifier, Document.user_id == user, Project.user_id == user, Document.deleted_at.is_(None))
            )

    async def project(self, identifier, user):
        async with self.sessions() as db:
            return await db.scalar(select(Project).where(Project.id == identifier, Project.user_id == user))

    async def snapshots(self, ids):
        async with self.sessions() as db:
            rows = (
                await db.execute(
                    select(SupplierOffer, ProductObservation)
                    .outerjoin(ProductObservation, ProductObservation.offer_id == SupplierOffer.id)
                    .where(SupplierOffer.id.in_(ids))
                )
            ).all()
        found = {}
        for offer, observation in rows:
            if offer.supplier != "vto":
                raise ValueError("В презентации разрешены только варианты VTO")
            if offer.lifecycle == "REMOVED":
                raise ValueError(
                    "Выбранный вариант недоступен в каталоге. Уберите его из подборки перед созданием нового документа."
                )
            p = offer.payload
            m = p.get("metadata", {})
            identity = identity_inputs(
                p["name"],
                m.get("supplier_category"),
                m.get("breadcrumbs"),
                m.get("attributes"),
                p.get("description"),
                p.get("brand"),
            )
            category = next(iter(identity.primary), p.get("category") or "product")
            label = SEEDS.get(
                category, (CATEGORY_TERMS.get(category, m.get("supplier_category") or "Товары").capitalize(),)
            )[0]
            if identity.role == "SET":
                category, label = "catalog-set", "Подарочные наборы"
            elif identity.role == "PACKAGING":
                category, label = "packaging", "Упаковка и футляры"
            images = list(
                dict.fromkeys(
                    [url for url in [p.get("primary_image"), *p.get("images", [])] if isinstance(url, str)]
                )
            )
            found[offer.id] = {
                "offer_id": offer.id,
                "product_id": offer.product_id,
                "source_name": p["name"],
                "display_name": short_display_name(p["name"]),
                "caption": "",
                "model_name": m.get("model_name"),
                "brand": None
                if (p.get("brand") or "").strip().casefold()
                in ("", "без бренда", "бренд не указан", "no brand", "unknown", "n/a")
                else p.get("brand"),
                "source_brand": p.get("brand"),
                "category_key": category,
                "category_label": label,
                "colors": p.get("colors", []),
                "unit_price_kzt": str(observation.price_kzt)
                if observation and observation.price_kzt is not None
                else None,
                "observed_at": observation.observed_at.isoformat() if observation else None,
                "images": images,
                "image_url": images[0] if images else None,
                "prepared_key": None,
                "original_key": None,
                "snapshot_id": str(uuid4()),
                "source_url": offer.source_url,
                "sku": m.get("sku") or m.get("vto_sku"),
                "vto_product_id": m.get("vto_product_id"),
                "vto_variation_id": m.get("vto_variation_id"),
                "stock": observation.stock if observation else None,
                "source_metadata": m,
            }
        if set(found) != set(ids):
            raise ValueError("Один из выбранных вариантов больше не доступен в индексе")
        return [found[id] for id in ids]

    async def create(self, project_id, user, body):
        project = await self.project(project_id, user)
        if not project:
            return None
        if self.collections is None:
            raise ValueError("Подборка проекта недоступна")
        # The project row lock serializes both collection operations and the
        # document snapshot transaction. No supplier HTTP is involved.
        async with self.collections.locked(project_id, user) as (collection, db):
            if collection is None:
                return None
            version = (
                body.collection_version if body.collection_version is not None else body.selection_version
            )
            if version != collection.version:
                raise ValueError("STATE_CONFLICT")
            state = await self.collections.state(db, collection)
            ids = state["selected_ids"]
            if not ids:
                raise ValueError("Сначала выберите товары")
            if len(ids) > 200:
                raise ValueError("Один документ поддерживает до 200 выбранных вариантов")
            items = await self.snapshots(ids)
        settings = Theme(
            title="Подборка для " + project.client_name[:100]
            if project.client_name
            else "Каталог выбранных товаров",
            client=(project.client_name or "")[:100],
            auto_decoration={"version": 1, "enabled": True, "intensity": "SUBTLE"},
            cover_policy=1,
            renderer_version=1,
        ).model_dump()
        async with self.sessions.begin() as db:
            row = Document(
                project_id=project_id,
                user_id=user,
                draft={
                    "research_id": body.research_id,
                    "selection_version": body.selection_version,
                    "collection_version": version,
                    "items": items,
                    "settings": settings,
                    "category_order": list(dict.fromkeys(i["category_key"] for i in items)),
                    "original_fallback_ids": [],
                },
            )
            db.add(row)
            await db.flush()
            identifier = row.id
        return await self.get(identifier, user)

    async def listing(self, project, user):
        if not await self.project(project, user):
            return None
        async with self.sessions() as db:
            rows = (
                await db.scalars(
                    select(Document)
                    .where(
                        Document.project_id == project,
                        Document.user_id == user,
                        Document.deleted_at.is_(None),
                        or_(
                            Document.draft["kind"].as_string().is_(None),
                            Document.draft["kind"].as_string() != "COMMERCIAL_OFFER",
                        ),
                    )
                    .order_by(Document.updated_at.desc())
                )
            ).all()
        return [
            {
                "id": r.id,
                "title": r.draft["settings"]["title"],
                "version": r.version,
                "updated_at": r.updated_at.isoformat(),
            }
            for r in rows
        ]

    async def get(self, identifier, user):
        row = await self.owned(identifier, user)
        if not row:
            return None
        async with self.sessions() as db:
            revisions = (
                await db.scalars(
                    select(Revision)
                    .where(Revision.document_id == row.id)
                    .order_by(Revision.created_at.desc())
                )
            ).all()
        row = await self.reconcile_material_failures(row, revisions)
        draft = copy.deepcopy(row.draft)
        for material in draft.get("materials", []):
            if material["status"] == "READY":
                material["thumbnail_url"] = (
                    f"/api/presentations/{identifier}/materials/{material['id']}?thumbnail=true"
                )
        return {
            "id": row.id,
            "project_id": row.project_id,
            "version": row.version,
            "draft": draft,
            "revisions": [self.revision_view(r) for r in revisions if r.input.get("kind") != MATERIAL_JOB],
            "materials_preparing": any(
                r.input.get("kind") == MATERIAL_JOB and r.status in ACTIVE for r in revisions
            ),
            "material_limits": self.material_limits(),
        }

    def revision_view(self, row):
        # Only authenticated file routes resolve storage paths.
        result = copy.deepcopy(row.result)
        base = f"/api/presentations/{row.document_id}/revisions/{row.id}"
        if result.get("pdf"):
            result["pdf_url"] = base + "/pdf"
            result.pop("pdf")
        for page in result.get("pages", []):
            page["preview"] = base + f"/pages/{page['page']}"
            page["thumbnail"] = base + f"/pages/{page['page']}?thumbnail=true"
        for offer, asset in result.get("assets", {}).items():
            asset["url"] = base + "/assets/" + offer
            asset.pop("key", None)
            asset.pop("original", None)
        return {
            "id": row.id,
            "source_version": row.source_version,
            "status": row.status,
            "error": row.error,
            "result": result,
            "created_at": row.created_at.isoformat(),
            "request_id": row.input.get("generation_request_id"),
        }

    async def save(self, identifier, user, body):
        row = await self.owned(identifier, user)
        if not row:
            return None
        draft = copy.deepcopy(row.draft)
        old = {i["offer_id"]: i for i in draft["items"]}
        ids = [i.offer_id for i in body.items]
        if len(ids) != len(set(ids)) or set(ids) != set(old):
            raise ValueError(
                "Изменён состав snapshot. Порядок и подписи можно редактировать, новые товары требуют нового документа."
            )
        items = []
        for change in body.items:
            item = old[change.offer_id]
            if change.image_url is not None and change.image_url not in item["images"]:
                raise ValueError("Выберите изображение этого варианта")
            if change.image_url != item["image_url"]:
                item["prepared_key"] = None
                item["original_key"] = None
                item["snapshot_id"] = str(uuid4())
            item.update(change.model_dump())
            items.append(item)
        settings = body.settings.model_dump()
        for field in ("palette_plan", "auto_decoration", "cover_policy", "renderer_version"):
            if field not in body.settings.model_fields_set:
                settings[field] = copy.deepcopy(row.draft["settings"].get(field))
        from app.domain.presentation_palette import manual_plan, resolve_choice

        choice = settings.get("palette_plan")
        if choice is None and settings["palette"] != row.draft["settings"]["palette"]:
            old_colors = row.draft["settings"]["palette"]
            slots = [
                n for n, c in enumerate(settings["palette"]) if n >= len(old_colors) or old_colors[n] != c
            ]
            choice = manual_plan(settings["palette"], edited_slots=slots)
        settings["palette_plan"] = await asyncio.to_thread(resolve_choice, choice, settings["palette"])
        settings["style_version"] = 3 if settings["style"] == "premium-dark" else 2
        settings["title"] = (
            "Подборка для " + settings["client"].strip()
            if settings["client"].strip()
            else "Каталог выбранных товаров"
        )
        logo = settings["logo"]
        if logo and (
            not re.fullmatch(r"documents/" + re.escape(identifier) + r"/uploads/[0-9a-f]{64}\.png", logo)
            or not safe_path(self.root, logo).is_file()
        ):
            raise ValueError("Логотип не принадлежит документу")
        categories = list(dict.fromkeys(i["category_key"] for i in items))
        order = body.category_order or categories
        if len(order) != len(set(order)) or set(order) != set(categories):
            raise ValueError("Некорректный порядок категорий")
        if not set(body.original_fallback_ids) <= set(ids):
            raise ValueError("Некорректное согласие на оригинальные фотографии")
        draft.update(
            items=items,
            settings=settings,
            category_order=order,
            original_fallback_ids=body.original_fallback_ids,
        )
        self.edit_materials(draft, body.materials)
        await self.replace_draft(row, body.version, draft)
        return await self.get(identifier, user)

    async def replace_draft(self, row, version, draft):
        # SQLite ignores FOR UPDATE. Keep the same CAS contract in isolated
        # regression/local databases; PostgreSQL retains its row-level lock.
        lock = self.local_lock if self.sessions.kw["bind"].dialect.name == "sqlite" else nullcontext()
        async with lock, self.sessions.begin() as db:
            current = await db.scalar(select(Document).where(Document.id == row.id).with_for_update())
            if current is None or current.deleted_at is not None or current.version != version:
                raise ValueError("STATE_CONFLICT")
            removed = {m["id"] for m in current.draft.get("materials", [])} - {
                m["id"] for m in draft.get("materials", [])
            }
            # A worker may have frozen original bytes while the editor was
            # typing. Preserve this technical publication across the CAS edit.
            originals = {i["offer_id"]: i for i in current.draft["items"]}
            for item in draft["items"]:
                previous = originals.get(item["offer_id"], {})
                if item.get("snapshot_id") == previous.get("snapshot_id") and not item.get("original_key"):
                    item["original_key"] = previous.get("original_key")
                by_photo = {p["id"]: p for p in previous.get("photos", [])}
                for photo in item.get("photos", []):
                    old_photo = by_photo.get(photo["id"], {})
                    if old_photo.get("source_url") == photo.get("source_url"):
                        for field in ("original_key", "cached_key", "source_hash"):
                            if not photo.get(field) and old_photo.get(field):
                                photo[field] = old_photo[field]
            result = await db.execute(
                update(Document)
                .where(Document.id == row.id, Document.version == version, Document.deleted_at.is_(None))
                .values(draft=draft, version=version + 1, updated_at=utcnow())
            )
            if result.rowcount != 1:
                raise ValueError("STATE_CONFLICT")
            if removed:
                await db.execute(
                    update(Revision)
                    .where(
                        Revision.document_id == row.id,
                        Revision.status.in_(ACTIVE),
                        Revision.input["kind"].as_string() == MATERIAL_JOB,
                        Revision.input["material"]["id"].as_string().in_(removed),
                    )
                    .values(status="CANCELLED", updated_at=utcnow())
                )

    async def refresh(self, identifier, user, version):
        row = await self.owned(identifier, user)
        if not row:
            return None
        draft = copy.deepcopy(row.draft)
        fresh = await self.snapshots([i["offer_id"] for i in draft["items"]])
        for previous, item in zip(draft["items"], fresh, strict=True):
            item.update(display_name=previous["display_name"], caption=previous["caption"])
        draft["items"] = fresh
        draft["original_fallback_ids"] = []
        draft["category_order"] = list(dict.fromkeys(i["category_key"] for i in fresh))
        await self.replace_draft(row, version, draft)
        return await self.get(identifier, user)

    async def refresh_composition(self, identifier, user, version, collection_version):
        row = await self.owned(identifier, user)
        if not row:
            return None
        async with self.collections.locked(row.project_id, user) as (collection, db):
            if collection.version != collection_version:
                raise ValueError("STATE_CONFLICT")
            state = await self.collections.state(db, collection)
            if not state["selected_ids"] or len(state["selected_ids"]) > 200:
                raise ValueError("Для документа выберите от 1 до 200 вариантов")
            fresh = await self.snapshots(state["selected_ids"])
            previous = {i["offer_id"]: i for i in row.draft["items"]}
            items = [previous.get(i["offer_id"], i) for i in fresh]
            draft = copy.deepcopy(row.draft)
            draft.update(
                items=items,
                collection_version=collection_version,
                category_order=list(dict.fromkeys(i["category_key"] for i in items)),
                original_fallback_ids=[
                    id for id in draft["original_fallback_ids"] if id in state["selected_ids"]
                ],
            )
            await self.replace_draft(row, version, draft)
        return await self.get(identifier, user)

    async def alternate(self, identifier, user, version, request_id=None):
        if request_id is not None:
            await self.enqueue(identifier, user, version, request_id)
            return await self.get(identifier, user)
        row = await self.owned(identifier, user)
        if not row:
            return None
        self.check_generation_materials(row.draft)
        draft = copy.deepcopy(row.draft)
        draft["settings"]["design_seed"] = (draft["settings"].get("design_seed", 0) + 1) % 2_147_483_648
        await self.replace_draft(row, version, draft)
        await self.enqueue(identifier, user, version + 1)
        return await self.get(identifier, user)

    async def upload(self, identifier, user, data, version, offer_id=None):
        row = await self.owned(identifier, user)
        if not row:
            return None
        draft = copy.deepcopy(row.draft)
        if row.version != version:
            raise ValueError("STATE_CONFLICT")
        item = next((i for i in draft["items"] if i["offer_id"] == offer_id), None) if offer_id else None
        if offer_id and item is None:
            raise ValueError("Вариант отсутствует в snapshot")
        key = await asyncio.to_thread(store_upload, self.root, identifier, data, bool(offer_id))
        if item is not None:
            item["prepared_key"] = key
        else:
            draft["settings"].update(logo=key, use_logo=True)
        await self.replace_draft(row, version, draft)
        return await self.get(identifier, user)

    async def enqueue(self, identifier, user, version, request_id=None):
        owned = await self.owned(identifier, user)
        if not owned:
            return None
        key = str(request_id) if request_id else None

        def request_filter():
            return (
                Revision.input["generation_request_id"].as_string() == key
                if key
                else Revision.source_version == version
            )

        async with self.sessions() as db:
            previous = await db.scalar(
                select(Revision).where(
                    Revision.document_id == identifier,
                    request_filter(),
                    or_(
                        Revision.input["kind"].as_string().is_(None),
                        Revision.input["kind"].as_string() != MATERIAL_JOB,
                    ),
                )
            )
        # Resolving/copying logo bytes is bounded filesystem work outside a DB lock.
        # Retry reuses its frozen input even if the current draft has changed.
        from app.application.presentation_logo import freeze_logo

        resolved_logo = (
            None if previous else await asyncio.to_thread(freeze_logo, self.root, owned.draft["settings"])
        )
        async with self.local_lock, self.sessions.begin() as db:
            if db.bind.dialect.name == "postgresql":
                await db.execute(text("SELECT pg_advisory_xact_lock(hashtext('presentation-enqueue'))"))
            row = await db.scalar(
                select(Document).where(Document.id == identifier, Document.user_id == user, Document.deleted_at.is_(None)).with_for_update()
            )
            old = await db.scalar(
                select(Revision).where(
                    Revision.document_id == identifier,
                    request_filter(),
                    or_(
                        Revision.input["kind"].as_string().is_(None),
                        Revision.input["kind"].as_string() != MATERIAL_JOB,
                    ),
                )
            )
            if old and old.status != "FAILED":
                return self.revision_view(old)
            if row.version != version and not (key and old):
                raise ValueError("STATE_CONFLICT")
            self.check_generation_materials(row.draft)
            active = await db.scalar(
                select(Revision).where(
                    Revision.document_id == identifier,
                    Revision.status.in_(ACTIVE),
                    or_(
                        Revision.input["kind"].as_string().is_(None),
                        Revision.input["kind"].as_string() != MATERIAL_JOB,
                    ),
                )
            )
            if active:
                return self.revision_view(active)
            await self.queue_capacity(db, user)
            if old:
                if old.attempts >= 2:
                    raise ValueError("Лимит повторов исчерпан. Измените черновик перед новой генерацией.")
                old.status, old.error, old.claim = "QUEUED", None, None
                await db.flush()
                return self.revision_view(old)
            value = copy.deepcopy(row.draft) | {"document_id": identifier}
            # Only a newly requested revision adopts the current template.
            # Existing revision inputs/plans and failed-revision retries stay frozen.
            value["settings"]["style_version"] = 3 if value["settings"].get("style") == "premium-dark" else 2
            from app.domain.presentation_composition import novelty
            from app.domain.presentation_design import deterministic_plan
            from app.domain.presentation_palette import manual_plan

            recent = (
                await db.scalars(
                    select(Revision)
                    .where(Revision.document_id == identifier, Revision.input["kind"].as_string().is_(None))
                    .order_by(Revision.created_at.desc())
                    .limit(3)
                )
            ).all()
            value["recent_designs"] = [
                r.result["design_signature"] for r in recent if r.result.get("design_signature")
            ]
            seed = max((r.input.get("settings", {}).get("design_seed", -1) for r in recent), default=-1) + 1
            seed = max(seed, value["settings"].get("design_seed", 0))
            value["settings"].update(
                cover=True, cover_policy=1, renderer_version=1, resolved_logo=resolved_logo
            )
            if not value["settings"].get("palette_plan"):
                value["settings"]["palette_plan"] = manual_plan(value["settings"]["palette"])
            for attempt in range(10):
                value["settings"]["design_seed"] = (seed + attempt) % 2_147_483_648
                candidate = deterministic_plan(
                    value["items"],
                    value["settings"],
                    value["category_order"],
                    materials=value.get("materials", []),
                )
                if novelty(candidate, value["recent_designs"])["status"] == "DISTINCT":
                    break
            value["variation_attempts"] = attempt + 1
            value["generation_request_id"] = key
            # A completed request followed by an explicit new key is a new
            # source version. Replayed HTTP keys above never increment it.
            if key and await db.scalar(
                select(func.count())
                .select_from(Revision)
                .where(Revision.document_id == identifier, Revision.source_version == row.version)
            ):
                row.version += 1
                row.updated_at = utcnow()
            revision = Revision(
                document_id=identifier,
                source_version=row.version,
                input=value,
            )
            db.add(revision)
            await db.flush()
            return self.revision_view(revision)

    async def revision(self, identifier, revision, user):
        if not await self.owned(identifier, user):
            return None
        async with self.sessions() as db:
            return await db.scalar(
                select(Revision).where(Revision.id == revision, Revision.document_id == identifier)
            )

    async def queue_capacity(self, db, user):
        queued = await db.scalar(
            select(func.count()).select_from(Revision).where(Revision.status.in_(ACTIVE))
        )
        owned = await db.scalar(
            select(func.count())
            .select_from(Revision)
            .join(Document)
            .where(Document.user_id == user, Revision.status.in_(ACTIVE))
        )
        if queued >= self.settings.presentation_queue_limit or owned >= 2:
            raise ValueError("QUEUE_FULL")

    async def enqueue_offer(
        self, identifier, user, version, request_id, payload, vault, *, publish=True, expected_epoch=0
    ):
        """Same queue/claims/resource limits, with an explicit commercial kind."""
        from app.database.models import CommercialOfferRequest as Receipt
        from app.database.models import CommercialOfferShare as Share
        from app.domain.commercial_offer import KIND
        from app.domain.offer_lifecycle import activate, expired

        async with self.local_lock, self.sessions.begin() as db:
            if db.bind.dialect.name == "postgresql":
                await db.execute(text("SELECT pg_advisory_xact_lock(hashtext('presentation-enqueue'))"))
            row = await db.scalar(
                select(Document).where(Document.id == identifier, Document.user_id == user, Document.deleted_at.is_(None)).with_for_update()
            )
            if not row or row.draft.get("kind") != KIND:
                return None
            if not row.is_primary_offer:
                raise ValueError("ARCHIVED_OFFER")
            share = await db.get(Share, identifier, with_for_update=True)
            receipt = await db.get(Receipt, (identifier, request_id))
            old = (
                await db.get(Revision, receipt.revision_id)
                if receipt
                else await db.scalar(
                    select(Revision).where(
                        Revision.document_id == identifier,
                        Revision.input["kind"].as_string() == KIND,
                        Revision.input["generation_request_id"].as_string() == request_id,
                    )
                )
            )
            if (
                receipt
                and receipt.publication != publish
                or not receipt
                and old
                and bool(old.input.get("publication_requested", True)) != publish
            ):
                raise ValueError("STATE_CONFLICT")
            if old and old.status != "FAILED":
                return old.id
            if row.version != version and not old:
                raise ValueError("STATE_CONFLICT")
            if publish:
                if share and share.epoch != expected_epoch:
                    raise ValueError("STATE_CONFLICT")
                if share is None:
                    _, digest, encrypted = vault.create()
                    share = Share(
                        document_id=identifier,
                        token_hash=digest,
                        encrypted_token=encrypted,
                        active=False,
                        epoch=0,
                    )
                    db.add(share)
                    await db.flush()
                now = self.offer_now()
                new_period = expired(share, now) or (
                    not share.active
                    and not share.pending_revision_id
                    and (share.activated_at is not None or share.epoch > 0)
                )
                if new_period:
                    running = await db.scalar(
                        select(Revision).where(
                            Revision.document_id == identifier, Revision.status.in_(ACTIVE)
                        )
                    )
                    if running:
                        raise ValueError(
                            "Дождитесь завершения прежней подготовки и повторите создание ссылки"
                        )
                    _, share.token_hash, share.encrypted_token = vault.create()
                    share.epoch += 1
                    share.active = False
                    share.activated_at = share.expires_at = None
                    share.pending_revision_id = share.pending_epoch = None

            def remember(revision):
                if not receipt:
                    db.add(
                        Receipt(
                            document_id=identifier,
                            request_id=request_id,
                            revision_id=revision.id,
                            publication=publish,
                            epoch=share.epoch if publish else None,
                        )
                    )

            ready = await db.scalar(
                select(Revision)
                .where(
                    Revision.document_id == identifier,
                    Revision.source_version == version,
                    Revision.input["kind"].as_string() == KIND,
                    Revision.status == "READY",
                    Revision.input["offer_layout_version"].as_integer()
                    == payload.get("offer_layout_version", 2),
                )
                .order_by(Revision.created_at.desc())
            )
            if ready:
                if publish:
                    share.published_revision_id, share.pending_revision_id = (
                        ready.id,
                        None,
                    )
                    share.pending_epoch = None
                    activate(share, self.offer_now())
                    share.updated_at = self.offer_now()
                remember(ready)
                return ready.id
            active = await db.scalar(
                select(Revision).where(Revision.document_id == identifier, Revision.status.in_(ACTIVE))
            )
            if active:
                if active.input.get("kind") == KIND and active.source_version == version:
                    if publish:
                        if active.input.get("publication_epoch") not in (None, share.epoch):
                            raise ValueError("Дождитесь завершения прежней подготовки")
                        share.pending_revision_id = active.id
                        share.pending_epoch = share.epoch
                    remember(active)
                    return active.id
                raise ValueError("Дождитесь завершения подготовки текущей версии")
            await self.queue_capacity(db, user)
            if old:
                if old.attempts >= 2 or publish and old.input.get("publication_epoch") != share.epoch:
                    raise ValueError("Повтор этой операции больше недоступен. Создайте новую публикацию.")
                old.status, old.error, old.claim = "QUEUED", None, None
                revision = old
            else:
                revision = Revision(
                    document_id=identifier,
                    source_version=version,
                    input=payload
                    | {
                        "generation_request_id": request_id,
                        "publication_epoch": share.epoch if publish else None,
                        "publication_requested": publish,
                    },
                )
                db.add(revision)
                await db.flush()
            if publish:
                share.pending_revision_id = revision.id
                share.pending_epoch = share.epoch
                share.updated_at = self.offer_now()
            remember(revision)
            return revision.id

    async def cancel(self, identifier, revision, user):
        if not await self.revision(identifier, revision, user):
            return None
        async with self.sessions.begin() as db:
            await db.execute(
                update(Revision)
                .where(Revision.id == revision, Revision.status.in_(ACTIVE))
                .values(status="CANCELLED", updated_at=utcnow())
            )
        return await self.get(identifier, user)

    async def approve(self, identifier, revision, user, hashes):
        row = await self.revision(identifier, revision, user)
        if not row:
            return None
        if (
            row.status not in ("READY", "NEEDS_REVIEW")
            or not row.result.get("pdf")
            or row.result.get("issues")
        ):
            raise ValueError("Сначала разрешите проблемы изображений или текста и повторите генерацию")
        expected = {a["hash"] for a in row.result["assets"].values() if a["review_required"]}
        if not expected <= set(hashes):
            raise ValueError("Подтвердите все вырезанные изображения после просмотра")
        result = row.result | {"approved_asset_hashes": list(expected), "approved_at": utcnow().isoformat()}
        async with self.sessions.begin() as db:
            await db.execute(
                update(Revision)
                .where(Revision.id == revision, Revision.status.in_(("NEEDS_REVIEW", "READY")))
                .values(status="READY", result=result, updated_at=utcnow())
            )
        return await self.get(identifier, user)

    def start(self):
        self.task = asyncio.create_task(self.loop(), name="presentation-worker")

    async def close(self):
        if self.task:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task

    async def claim_next(self):
        now = utcnow()
        async with self.local_lock, self.sessions.begin() as db:
            if db.bind.dialect.name == "postgresql":
                await db.execute(text("SELECT pg_advisory_xact_lock(hashtext('presentation-worker-claim'))"))
            # Only expired claims recover, so a second API cannot steal live work.
            expired = (
                await db.scalars(
                    select(Revision)
                    .where(Revision.status.in_(ACTIVE[1:]), Revision.lease_until < now)
                    .with_for_update(skip_locked=True)
                )
            ).all()
            for row in expired:
                row.status = "QUEUED" if row.attempts < 2 else "FAILED"
                row.error = (
                    "Прерванная генерация восстановлена" if row.attempts < 2 else "Исчерпан лимит повторов"
                )
                row.claim = None
            await db.flush()
            running = await db.scalar(
                select(func.count())
                .select_from(Revision)
                .where(Revision.status.in_(ACTIVE[1:]), Revision.lease_until >= now)
            )
            if running:
                return None
            row = await db.scalar(
                select(Revision)
                .join(Document, Document.id == Revision.document_id)
                .where(Revision.status == "QUEUED", Document.deleted_at.is_(None))
                .order_by(Revision.created_at)
                .with_for_update(skip_locked=True, of=Revision)
                .limit(1)
            )
            if not row:
                return None
            row.status = "PREPARING_ASSETS"
            row.claim = str(uuid4())
            row.attempts += 1
            row.lease_until = now + timedelta(seconds=self.settings.presentation_timeout_seconds + 30)
            row.updated_at = now
            await db.flush()
            return row

    async def publish(self, revision, status, **kwargs):
        async with self.sessions.begin() as db:
            if revision.input.get("kind") == "COMMERCIAL_OFFER":
                document = await db.scalar(select(Document).where(Document.id == revision.document_id).with_for_update())
                if not document or document.deleted_at is not None:
                    return
            changed = await db.execute(
                update(Revision)
                .where(
                    Revision.id == revision.id, Revision.claim == revision.claim, Revision.status.in_(ACTIVE)
                )
                .values(status=status, updated_at=utcnow(), **kwargs)
            )
            if (
                changed.rowcount == 1
                and status == "READY"
                and revision.input.get("kind") == "COMMERCIAL_OFFER"
            ):
                from app.database.models import CommercialOfferShare as Share
                from app.domain.offer_lifecycle import activate, expired

                share = await db.get(Share, revision.document_id, with_for_update=True)
                if share and share.pending_revision_id == revision.id and share.pending_epoch == share.epoch:
                    share.pending_revision_id = None
                    share.pending_epoch = None
                    if expired(share, self.offer_now()):
                        share.active = False
                    else:
                        share.published_revision_id = revision.id
                        activate(share, self.offer_now())
                    share.updated_at = self.offer_now()

    async def loop(self):
        while True:
            try:
                row = await self.claim_next()
                if row:
                    await self.execute(row)
                else:
                    await asyncio.sleep(0.5)
            except asyncio.CancelledError:
                raise
            except Exception:
                # Job-specific failures are recorded by execute; a transient DB
                # fault must not kill scheduling or impact the catalog worker.
                await asyncio.sleep(2)

    async def execute(self, row):
        folder = safe_path(self.root, f"documents/{row.document_id}/revisions/{row.id}")
        folder.mkdir(parents=True, exist_ok=True)
        source = folder / "input.json"
        output = folder / "result.json"
        plan_file = folder / "design-plan.json"
        if plan_file.exists():
            plan_file.unlink()  # Only this exact retry's IPC file; persisted results remain.
        worker_input = copy.deepcopy(row.input)
        if row.input.get("kind") == MATERIAL_JOB:
            async with self.sessions() as db:
                jobs = (
                    await db.scalars(select(Revision).where(Revision.document_id == row.document_id))
                ).all()
            used = sum(
                j.input.get("material", {}).get("bytes", 0)
                + j.result.get("material", {}).get("prepared_bytes", 0)
                for j in jobs
                if j.input.get("kind") == MATERIAL_JOB
            )
            worker_input["limits"]["remaining_bytes"] = max(
                0, self.settings.presentation_material_max_total_bytes - used
            )
            worker_input["limits"]["known_keys"] = [
                j.result["material"]["key"]
                for j in jobs
                if j.input.get("kind") == MATERIAL_JOB and j.result.get("material", {}).get("key")
            ]
        source.write_text(
            json.dumps(
                {
                    "storage": str(self.root),
                    "revision": row.id,
                    "input": worker_input,
                    "output": str(output),
                    "plan_file": str(plan_file),
                    "plan_timeout": self.settings.presentation_design_timeout_seconds,
                },
                ensure_ascii=False,
            ),
            "utf-8",
        )
        process = None
        reader = None
        planning = None
        peak = 0
        started = time.perf_counter()
        try:
            with (folder / "worker.log").open("wb") as log:
                process = await asyncio.create_subprocess_exec(
                    sys.executable,
                    "-m",
                    "app.application.presentation_worker",
                    str(source),
                    cwd=str(ROOT / "apps/api"),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=log,
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                )
                reader = asyncio.create_task(process.stdout.readline())
                while True:
                    done, _ = await asyncio.wait([reader], timeout=0.35)
                    with suppress(psutil.Error):
                        parent = psutil.Process(process.pid)
                        resident = parent.memory_info().rss
                        for child in parent.children(recursive=True):
                            with suppress(psutil.Error):
                                resident += child.memory_info().rss
                        peak = max(peak, resident)
                    async with self.sessions() as db:
                        current = await db.get(Revision, row.id)
                        cancelled = (
                            current is None or current.status == "CANCELLED" or current.claim != row.claim
                        )
                    if cancelled:
                        if planning:
                            planning.cancel()
                        await stop_render_process(process)
                        return
                    timeout = (
                        self.settings.presentation_material_timeout_seconds
                        if row.input.get("kind") == MATERIAL_JOB
                        else self.settings.presentation_timeout_seconds
                    )
                    if time.perf_counter() - started > timeout:
                        raise TimeoutError()
                    if done:
                        line = reader.result()
                        if not line:
                            break
                        try:
                            event = json.loads(line)
                            phase = event.get("phase")
                            if phase == "PLANNING_DESIGN" and planning is None:

                                async def plan(geometry=event["geometry"]):
                                    from app.application.presentation_design import prepare_design

                                    if row.result.get("design_plan"):
                                        from app.domain.presentation_design import validate_plan

                                        saved = validate_plan(
                                            row.result["design_plan"],
                                            row.input["items"],
                                            row.input["settings"],
                                            row.input.get("materials", []),
                                        )
                                        result = {
                                            k: row.result.get(k)
                                            for k in ("ai_used", "fallback_reason", "ai_seconds")
                                        }
                                        result["design_plan"] = saved.model_dump()
                                    else:
                                        result = await prepare_design(
                                            self.model,
                                            row.input,
                                            geometry,
                                            self.settings.presentation_design_timeout_seconds,
                                        )
                                        # Persist the validated plan before rendering. A retry of this
                                        # revision reuses it even if Qwen would answer differently.
                                        await self.publish(row, "PLANNING_DESIGN", result=result)
                                    part = plan_file.with_suffix(".tmp")
                                    part.write_text(json.dumps(result, ensure_ascii=False), "utf-8")
                                    part.replace(plan_file)

                                planning = asyncio.create_task(plan())
                            if phase in ACTIVE[1:]:
                                await self.publish(row, phase)
                        except (ValueError, AttributeError):
                            pass
                        reader = asyncio.create_task(process.stdout.readline())
                await process.wait()
            if process.returncode != 0 or not output.exists():
                raise RuntimeError("worker failed")
            result = json.loads(output.read_text("utf-8"))
            result["peak_rss_bytes"] = peak
            if row.input.get("kind") == MATERIAL_JOB:
                await self.finish_material(row, result)
            elif row.input.get("kind") == "COMMERCIAL_OFFER":
                await self.freeze_offer_assets(row, result)
                await self.publish(row, result["status"], result=result, error=None, lease_until=None)
            else:
                await self.freeze_assets(row, result)
                await self.publish(row, result["status"], result=result, error=None, lease_until=None)
        except asyncio.CancelledError:
            if planning:
                planning.cancel()
            if process and process.returncode is None:
                await stop_render_process(process)
            with suppress(Exception):
                await self.publish(
                    row, "QUEUED" if row.attempts < 2 else "FAILED", claim=None, lease_until=None
                )
            raise
        except Exception as exc:
            if planning:
                planning.cancel()
            if process and process.returncode is None:
                await stop_render_process(process)
            if row.input.get("kind") == MATERIAL_JOB:
                await self.finish_material(
                    row,
                    {
                        "status": "FAILED",
                        "message": "Превышено время подготовки"
                        if isinstance(exc, TimeoutError)
                        else "Подготовка не завершилась. Замените файл.",
                    },
                )
            await self.publish(
                row,
                "FAILED",
                error="Превышено время генерации"
                if isinstance(exc, TimeoutError)
                else "Генерация не завершилась. Черновик сохранён.",
                lease_until=None,
            )
        finally:
            if planning:
                if not planning.done():
                    planning.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await planning
            if reader and not reader.done():
                reader.cancel()
                with suppress(asyncio.CancelledError):
                    await reader

    async def freeze_offer_assets(self, revision, result):
        async with self.sessions.begin() as db:
            document = await db.scalar(
                select(Document).where(Document.id == revision.document_id).with_for_update()
            )
            if not document or document.deleted_at is not None:
                return
            draft = copy.deepcopy(document.draft)
            for item in draft["items"]:
                for photo in item.get("photos", []):
                    if photo["id"] in result.get("photo_original_keys", {}):
                        photo.update(result["photo_original_keys"][photo["id"]])
            document.draft = draft

    async def freeze_assets(self, revision, result):
        """Keep source bytes across revisions until an explicit source refresh."""
        async with self.sessions.begin() as db:
            document = await db.scalar(
                select(Document).where(Document.id == revision.document_id).with_for_update()
            )
            if not document or document.deleted_at is not None:
                return
            if not document:
                return
            previous = (
                await db.scalars(
                    select(Revision).where(Revision.document_id == document.id, Revision.status == "READY")
                )
            ).all()
            approved = {h for r in previous for h in r.result.get("approved_asset_hashes", [])}
            old_items = {i["offer_id"]: i for i in revision.input["items"]}
            draft = copy.deepcopy(document.draft)
            for item in draft["items"]:
                old = old_items.get(item["offer_id"])
                asset = result.get("assets", {}).get(item["offer_id"])
                if (
                    asset
                    and old
                    and old.get("snapshot_id") == item.get("snapshot_id")
                    and not item.get("prepared_key")
                ):
                    item["original_key"] = asset["original"]
            document.draft = draft
            for asset in result.get("assets", {}).values():
                if asset["hash"] in approved:
                    asset["review_required"] = False
            if (
                result.get("pdf")
                and not result.get("issues")
                and not any(a["review_required"] for a in result["assets"].values())
            ):
                result["status"] = "READY"
                result["approved_asset_hashes"] = sorted(
                    approved & {a["hash"] for a in result["assets"].values()}
                )
