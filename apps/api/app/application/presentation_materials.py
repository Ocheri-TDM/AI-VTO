"""Custom files use the presentation storage, CAS edits and durable worker queue."""

import asyncio
import copy
import hashlib
import re
from uuid import uuid4

from sqlalchemy import func, select, text

from app.application.presentation_assets import store_material_source
from app.database.models import PresentationDocument as Document
from app.database.models import PresentationRevision as Revision
from app.domain.models import utcnow
from app.domain.presentation import ACTIVE
from app.domain.presentation_materials import MaterialEdit

MATERIAL_JOB = "PREPARE_MATERIAL"


class PresentationMaterials:
    def material_limits(self):
        return {
            name: getattr(self.settings, "presentation_material_" + name)
            for name in (
                "max_bytes",
                "max_pixels",
                "max_total_bytes",
                "max_files",
                "max_decorations",
                "timeout_seconds",
            )
        }

    async def upload_material(self, identifier, user, data, version, role, filename, replace_id=None):
        row = await self.owned(identifier, user)
        if not row:
            return None
        limits = self.material_limits()
        if row.draft["settings"].get("style") != "premium-dark":
            raise ValueError("Свои материалы пока поддерживает Premium Dark")
        if not data or len(data) > limits["max_bytes"]:
            raise ValueError("Файл пуст или превышает допустимый размер")
        if row.version != version:
            raise ValueError("STATE_CONFLICT")
        asset_id = str(uuid4())
        # Unique immutable source path; preparation never runs on the event loop.
        key = await asyncio.to_thread(store_material_source, self.root, identifier, asset_id, data)
        committed = False
        try:
            async with self.local_lock, self.sessions.begin() as db:
                if db.bind.dialect.name == "postgresql":
                    await db.execute(text("SELECT pg_advisory_xact_lock(hashtext('presentation-enqueue'))"))
                current = await db.scalar(
                    select(Document)
                    .where(Document.id == identifier, Document.user_id == user)
                    .with_for_update()
                )
                if not current or current.version != version:
                    raise ValueError("STATE_CONFLICT")
                values = copy.deepcopy(current.draft.get("materials", []))
                previous = next((m for m in values if m["id"] == replace_id), None)
                if replace_id and (not previous or previous["role"] != role):
                    raise ValueError("Заменяемый материал не принадлежит документу или имеет другую роль")
                if len(values) >= limits["max_files"]:
                    raise ValueError("Достигнут лимит файлов документа. Удалите ненужные материалы.")
                peers = [m for m in values if m["role"] == role and m["status"] != "FAILED"]
                if role != "DECORATION" and peers and not previous:
                    raise ValueError("Для этой роли используйте замену существующего файла")
                if role == "DECORATION" and len(peers) - bool(previous) >= limits["max_decorations"]:
                    raise ValueError("Достигнут лимит декоративных элементов")
                if previous and any(m.get("replaces_id") == replace_id for m in values):
                    raise ValueError("Замена этого материала уже готовится")
                jobs = (await db.scalars(select(Revision).where(Revision.document_id == identifier))).all()
                used_bytes = sum(
                    j.input.get("material", {}).get("bytes", 0)
                    + j.result.get("material", {}).get("prepared_bytes", 0)
                    for j in jobs
                    if j.input.get("kind") == MATERIAL_JOB
                )
                if used_bytes + len(data) > limits["max_total_bytes"]:
                    raise ValueError(
                        "Достигнут суммарный лимит материалов документа, включая сохранённые версии"
                    )
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
                name = re.sub(r"[\x00-\x1f/\\]", "_", filename).strip()[:140] or "Изображение"
                config = MaterialEdit(
                    id=asset_id, scope="COVER" if role == "COVER_PHOTO" else "DOCUMENT"
                ).model_dump(mode="json")
                if previous:
                    config.update({k: previous[k] for k in MaterialEdit.model_fields if k != "id"})
                entry = config | {
                    "role": role,
                    "filename": name,
                    "original": key,
                    "hash": hashlib.sha256(data).hexdigest(),
                    "bytes": len(data),
                    "status": "QUEUED",
                    "document_id": identifier,
                    "replaces_id": replace_id,
                    "asset_schema_version": 1,
                }
                draft = copy.deepcopy(current.draft)
                draft["materials"] = values + [entry]
                current.draft, current.version, current.updated_at = draft, version + 1, utcnow()
                db.add(
                    Revision(
                        document_id=identifier,
                        source_version=version + 1,
                        input={"kind": MATERIAL_JOB, "material": entry, "limits": limits},
                    )
                )
            committed = True
        finally:
            if not committed:
                # This UUID source was never attached to a document/job.
                from app.application.presentation_assets import safe_path

                source = safe_path(self.root, key)
                await asyncio.to_thread(source.unlink, missing_ok=True)
                await asyncio.to_thread(source.parent.rmdir)
        return await self.get(identifier, user)

    def edit_materials(self, draft, edits):
        if edits is None:
            return
        known = {m["id"]: m for m in draft.get("materials", [])}
        ids = [str(m.id) for m in edits]
        if len(ids) != len(set(ids)) or not set(ids) <= set(known):
            raise ValueError("Материал не принадлежит текущему оформлению документа")
        values = []
        for edit in edits:
            value = known[str(edit.id)] | edit.model_dump(mode="json")
            if value["enabled"] and value["status"] == "FAILED":
                raise ValueError("Сначала замените файл, подготовка которого завершилась ошибкой")
            if value["harmonize"] and value["role"] != "DECORATION":
                raise ValueError("Согласование с палитрой разрешено только для декора")
            if value["crop"] and value["role"] == "DECORATION":
                raise ValueError("Обрезка доступна только для фона и фотографии")
            values.append(value)
        for role in ("COVER_PHOTO", "BACKGROUND"):
            ready = [m for m in values if m["role"] == role and m.get("enabled") and m["status"] == "READY"]
            if len(ready) > 1:
                raise ValueError("Для фотографии и фона включите один файл каждой роли")
        draft["materials"] = values

    def check_generation_materials(self, draft):
        if any(
            m.get("enabled") and m["status"] not in ("READY", "FAILED") for m in draft.get("materials", [])
        ):
            raise ValueError("Дождитесь подготовки своих материалов или выключите их")
        if draft["settings"].get("style") != "premium-dark" and any(
            m.get("enabled") for m in draft.get("materials", [])
        ):
            raise ValueError(
                "Свои материалы пока поддерживает Premium Dark. Выключите их или выберите этот стиль."
            )

    async def finish_material(self, revision, result):
        async with self.sessions.begin() as db:
            doc = await db.scalar(
                select(Document).where(Document.id == revision.document_id).with_for_update()
            )
            job = await db.scalar(select(Revision).where(Revision.id == revision.id).with_for_update())
            if not job or job.claim != revision.claim or job.status not in ACTIVE:
                return
            job.status, job.result, job.error, job.lease_until = result["status"], result, None, None
            job.updated_at = utcnow()
            if not doc:
                return
            draft = copy.deepcopy(doc.draft)
            entry = next(
                (m for m in draft.get("materials", []) if m["id"] == revision.input["material"]["id"]), None
            )
            if not entry:  # Removed while preparing: never resurrect it.
                return
            if result["status"] == "READY":
                entry.update(result["material"], status="READY", error=None)
                old = entry.pop("replaces_id", None)
                draft["materials"] = [m for m in draft["materials"] if m["id"] != old]
            else:
                entry.update(
                    status="FAILED", enabled=False, error=result.get("message", "Не удалось подготовить файл")
                )
            doc.draft, doc.version, doc.updated_at = draft, doc.version + 1, utcnow()

    async def reconcile_material_failures(self, doc, revisions):
        failed = {
            r.input["material"]["id"]: r.error or "Исчерпан лимит повторов подготовки. Замените файл."
            for r in revisions
            if r.input.get("kind") == MATERIAL_JOB and r.status in ("FAILED", "CANCELLED")
        }
        if not any(
            m["id"] in failed and m["status"] not in ("READY", "FAILED")
            for m in doc.draft.get("materials", [])
        ):
            return doc
        async with self.sessions.begin() as db:
            current = await db.scalar(
                select(Document)
                .where(Document.id == doc.id, Document.user_id == doc.user_id)
                .with_for_update()
            )
            if current:
                draft = copy.deepcopy(current.draft)
                for material in draft.get("materials", []):
                    if material["id"] in failed and material["status"] not in ("READY", "FAILED"):
                        material.update(status="FAILED", enabled=False, error=failed[material["id"]])
                current.draft, current.version, current.updated_at = draft, current.version + 1, utcnow()
                await db.flush()
                return current
        return doc

    async def material(self, identifier, asset_id, user):
        doc = await self.owned(identifier, user)
        if not doc:
            return None
        entry = next(
            (m for m in doc.draft.get("materials", []) if m["id"] == asset_id and m["status"] == "READY"),
            None,
        )
        if entry:
            return entry
        # Removed draft entries remain readable to their owner in saved revisions.
        async with self.sessions() as db:
            jobs = (await db.scalars(select(Revision).where(Revision.document_id == identifier))).all()
            for job in jobs:
                if (
                    job.input.get("kind") == MATERIAL_JOB
                    and job.input["material"]["id"] == asset_id
                    and job.status == "READY"
                ):
                    return job.input["material"] | job.result["material"]
        return None
