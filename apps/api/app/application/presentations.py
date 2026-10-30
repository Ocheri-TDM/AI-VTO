"""Project documents, immutable selection snapshots and an independent durable queue."""

import asyncio
import copy
import json
import re
import subprocess
import sys
import time
from contextlib import suppress
from datetime import timedelta
from uuid import uuid4

import psutil
from sqlalchemy import func, select, text, update

from app.application.presentation_assets import safe_path, store_upload
from app.config import ROOT
from app.database.models import Chat, ProductObservation, Project, ResearchRecord, SupplierOffer
from app.database.models import PresentationDocument as Document
from app.database.models import PresentationRevision as Revision
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


class PresentationService:
    def __init__(self, sessions, settings, research_repository):
        self.sessions, self.settings, self.research_repository = sessions, settings, research_repository
        self.root = settings.presentation_storage.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.task = None
        self.local_lock = asyncio.Lock()

    async def owned(self, identifier, user):
        async with self.sessions() as db:
            return await db.scalar(
                select(Document)
                .join(Project)
                .where(Document.id == identifier, Document.user_id == user, Project.user_id == user)
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
        # The snapshot is read from persisted selection under its existing lock,
        # never from client page DTOs or the current visible filters.
        async with self.research_repository.mutation_lock(body.research_id):
            async with self.sessions() as db:
                record = await db.scalar(
                    select(ResearchRecord)
                    .join(Chat)
                    .where(ResearchRecord.id == body.research_id, Chat.project_id == project_id)
                )
                if not record:
                    return None
                payload = record.payload
                view = payload["view"]
                if view.get("version", 0) != body.selection_version:
                    raise ValueError("STATE_CONFLICT")
                ids = list(dict.fromkeys(view["selected_ids"]))
                if not ids:
                    raise ValueError("Сначала выберите товары")
                if len(ids) > 200:
                    raise ValueError("Один документ поддерживает до 200 выбранных вариантов")
            items = await self.snapshots(ids)
        settings = Theme(
            title=project.name[:160] or "Товарная подборка", client=project.client_name or ""
        ).model_dump()
        async with self.sessions.begin() as db:
            row = Document(
                project_id=project_id,
                user_id=user,
                draft={
                    "research_id": body.research_id,
                    "selection_version": body.selection_version,
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
                    .where(Document.project_id == project, Document.user_id == user)
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
        return {
            "id": row.id,
            "project_id": row.project_id,
            "version": row.version,
            "draft": row.draft,
            "revisions": [self.revision_view(r) for r in revisions],
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
        await self.replace_draft(row, body.version, draft)
        return await self.get(identifier, user)

    async def replace_draft(self, row, version, draft):
        async with self.sessions.begin() as db:
            current = await db.scalar(select(Document).where(Document.id == row.id).with_for_update())
            if current is None or current.version != version:
                raise ValueError("STATE_CONFLICT")
            # A worker may have frozen original bytes while the editor was
            # typing. Preserve this technical publication across the CAS edit.
            originals = {i["offer_id"]: i for i in current.draft["items"]}
            for item in draft["items"]:
                previous = originals.get(item["offer_id"], {})
                if item.get("snapshot_id") == previous.get("snapshot_id") and not item.get("original_key"):
                    item["original_key"] = previous.get("original_key")
            result = await db.execute(
                update(Document)
                .where(Document.id == row.id, Document.version == version)
                .values(draft=draft, version=version + 1, updated_at=utcnow())
            )
            if result.rowcount != 1:
                raise ValueError("STATE_CONFLICT")

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

    async def enqueue(self, identifier, user, version):
        if not await self.owned(identifier, user):
            return None
        async with self.local_lock, self.sessions.begin() as db:
            if db.bind.dialect.name == "postgresql":
                await db.execute(text("SELECT pg_advisory_xact_lock(hashtext('presentation-enqueue'))"))
            row = await db.scalar(
                select(Document).where(Document.id == identifier, Document.user_id == user).with_for_update()
            )
            if row.version != version:
                raise ValueError("STATE_CONFLICT")
            old = await db.scalar(
                select(Revision).where(Revision.document_id == identifier, Revision.source_version == version)
            )
            if old and old.status != "FAILED":
                return self.revision_view(old)
            queued = await db.scalar(
                select(func.count()).select_from(Revision).where(Revision.status.in_(ACTIVE))
            )
            if queued >= self.settings.presentation_queue_limit:
                raise ValueError("QUEUE_FULL")
            owned_count = await db.scalar(
                select(func.count())
                .select_from(Revision)
                .join(Document)
                .where(Document.user_id == user, Revision.status.in_(ACTIVE))
            )
            if owned_count >= 2:
                raise ValueError("QUEUE_FULL")
            if old:
                if old.attempts >= 2:
                    raise ValueError("Лимит повторов исчерпан. Измените черновик перед новой генерацией.")
                old.status, old.error, old.claim = "QUEUED", None, None
                await db.flush()
                return self.revision_view(old)
            revision = Revision(
                document_id=identifier,
                source_version=version,
                input=copy.deepcopy(row.draft) | {"document_id": identifier},
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
                .where(Revision.status == "QUEUED")
                .order_by(Revision.created_at)
                .with_for_update(skip_locked=True)
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
            await db.execute(
                update(Revision)
                .where(
                    Revision.id == revision.id, Revision.claim == revision.claim, Revision.status.in_(ACTIVE)
                )
                .values(status=status, updated_at=utcnow(), **kwargs)
            )

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
        source.write_text(
            json.dumps(
                {"storage": str(self.root), "revision": row.id, "input": row.input, "output": str(output)},
                ensure_ascii=False,
            ),
            "utf-8",
        )
        process = None
        reader = None
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
                        await stop_render_process(process)
                        return
                    if time.perf_counter() - started > self.settings.presentation_timeout_seconds:
                        raise TimeoutError()
                    if done:
                        line = reader.result()
                        if not line:
                            break
                        try:
                            phase = json.loads(line).get("phase")
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
            await self.freeze_assets(row, result)
            await self.publish(row, result["status"], result=result, error=None, lease_until=None)
        except asyncio.CancelledError:
            if process and process.returncode is None:
                await stop_render_process(process)
            with suppress(Exception):
                await self.publish(
                    row, "QUEUED" if row.attempts < 2 else "FAILED", claim=None, lease_until=None
                )
            raise
        except Exception as exc:
            if process and process.returncode is None:
                await stop_render_process(process)
            await self.publish(
                row,
                "FAILED",
                error="Превышено время генерации"
                if isinstance(exc, TimeoutError)
                else "Генерация не завершилась. Черновик сохранён.",
                lease_until=None,
            )
        finally:
            if reader and not reader.done():
                reader.cancel()
                with suppress(asyncio.CancelledError):
                    await reader

    async def freeze_assets(self, revision, result):
        """Keep source bytes across revisions until an explicit source refresh."""
        async with self.sessions.begin() as db:
            document = await db.scalar(
                select(Document).where(Document.id == revision.document_id).with_for_update()
            )
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
