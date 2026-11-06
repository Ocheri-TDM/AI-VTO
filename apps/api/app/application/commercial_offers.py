"""Commercial documents reuse project selection, CAS, images and the existing queue."""

import asyncio
import copy
from decimal import ROUND_HALF_UP, Decimal
from uuid import uuid4

from sqlalchemy import select

from app.application.offer_tokens import TokenVault, public_origin
from app.application.presentation_assets import safe_path, valid_source
from app.application.presentation_logo import OFFER_LOGO, OFFER_LOGO_SHA256, freeze_logo
from app.database.models import CommercialOfferShare as Share
from app.database.models import PresentationDocument as Document
from app.database.models import PresentationRevision as Revision
from app.database.models import ProductObservation, SupplierOffer
from app.domain.commercial_offer import (
    KIND,
    availability,
    characteristics,
    client_snapshot,
    draft_amount,
    line_amount,
    plain_text,
    price,
    public_description,
    validate_document,
)
from app.domain.models import utcnow


def logo_background(path):
    from PIL import Image, ImageStat

    with Image.open(path) as image:
        image = image.convert("RGBA")
        image.thumbnail((128, 128))
        if image.getchannel("A").getextrema() == (255, 255):
            return "#ffffff"
        means = ImageStat.Stat(image.convert("RGB"), mask=image.getchannel("A")).mean
    return "#151918" if sum(means) / 3 > 160 else "#ffffff"


