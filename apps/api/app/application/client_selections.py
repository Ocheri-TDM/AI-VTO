"""Independent browser interest with short transactions and a durable owner outbox."""

import hashlib
import hmac
import json
import secrets
from contextlib import asynccontextmanager

from sqlalchemy import func, select, text

from app.database.models import (
    CommercialOfferShare as Share,
)
from app.database.models import (
    ManagerEvent,
    ManagerEventCursor,
    ManagerNotification,
)
from app.database.models import (
    OfferClientMutation as Mutation,
)
from app.database.models import (
    OfferClientSelection as Selection,
)
from app.database.models import (
    OfferVisitor as Visitor,
)
from app.database.models import (
    OfferVisitorBudget as Budget,
)
from app.database.models import (
    PresentationDocument as Document,
)
from app.database.models import (
    PresentationRevision as Revision,
)
from app.domain.client_selection import SelectionProblem
from app.domain.commercial_offer import KIND
from app.domain.offer_lifecycle import aware, expired, live


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def cookie_name(generation):
    return "offer_visitor_" + generation[:20]


def csrf(secret, generation):
    return hmac.new(secret.encode(), ("offer-choice:" + generation).encode(), hashlib.sha256).hexdigest()


async def next_owner_event(db, user, document, event_type, payload=None, visitor=None):
    # PostgreSQL row lock serializes allocation AND commit for this owner. A later
    # cursor cannot pass a lower, still-uncommitted event from another transaction.
    await db.execute(
        text(
            "INSERT INTO manager_event_cursors (user_id,sequence) VALUES (:user,0) ON CONFLICT (user_id) DO NOTHING"
        ),
        {"user": user},
    )
    cursor = await db.scalar(
        select(ManagerEventCursor).where(ManagerEventCursor.user_id == user).with_for_update()
    )
    cursor.sequence += 1
    db.add(
        ManagerEvent(
            user_id=user,
            sequence=cursor.sequence,
            document_id=document,
            visitor_id=visitor,
            event_type=event_type,
            payload=payload or {},
        )
    )
    return cursor.sequence


