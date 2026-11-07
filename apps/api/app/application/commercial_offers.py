"""Commercial documents reuse project selection, CAS, images and the existing queue."""

import asyncio
import copy
from decimal import ROUND_HALF_UP, Decimal
from uuid import uuid4

from sqlalchemy import select, update

from app.application.offer_tokens import TokenVault, public_origin
from app.application.presentation_assets import safe_path, valid_source
from app.application.presentation_logo import OFFER_LOGO, OFFER_LOGO_SHA256, freeze_logo
from app.database.models import CommercialOfferShare as Share
from app.database.models import PresentationDocument as Document
from app.database.models import PresentationRevision as Revision
from app.database.models import ProductObservation, Project, SupplierOffer
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
from app.domain.offer_lifecycle import LINK_LIFETIME_DAYS, aware, expired, live


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
        self.vault = TokenVault(self.root, create=False)
        self.origin = public_origin(documents.settings.public_frontend_origin)

    async def owned(self, identifier, user):
        row = await self.documents.owned(identifier, user)
        return row if row and row.draft.get("kind") == KIND else None

    async def ensure_vault(self):
        if self.vault.cipher is None:
            async with self.documents.local_lock:
                if (self.root / "keys/commercial-offers.key").is_file():
                    self.vault = TokenVault(self.root, create=False)
                    return
                async with self.sessions() as db:
                    existing = await db.scalar(select(Share.document_id).limit(1))
                if existing:
                    raise ValueError(
                        "Восстановите commercial-offers.key из согласованной резервной копии; существующие ссылки не перевыпущены"
                    )
                self.vault = TokenVault(self.root)

    async def primary(self, project_id, user):
        if not await self.documents.project(project_id, user):
            raise ValueError("PROJECT_NOT_FOUND")
        async with self.sessions() as db:
            row = await db.scalar(
                select(Document).where(
                    Document.project_id == project_id,
                    Document.user_id == user,
                    Document.is_primary_offer.is_(True),
                    Document.deleted_at.is_(None),
                )
            )
        return (
            {"id": row.id, "title": row.draft["settings"]["title"], "version": row.version} if row else None
        )

    @staticmethod
    def editable(row):
        if row and not row.is_primary_offer:
            raise ValueError("ARCHIVED_OFFER")

    async def state(self, project_id, user):
        project = await self.documents.project(project_id, user)
        if not project:
            return None
        return {"offer": await self.primary(project_id, user), "generation": project.offer_generation}

    async def delete(self, identifier, user, body):
        # Ownership is checked even for an idempotent tombstone replay.
        async with self.sessions() as db:
            row = await db.get(Document, identifier)
            if not row or row.user_id != user or row.draft.get("kind") != KIND:
                return None
            project_id = row.project_id
        if not body.confirmed:
            raise ValueError("Подтвердите удаление предложения")
        async with self.documents.local_lock, self.sessions.begin() as db:
            project = await db.scalar(select(Project).where(Project.id == project_id, Project.user_id == user).with_for_update())
            row = await db.scalar(select(Document).where(Document.id == identifier, Document.user_id == user).with_for_update())
            if not project or not row:
                return None
            if row.deleted_at is not None:
                return {"id": identifier, "deleted": True, "generation": project.offer_generation}
            if row.version != body.version:
                raise ValueError("STATE_CONFLICT")
            if row.is_primary_offer:
                project.offer_generation += 1
                row.primary_reason = "DELETED_PRIMARY"
            else:
                row.primary_reason = "DELETED_PREVIOUS"
            row.deleted_at = row.updated_at = utcnow()
            row.is_primary_offer, row.version = False, row.version + 1
            share = await db.get(Share, identifier, with_for_update=True)
            if share:
                share.active = False
                share.epoch += 1
                share.pending_revision_id = share.pending_epoch = None
                share.updated_at = utcnow()
            from app.domain.presentation import ACTIVE
            await db.execute(update(Revision).where(Revision.document_id == identifier, Revision.status.in_(ACTIVE)).values(status="CANCELLED", claim=None, lease_until=None, updated_at=utcnow()))
            return {"id": identifier, "deleted": True, "generation": project.offer_generation}

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
        async with self.documents.local_lock, self.documents.collections.locked(project_id, user) as (collection, db):
            if collection is None:
                return None
            current_project = await db.get(Project, project_id)
            if (body.offer_generation if body.offer_generation is not None else 0) != current_project.offer_generation:
                raise ValueError("STATE_CONFLICT")
            existing = await db.scalar(
                select(Document).where(Document.project_id == project_id, Document.is_primary_offer.is_(True), Document.deleted_at.is_(None))
            )
            if existing:
                return await self.get(existing.id, user) | {"created": False}
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
                is_primary_offer=True,
                primary_reason="FIRST_EXPLICIT_CREATE",
                draft={
                    "kind": KIND,
                    "schema_version": 1,
                    "collection_version": collection.version,
                    "collection_offer_ids": state["selected_ids"],
                    "excluded_offer_ids": [],
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
        return await self.get(identifier, user) | {"created": True}

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
                        Document.deleted_at.is_(None),
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
                "is_primary": r.is_primary_offer,
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
        if share:
            await self.ensure_vault()
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
        ids = [item["offer_id"] for item in draft["items"]]
        async with self.sessions() as db:
            stock_rows = (
                await db.execute(
                    select(SupplierOffer.id, SupplierOffer.supplier, ProductObservation)
                    .outerjoin(ProductObservation, ProductObservation.offer_id == SupplierOffer.id)
                    .where(SupplierOffer.id.in_(ids))
                )
            ).all()
        current = {id: (supplier, observation) for id, supplier, observation in stock_rows}
        owner_stock = {}
        for item in draft["items"]:
            supplier, observation = current.get(item["offer_id"], (None, None))
            owner_stock[item["id"]] = {
                "source": "LOCAL_INDEX" if observation else "SNAPSHOT_ONLY",
                "quantity": observation.stock if observation else item.get("stock"),
                "observed_at": observation.observed_at.isoformat()
                if observation
                else item.get("observed_at"),
                "incoming_quantity": observation.incoming if observation else item.get("incoming_quantity"),
                "label": "Остаток по данным VTO"
                if supplier == "vto" or item.get("supplier") == "vto"
                else "Последний известный остаток",
                "snapshot_quantity": item.get("stock"),
                "snapshot_observed_at": item.get("observed_at"),
            }
        return {
            "id": row.id,
            "is_primary": row.is_primary_offer,
            "primary_offer_id": (await self.primary(row.project_id, user) or {}).get("id"),
            "project_id": row.project_id,
            "version": row.version,
            "kind": KIND,
            "draft": draft,
            "preview": preview,
            "owner_stock": owner_stock,
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
                    "publication_epoch": r.input.get("publication_epoch"),
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
                "activated_at": None,
                "expires_at": None,
                "expired": False,
                "state": "UNPUBLISHED",
                "lifetime_days": LINK_LIFETIME_DAYS,
            }
        path = "/offers/" + self.vault.reveal(share.encrypted_token)
        now = self.documents.offer_now()
        active = live(share, now)
        is_expired = expired(share, now)
        return {
            "active": active,
            "lifetime_days": LINK_LIFETIME_DAYS,
            "epoch": share.epoch,
            "public_url": self.origin + path if self.origin and active else None,
            "local_preview_path": path if active else None,
            "published_revision_id": share.published_revision_id,
            "pending_revision_id": share.pending_revision_id,
            "activated_at": aware(share.activated_at).isoformat() if share.activated_at else None,
            "expires_at": aware(share.expires_at).isoformat() if share.expires_at else None,
            "expired": is_expired,
            "state": "EXPIRED"
            if is_expired
            else "ACTIVE"
            if active
            else "PENDING"
            if share.pending_revision_id
            else "REVOKED"
            if share.epoch
            else "UNPUBLISHED",
        }

    def draft_preview(self, row, draft):
        value = client_snapshot(draft)
        by_id = {i["id"]: i for i in draft["items"]}
        base = f"/api/offers/{row.id}"
        value.update(
            vto_logo_url="/api/branding/vto-logo.png",
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
        self.editable(row)
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
        draft["excluded_offer_ids"] = list(
            dict.fromkeys(
                [*draft.get("excluded_offer_ids", []), *[known[i]["offer_id"] for i in known if i not in ids]]
            )
        )
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
        self.editable(row)
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

    async def composition(self, identifier, user, body=None):
        row = await self.owned(identifier, user)
        if not row:
            return None
        self.editable(row)
        async with self.documents.collections.locked(row.project_id, user) as (collection, db):
            if collection is None:
                return None
            current = await db.scalar(select(Document).where(Document.id == identifier).with_for_update())
            if not current or current.deleted_at is not None:
                return None
            state = await self.documents.collections.state(db, collection)
            selected = set(state["selected_ids"])
            items = current.draft["items"]
            known = {i["offer_id"] for i in items}
            excluded = set(current.draft.get("excluded_offer_ids", []))
            added = [i for i in state["selected_ids"] if i not in known | excluded]
            removed = [i for i in items if i["offer_id"] not in selected]
            fresh = (
                await self.snapshot_items({"selected_ids": added, "items": state["items"]}) if added else []
            )
            comparison = {
                "version": current.version,
                "collection_version": collection.version,
                "added": [{"offer_id": i["offer_id"], "title": i["title"]} for i in fresh],
                "removed": [{"offer_id": i["offer_id"], "title": i["title"]} for i in removed],
                "retained": sum(i["offer_id"] in selected for i in items),
                "excluded_retained": len(excluded & selected),
            }
            if body is None:
                return comparison
            if current.version != body.version or collection.version != body.collection_version:
                raise ValueError("STATE_CONFLICT")
            if not body.confirmed:
                raise ValueError("Подтвердите изменение состава")
            values = [i for i in items if i["offer_id"] in selected] + fresh
            if not values:
                raise ValueError("В предложении должна остаться хотя бы одна позиция")
            if len(values) > 200:
                raise ValueError("Один документ поддерживает не более 200 позиций")
            if added or removed:
                current.draft = copy.deepcopy(current.draft) | {
                    "items": values,
                    "collection_version": collection.version,
                    "collection_offer_ids": state["selected_ids"],
                    "last_composition_changes": comparison,
                }
                current.version += 1
                current.updated_at = utcnow()
        return await self.get(identifier, user)

    async def publish(self, identifier, user, body, *, publish=True):
        row = await self.owned(identifier, user)
        if not row:
            return None
        self.editable(row)
        await self.ensure_vault()
        async with self.sessions() as db:
            from app.database.models import CommercialOfferRequest

            receipt = await db.get(CommercialOfferRequest, (identifier, str(body.request_id)))
            old = (
                await db.get(Revision, receipt.revision_id)
                if receipt
                else await db.scalar(
                    select(Revision).where(
                        Revision.document_id == identifier,
                        Revision.input["kind"].as_string() == KIND,
                        Revision.input["generation_request_id"].as_string() == str(body.request_id),
                    )
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
        mark = await asyncio.to_thread(
            freeze_logo, self.root, {}, default_logo=OFFER_LOGO, default_sha256=OFFER_LOGO_SHA256
        )
        payload = copy.deepcopy(row.draft) | {
            "kind": KIND,
            "document_id": identifier,
            "resolved_logo": logo,
            "resolved_vto_mark": mark,
            "offer_layout_version": 2,
        }
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
            if not row or row.deleted_at is not None:
                return None
            share = await db.get(Share, identifier, with_for_update=True)
            if row.version != body.version or not share or share.epoch != body.epoch:
                raise ValueError("STATE_CONFLICT")
            share.epoch += 1
            share.pending_revision_id = None
            share.pending_epoch = None
            if action == "revoke":
                share.active = False
            else:
                if not share.published_revision_id:
                    raise ValueError("Сначала дождитесь готовой опубликованной версии")
                _, share.token_hash, share.encrypted_token = self.vault.create()
                share.activated_at = share.expires_at = None
                from app.domain.offer_lifecycle import activate

                activate(share, self.documents.offer_now())
                share.active = True
            share.updated_at = utcnow()
        return await self.get(identifier, user)

    async def public(self, token, revision=None):
        digest = self.vault.digest(token)
        if not digest:
            return None
        async with self.sessions() as db:
            share = await db.scalar(select(Share).where(Share.token_hash == digest))
            document = await db.get(Document, share.document_id) if share else None
            if not document or document.deleted_at is not None:
                return None
            if share and expired(share, self.documents.offer_now()):
                raise ValueError("OFFER_LINK_EXPIRED")
            if not live(share, self.documents.offer_now()) or not share.published_revision_id:
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
            vto_logo_url=base + "/assets/vto_mark"
            if "vto_mark" in row.result.get("asset_keys", {})
            else "/api/branding/vto-logo.png",
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