class CommercialOfferService:
    def __init__(self, documents):
        self.documents = documents
        self.sessions, self.root = documents.sessions, documents.root
        self.vault = TokenVault(self.root)
        self.origin = public_origin(documents.settings.public_frontend_origin)

    async def owned(self, identifier, user):
        row = await self.documents.owned(identifier, user)
        return row if row and row.draft.get("kind") == KIND else None

    async def snapshot_items(self, state):
        base = await self.documents.snapshots(state["selected_ids"])
        contexts = {
            item["offer_id"]: item.get("selection_context")
            or {"quantity": 1, "rule": "HISTORICAL_CONTEXT_UNAVAILABLE"}
            for item in state["items"]
        }
        async with self.sessions() as db:
            rows = (
                await db.execute(
                    select(SupplierOffer, ProductObservation)
                    .outerjoin(ProductObservation, ProductObservation.offer_id == SupplierOffer.id)
                    .where(SupplierOffer.id.in_(state["selected_ids"]))
                )
            ).all()
        by_id = {offer.id: (offer, observation) for offer, observation in rows}
        output = []
        for item in base:
            offer, observation = by_id[item["offer_id"]]
            payload = offer.payload
            evidence = observation.payload if observation else {}
            original = evidence.get("original_price") if evidence.get("original_currency") == "KZT" else None
            if original is None:
                original = observation.price_kzt if observation else None
            catalog_price = (
                format(Decimal(str(original)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), ".2f")
                if original is not None
                else None
            )
            try:
                quoted = price(catalog_price) if catalog_price and Decimal(catalog_price) > 0 else None
            except ValueError:
                quoted = None
            context = contexts[item["offer_id"]]
            photos = []
            for url in item["images"]:
                try:
                    valid_source(url, resolve=False)
                except ValueError:
                    continue
                photos.append({"id": str(uuid4()), "source_url": url, "original_key": None})
            output.append(
                item
                | {
                    "id": str(uuid4()),
                    "title": plain_text(item["source_name"]),
                    "sku": str(item.get("sku") or ""),
                    "catalog_sku": str(item.get("sku") or ""),
                    "sku_source": "CATALOG",
                    "sku_note": "",
                    "source_description": payload.get("description", ""),
                    "description": public_description(payload.get("description", "")),
                    "characteristics": characteristics(payload),
                    "catalog_price": catalog_price,
                    "unit_price": quoted,
                    "price_source": "CATALOG",
                    "quantity": int(context.get("quantity") or 1),
                    "quantity_context": context,
                    "quantity_source": "SELECTION_CONTEXT",
                    "availability": availability(observation.stock if observation else None, evidence),
                    "availability_evidence": evidence.get("availability_records", []),
                    "photos": photos,
                    "main_image": photos[0]["id"] if photos else None,
                }
            )
        return output

    async def create(self, project_id, user, body):
        project = await self.documents.project(project_id, user)
        if not project:
            return None
        async with self.documents.collections.locked(project_id, user) as (collection, db):
            if collection is None:
                return None
            if body.collection_version != collection.version:
                raise ValueError("STATE_CONFLICT")
            state = await self.documents.collections.state(db, collection)
            if not 1 <= len(state["selected_ids"]) <= 200:
                raise ValueError("Выберите от 1 до 200 вариантов")
            items = await self.snapshot_items(state)
            title = (
                "Предложение для " + project.client_name
                if project.client_name
                else "Предложение · " + project.name
            )
            row = Document(
                project_id=project_id,
                user_id=user,
                draft={
                    "kind": KIND,
                    "schema_version": 1,
                    "collection_version": collection.version,
                    "items": items,
                    "materials": [],
                    "settings": {
                        "title": title[:200],
                        "client": (project.client_name or "")[:200],
                        "use_logo": True,
                        "logo": None,
                        "use_banner": True,
                    },
                },
            )
            db.add(row)
            await db.flush()
            identifier = row.id
        return await self.get(identifier, user)

    async def listing(self, project_id, user):
        if not await self.documents.project(project_id, user):
            return None
        async with self.sessions() as db:
            rows = (
                await db.scalars(
                    select(Document)
                    .where(
                        Document.project_id == project_id,
                        Document.user_id == user,
                        Document.draft["kind"].as_string() == KIND,
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
            all_revisions = (
                await db.scalars(
                    select(Revision)
                    .where(Revision.document_id == identifier)
                    .order_by(Revision.created_at.desc())
                )
            ).all()
            revisions = [r for r in all_revisions if r.input.get("kind") == KIND]
            share = await db.get(Share, identifier)
        row = await self.documents.reconcile_material_failures(row, all_revisions)
        draft = copy.deepcopy(row.draft)
        for item in draft["items"]:
            item["line_amount"] = draft_amount(item["unit_price"], item["quantity"])
            item["warnings"] = []
            if item.get("stock") is not None and item["quantity"] > item["stock"]:
                item["warnings"].append(
                    "Тираж выше известного остатка. Наличие нужно подтвердить; резерв не создаётся."
                )
            if item["quantity_context"].get("rule") in (
                "HISTORICAL_CONTEXT_UNAVAILABLE",
                "AMBIGUOUS_CONTEXT",
                "INCOMPATIBLE_CONTEXT",
                "NO_SOURCE",
            ):
                item["warnings"].append(
                    "Исходный тираж достоверно не зафиксирован: начальное значение 1. Проверьте количество."
                )
            if not item["photos"]:
                item["warnings"].append("Нет фотографии этого варианта. Товар сохранён в предложении.")
        preview = self.draft_preview(row, draft)
        selected_logo = draft["settings"].get("logo") if draft["settings"].get("use_logo") else None
        try:
            preview["logo_background"] = await asyncio.to_thread(
                logo_background, safe_path(self.root, selected_logo) if selected_logo else OFFER_LOGO
            )
        except (OSError, ValueError):
            preview["logo_background"] = "#151918"
        return {
            "id": row.id,
            "project_id": row.project_id,
            "version": row.version,
            "kind": KIND,
            "draft": draft,
            "preview": preview,
            "issues": validate_document(draft),
            "share": self.share_view(share),
            "materials_preparing": any(
                m["status"] not in ("READY", "FAILED") for m in draft.get("materials", [])
            ),
            "revisions": [
                {
                    "id": r.id,
                    "source_version": r.source_version,
                    "status": r.status,
                    "error": r.error,
                    "request_id": r.input.get("generation_request_id"),
                    "publication_requested": r.input.get("publication_requested", False),
                    "snapshot_hash": r.result.get("snapshot_hash"),
                    "warnings": r.result.get("warnings", []),
                    "timings": r.result.get("timings", {}),
                    "peak_rss_bytes": r.result.get("peak_rss_bytes"),
                    "pdf_url": f"/api/offers/{identifier}/revisions/{r.id}/pdf"
                    if r.status == "READY"
                    else None,
                    "xlsx_url": f"/api/offers/{identifier}/revisions/{r.id}/xlsx"
                    if r.status == "READY"
                    else None,
                }
                for r in revisions
            ],
        }

    def share_view(self, share):
        if not share:
            return {
                "active": False,
                "epoch": 0,
                "public_url": None,
                "local_preview_path": None,
                "published_revision_id": None,
                "pending_revision_id": None,
            }
        path = "/offers/" + self.vault.reveal(share.encrypted_token)
        return {
            "active": share.active,
            "epoch": share.epoch,
            "public_url": self.origin + path if self.origin and share.active else None,
            "local_preview_path": path if share.active else None,
            "published_revision_id": share.published_revision_id,
            "pending_revision_id": share.pending_revision_id,
        }

    def draft_preview(self, row, draft):
        value = client_snapshot(draft)
        by_id = {i["id"]: i for i in draft["items"]}
        base = f"/api/offers/{row.id}"
        value.update(
            logo_url=base + f"/logo?v={row.version}",
            banner_url=base + f"/banner?v={row.version}"
            if draft["settings"].get("use_banner")
            and any(
                m["status"] == "READY" and m["role"] == "COVER_PHOTO" and m.get("enabled")
                for m in draft.get("materials", [])
            )
            else None,
        )
        for item in value["items"]:
            item.pop("image_ids")
            item["images"] = [
                {
                    "id": p["id"],
                    "url": base + "/draft-images/" + p["id"] if p.get("cached_key") else p["source_url"],
                }
                for p in by_id[item["id"]]["photos"]
            ]
        return value

    async def save(self, identifier, user, body):
        row = await self.owned(identifier, user)
        if not row:
            return None
        draft = copy.deepcopy(row.draft)
        known = {i["id"]: i for i in draft["items"]}
        ids = [item.id for item in body.items]
        if len(ids) != len(set(ids)) or not set(ids) <= set(known):
            raise ValueError("Состав можно менять только в пределах снимка предложения")
        values = []
        for edit in body.items:
            old = known[edit.id]
            if edit.main_image is not None and edit.main_image not in {p["id"] for p in old["photos"]}:
                raise ValueError("Фото не принадлежит этому варианту")
            item = old | edit.model_dump()
            item["title"], item["sku"] = plain_text(item["title"]), plain_text(item["sku"])
            if not item["title"].strip():
                raise ValueError("Укажите название позиции")
            if item["sku"] != old["sku"]:
                if item["sku"] and len(item["sku_note"].strip()) < 3:
                    raise ValueError("Укажите источник вручную заданного артикула")
                item["sku_source"] = "MANUAL"
            if item["unit_price"] != old["unit_price"]:
                item["price_source"] = "MANUAL"
            if item["quantity"] != old["quantity"]:
                item["quantity_source"] = "MANUAL"
            line_amount(item["unit_price"], item["quantity"])
            values.append(item)
        draft["items"] = values
        if not plain_text(body.title).strip():
            raise ValueError("Укажите название предложения")
        draft["settings"].update(
            title=plain_text(body.title),
            client=plain_text(body.company),
            use_logo=body.use_logo,
            use_banner=body.use_banner,
        )
        await self.documents.replace_draft(row, body.version, draft)
        return await self.get(identifier, user)

    async def refresh(self, identifier, user, version):
        row = await self.owned(identifier, user)
        if not row:
            return None
        old = row.draft["items"]
        state = {
            "selected_ids": [i["offer_id"] for i in old],
            "items": [{"offer_id": i["offer_id"], "selection_context": i["quantity_context"]} for i in old],
        }
        fresh = await self.snapshot_items(state)
        changes, values = [], []
        for previous, current in zip(old, fresh, strict=True):
            for field in ("catalog_price", "sku", "description", "characteristics", "availability"):
                if previous[field] != current[field]:
                    changes.append(
                        {
                            "item_id": previous["id"],
                            "field": field,
                            "before": previous[field],
                            "after": current[field],
                        }
                    )
            current.update(
                id=previous["id"],
                title=previous["title"],
                quantity=previous["quantity"],
                quantity_source=previous["quantity_source"],
            )
            old_main = next((p for p in previous["photos"] if p["id"] == previous["main_image"]), None)
            if old_main:
                same = next((p for p in current["photos"] if p["source_url"] == old_main["source_url"]), None)
                if same:
                    current["main_image"] = same["id"]
            for field in ("unit_price", "price_source", "sku", "sku_note", "sku_source"):
                if (field.startswith("sku") and previous.get("sku_source") == "MANUAL") or (
                    field in ("unit_price", "price_source") and previous["price_source"] == "MANUAL"
                ):
                    current[field] = previous[field]
            values.append(current)
        draft = copy.deepcopy(row.draft) | {"items": values, "last_catalog_changes": changes}
        await self.documents.replace_draft(row, version, draft)
        return await self.get(identifier, user)

    async def publish(self, identifier, user, body, *, publish=True):
        row = await self.owned(identifier, user)
        if not row:
            return None
        async with self.sessions() as db:
            old = await db.scalar(
                select(Revision).where(
                    Revision.document_id == identifier,
                    Revision.input["kind"].as_string() == KIND,
                    Revision.input["generation_request_id"].as_string() == str(body.request_id),
                )
            )
        if old:
            await self.documents.enqueue_offer(
                identifier,
                user,
                body.version,
                str(body.request_id),
                old.input,
                self.vault,
                publish=publish,
                expected_epoch=body.share_epoch,
            )
            return await self.get(identifier, user)
        issues = validate_document(row.draft)
        if issues:
            raise ValueError("; ".join(i["message"] for i in issues))
        logo = await asyncio.to_thread(
            freeze_logo,
            self.root,
            row.draft["settings"],
            default_logo=OFFER_LOGO,
            default_sha256=OFFER_LOGO_SHA256,
        )
        logo["background"] = await asyncio.to_thread(logo_background, safe_path(self.root, logo["key"]))
        payload = copy.deepcopy(row.draft) | {"kind": KIND, "document_id": identifier, "resolved_logo": logo}
        if row.draft["settings"].get("use_banner"):
            materials = [
                m for m in row.draft.get("materials", []) if m.get("enabled") and m["role"] == "COVER_PHOTO"
            ]
            ready = [m for m in materials if m["status"] == "READY"]
            if materials and not ready:
                raise ValueError("Дождитесь подготовки баннера или выключите повреждённый файл")
            if ready:
                payload["resolved_banner"] = {"key": ready[-1]["key"], "hash": ready[-1]["prepared_hash"]}
        await self.documents.enqueue_offer(
            identifier,
            user,
            body.version,
            str(body.request_id),
            payload,
            self.vault,
            publish=publish,
            expected_epoch=body.share_epoch,
        )
        return await self.get(identifier, user)

    async def share_action(self, identifier, user, body, action):
        if not await self.owned(identifier, user):
            return None
        async with self.documents.local_lock, self.sessions.begin() as db:
            row = await db.scalar(select(Document).where(Document.id == identifier).with_for_update())
            share = await db.get(Share, identifier, with_for_update=True)
            if row.version != body.version or not share or share.epoch != body.epoch:
                raise ValueError("STATE_CONFLICT")
            share.epoch += 1
            share.pending_revision_id = None
            if action == "revoke":
                share.active = False
            else:
                if not share.published_revision_id:
                    raise ValueError("Сначала дождитесь готовой опубликованной версии")
                _, share.token_hash, share.encrypted_token = self.vault.create()
                share.active = True
            share.updated_at = utcnow()
        return await self.get(identifier, user)

    async def public(self, token, revision=None):
        digest = self.vault.digest(token)
        if not digest:
            return None
        async with self.sessions() as db:
            share = await db.scalar(select(Share).where(Share.token_hash == digest, Share.active.is_(True)))
            if not share or not share.published_revision_id:
                return None
            if revision is not None and share.published_revision_id != revision:
                raise ValueError("REVISION_CHANGED")
            row = await db.get(Revision, share.published_revision_id)
            if not row or row.status != "READY" or row.input.get("kind") != KIND:
                return None
        base = f"/api/public/offers/{token}/revisions/{row.id}"
        return self.client_view(row, base), row

    def client_view(self, row, base):
        value = copy.deepcopy(row.result["client_snapshot"])
        value.update(
            revision_id=row.id,
            snapshot_hash=row.result["snapshot_hash"],
            logo_url=base + "/assets/logo",
            banner_url=base + "/assets/banner" if row.result.get("banner") else None,
            pdf_url=base + "/pdf",
            xlsx_url=base + "/xlsx",
        )
        for item in value["items"]:
            item["images"] = [
                {"id": id, "url": base + "/assets/" + id}
                for id in item.pop("image_ids")
                if id in row.result["asset_keys"]
            ]
        return value

    async def revision(self, identifier, revision, user):
        if not await self.owned(identifier, user):
            return None
        row = await self.documents.revision(identifier, revision, user)
        return row if row and row.input.get("kind") == KIND and row.status == "READY" else None