class ClientSelectionService:
    def __init__(self, offers, settings):
        self.offers, self.settings, self.sessions = offers, settings, offers.sessions
        self.now = offers.documents.offer_now

    @asynccontextmanager
    async def atomic(self):
        if self.sessions.kw["bind"].dialect.name == "sqlite":
            async with self.offers.documents.local_lock, self.sessions.begin() as db:
                yield db
        else:
            async with self.sessions.begin() as db:
                yield db

    def check_expiry(self, share):
        if expired(share, self.now()):
            raise SelectionProblem("OFFER_LINK_EXPIRED", "Срок действия предложения истёк", 410)

    async def context(self, db, token, revision=None, write=False):
        generation = self.offers.vault.digest(token)
        share = await db.scalar(select(Share).where(Share.token_hash == generation)) if generation else None
        if not share:
            raise SelectionProblem("OFFER_NOT_FOUND", "Предложение недоступно", 404)
        # Match the existing publication/deletion lock order: document, then share.
        document = await db.get(Document, share.document_id, with_for_update=write)
        if write:
            share = await db.get(Share, share.document_id, with_for_update=True, populate_existing=True)
        if (
            not document
            or document.deleted_at
            or not share
            or share.token_hash != generation
            or document.draft.get("kind") != KIND
        ):
            raise SelectionProblem("OFFER_NOT_FOUND", "Предложение недоступно", 404)
        self.check_expiry(share)
        if not live(share, self.now()) or not share.published_revision_id:
            raise SelectionProblem("OFFER_NOT_FOUND", "Предложение недоступно", 404)
        if revision and str(revision) != share.published_revision_id:
            raise SelectionProblem(
                "REVISION_CHANGED", "Предложение обновилось. Обновите страницу перед выбором."
            )
        published = await db.get(Revision, share.published_revision_id)
        if not published or published.status != "READY" or published.input.get("kind") != KIND:
            raise SelectionProblem("OFFER_NOT_FOUND", "Предложение недоступно", 404)
        return document, share, published

    async def visitor(self, db, share, cookies, write=False):
        secret = cookies.get(cookie_name(share.token_hash), "")
        if not isinstance(secret, str) or not 40 <= len(secret) <= 100:
            return None, None
        visitor = await db.scalar(
            select(Visitor)
            .where(
                Visitor.document_id == share.document_id,
                Visitor.generation == share.token_hash,
                Visitor.secret_hash == digest(secret),
            )
            .with_for_update()
            if write
            else select(Visitor).where(
                Visitor.document_id == share.document_id,
                Visitor.generation == share.token_hash,
                Visitor.secret_hash == digest(secret),
            )
        )
        if visitor and aware(visitor.expires_at) > aware(self.now()):
            return visitor, secret
        return None, None

    async def state(self, db, share, published, visitor, secret):
        current = {i["id"] for i in published.result["client_snapshot"]["items"]}
        rows = (
            list(
                await db.scalars(
                    select(Selection).where(Selection.visitor_id == visitor.id, Selection.selected.is_(True))
                )
            )
            if visitor
            else []
        )
        return {
            "scope": digest("browser-scope:" + share.token_hash)[:24],
            "initialized": visitor is not None,
            "version": visitor.version if visitor else 0,
            "published_revision_id": published.id,
            "csrf_token": csrf(secret, share.token_hash) if secret else None,
            "selected_items": [
                {"item_id": r.item_id, "published_revision_id": r.published_revision_id}
                for r in rows
                if r.item_id in current
            ],
            "previous_item_count": sum(r.item_id not in current for r in rows),
        }

    async def get(self, token, cookies):
        async with self.sessions() as db:
            _, share, published = await self.context(db, token)
            visitor, secret = await self.visitor(db, share, cookies)
            return await self.state(db, share, published, visitor, secret)

    async def budget(self, db, share):
        budget = await db.get(Budget, (share.document_id, share.token_hash))
        now = aware(self.now())
        if budget is None:
            budget = Budget(
                document_id=share.document_id,
                generation=share.token_hash,
                total_sessions=0,
                total_mutations=0,
                window_sessions=0,
                window_mutations=0,
                window_at=now,
                created_at=now,
            )
            db.add(budget)
        if (now - aware(budget.window_at)).total_seconds() >= 60:
            budget.window_at = now
            budget.window_sessions = budget.window_mutations = 0
        return budget

    async def start(self, token, cookies, revision):
        async with self.atomic() as db:
            _, share, published = await self.context(db, token, revision, True)
            visitor, secret = await self.visitor(db, share, cookies, True)
            if visitor:
                return await self.state(db, share, published, visitor, secret), None, None
            budget = await self.budget(db, share)
            if budget.total_sessions >= self.settings.offer_visitor_max_sessions:
                raise SelectionProblem(
                    "VISITOR_LIMIT",
                    "Достигнут лимит посетителей предложения. Обратитесь к менеджеру.",
                    429,
                    60,
                )
            if budget.window_sessions >= self.settings.offer_visitor_sessions_per_minute:
                raise SelectionProblem("VISITOR_RATE_LIMIT", "Повторите попытку немного позже.", 429, 60)
            budget.total_sessions += 1
            budget.window_sessions += 1
            secret = secrets.token_urlsafe(32)
            visitor = Visitor(
                document_id=share.document_id,
                generation=share.token_hash,
                secret_hash=digest(secret),
                ordinal=budget.total_sessions,
                version=0,
                last_sequence=0,
                total_requests=0,
                window_requests=0,
                window_at=self.now(),
                expires_at=share.expires_at,
                created_at=self.now(),
                updated_at=self.now(),
            )
            db.add(visitor)
            await db.flush()
            value = await self.state(db, share, published, visitor, secret)
            cookie = {
                "key": cookie_name(share.token_hash),
                "value": secret,
                "path": f"/api/public/offers/{token}/selection",
                "max_age": max(1, int((aware(share.expires_at) - aware(self.now())).total_seconds())),
            }
            self.check_expiry(share)
        return value, cookie, share

    async def mutate(self, token, cookies, csrf_token, body):
        async with self.atomic() as db:
            document, share, published = await self.context(db, token, body.published_revision_id, True)
            visitor, secret = await self.visitor(db, share, cookies, True)
            if not visitor:
                raise SelectionProblem(
                    "VISITOR_SESSION_REQUIRED", "Восстановите сессию посетителя и повторите действие.", 401
                )
            if not csrf_token or not hmac.compare_digest(csrf(secret, share.token_hash), csrf_token):
                raise SelectionProblem("CSRF_REJECTED", "Повторите действие после обновления страницы.", 403)
            item_id = str(body.item_id)
            if item_id not in {i["id"] for i in published.result["client_snapshot"]["items"]}:
                raise SelectionProblem(
                    "ITEM_NOT_PUBLISHED", "Позиция не принадлежит опубликованному предложению.", 404
                )
            now = aware(self.now())
            budget = await self.budget(db, share)
            if (now - aware(visitor.window_at)).total_seconds() >= 60:
                visitor.window_at = now
                visitor.window_requests = 0
            if (
                budget.window_mutations >= self.settings.offer_selection_per_minute
                or visitor.window_requests >= self.settings.offer_visitor_requests_per_minute
            ):
                raise SelectionProblem(
                    "SELECTION_RATE_LIMIT", "Слишком много действий. Повторите немного позже.", 429, 60
                )
            budget.window_mutations += 1
            visitor.window_requests += 1
            fingerprint = digest(json.dumps([item_id, body.selected, str(body.published_revision_id)]))
            receipt = await db.get(Mutation, (visitor.id, str(body.mutation_id)))
            if receipt:
                if receipt.fingerprint != fingerprint:
                    raise SelectionProblem(
                        "MUTATION_REUSED", "Идентификатор операции уже использован для другого действия."
                    )
                result = await self.state(db, share, published, visitor, secret)
                result.update(replayed=True, changed=False, mutation_id=str(body.mutation_id))
            else:
                if body.version != visitor.version:
                    raise SelectionProblem(
                        "SELECTION_CONFLICT",
                        "Выбор изменился в другой вкладке. Согласуйте состояние и повторите действие.",
                    )
                if (
                    visitor.total_requests >= self.settings.offer_visitor_max_mutations
                    or budget.total_mutations >= self.settings.offer_selection_max_mutations
                ):
                    raise SelectionProblem(
                        "SELECTION_LIMIT",
                        "Достигнут лимит изменений выбора. Обратитесь к менеджеру.",
                        429,
                        60,
                    )
                selection = await db.get(Selection, (visitor.id, item_id))
                changed = body.selected != bool(selection and selection.selected)
                sequence = None
                if changed:
                    if selection is None:
                        selection = Selection(
                            visitor_id=visitor.id,
                            item_id=item_id,
                            selected=body.selected,
                            published_revision_id=published.id,
                            created_at=now,
                            updated_at=now,
                        )
                        db.add(selection)
                    else:
                        selection.selected = body.selected
                        selection.updated_at = now
                        if body.selected:
                            selection.published_revision_id = published.id
                    visitor.version += 1
                    visitor.updated_at = now
                    await db.flush()
                    count = await db.scalar(
                        select(func.count())
                        .select_from(Selection)
                        .where(
                            Selection.visitor_id == visitor.id,
                            Selection.selected.is_(True),
                            Selection.item_id.in_(
                                [i["id"] for i in published.result["client_snapshot"]["items"]]
                            ),
                        )
                    )
                    sequence = await next_owner_event(
                        db,
                        document.user_id,
                        document.id,
                        "selection.changed",
                        {
                            "item_id": item_id,
                            "selected": body.selected,
                            "published_revision_id": published.id,
                            "visitor_version": visitor.version,
                            "selected_count": count,
                        },
                        visitor.id,
                    )
                    visitor.last_sequence = sequence
                    notification = await db.scalar(
                        select(ManagerNotification).where(ManagerNotification.visitor_id == visitor.id)
                    )
                    if notification is None:
                        notification = ManagerNotification(
                            user_id=document.user_id,
                            document_id=document.id,
                            visitor_id=visitor.id,
                            read_sequence=0,
                        )
                        db.add(notification)
                    notification.title = published.result["client_snapshot"]["title"][:200]
                    notification.selected_count = count
                    notification.last_sequence = sequence
                    notification.updated_at = now
                visitor.total_requests += 1
                budget.total_mutations += 1
                db.add(
                    Mutation(
                        visitor_id=visitor.id,
                        mutation_id=str(body.mutation_id),
                        fingerprint=fingerprint,
                        result_version=visitor.version,
                        event_sequence=sequence,
                        created_at=now,
                    )
                )
                await db.flush()
                result = await self.state(db, share, published, visitor, secret)
                result.update(replayed=False, changed=changed, mutation_id=str(body.mutation_id))
            # The link can expire while this short transaction waits for an
            # owner cursor lock. Reject and roll back all staged effects then.
            self.check_expiry(share)
        # Captured immediately after the successful transaction commit, not at
        # insertion/HTTP-200 time; useful for the bounded delivery measurements.
        result["commit_observed_at"] = self.now().isoformat()
        return result
