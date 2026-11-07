import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from app.database.models import CommercialOfferShare, PresentationDocument
from app.domain.offer_lifecycle import expired, live


async def test_14_day_transition_is_finite_idempotent_and_never_revives_dead_keys(repository):
    now = datetime(2026, 11, 7, 12, tzinfo=UTC)
    async with repository.sessions() as db, db.begin():
        from app.database.models import Project, User

        db.add(User(id="owner", display_name="Owner", normalized_name="migration-owner", password_hash="!"))
        await db.flush()
        db.add(Project(id="project", user_id="owner", name="Preserve"))
        await db.flush()
        cases = [
            ("live", True, now - timedelta(days=2), "READY_ACTIVATION"),
            ("expired", True, now - timedelta(days=8), "READY_ACTIVATION"),
            ("revoked", False, now - timedelta(days=2), "READY_ACTIVATION"),
            ("uncertain", True, now - timedelta(days=2), "EARLIEST_RENDER_CONSERVATIVE"),
            ("invalid", True, None, None),
        ]
        for name, active, activated, source in cases:
            db.add(
                PresentationDocument(
                    id=name, project_id="project", user_id="owner", draft={"preserved": name}
                )
            )
            await db.flush()
            db.add(
                CommercialOfferShare(
                    document_id=name,
                    token_hash=name,
                    encrypted_token="ciphertext-" + name,
                    active=active,
                    epoch=4,
                    activated_at=activated,
                    expires_at=activated + timedelta(days=7) if activated else None,
                    activation_source=source,
                )
            )
    spec = importlib.util.spec_from_file_location(
        "chat_migration", Path(__file__).parents[1] / "alembic/versions/0026_chat_workflow.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    async with repository.sessions() as db, db.begin():
        connection = await db.connection()
        await connection.run_sync(lambda c: module.extend_live_links(c, now))
    async with repository.sessions() as db:
        rows = {r.document_id: r for r in (await db.scalars(select(CommercialOfferShare))).all()}
        assert rows["live"].expires_at.replace(tzinfo=UTC) == now + timedelta(days=12)
        assert expired(rows["expired"], now) and not live(rows["revoked"], now)
        assert rows["uncertain"].expires_at.replace(tzinfo=UTC) == now + timedelta(days=5)
        assert not rows["invalid"].active and rows["invalid"].epoch == 5
        before = {k: (v.expires_at, v.epoch, v.encrypted_token) for k, v in rows.items()}
    async with repository.sessions() as db, db.begin():
        connection = await db.connection()
        await connection.run_sync(lambda c: module.extend_live_links(c, now + timedelta(hours=1)))
    async with repository.sessions() as db:
        after = {
            r.document_id: (r.expires_at, r.epoch, r.encrypted_token)
            for r in (await db.scalars(select(CommercialOfferShare))).all()
        }
        assert after == before
