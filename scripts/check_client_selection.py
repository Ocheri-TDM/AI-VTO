"""Bounded, exact-ID live acceptance; private ledger never goes to evidence."""

import asyncio
import hashlib
import json
import shutil
import sys
from pathlib import Path

import check_clean_start as baseline
import httpx
import psutil
from sqlalchemy import text

ROOT = baseline.ROOT
LEDGER = ROOT / ".local/client-selection-ledger.json"
PUBLIC = ROOT / "docs/screenshots/client-selection"
EXTERNAL = "https://souvenir-studio-three.vercel.app"
CHANGED = {
    "apps/api/alembic/versions/0028_client_selections.py",
    "apps/api/app/main.py",
    "apps/api/app/config.py",
    "apps/api/app/database/models.py",
    "apps/api/app/application/commercial_offers.py",
    "apps/api/app/application/presentations.py",
}


def source_proof(state):
    changed, protected = [], 0
    for key, fingerprint in state["backend"].items():
        actual = hashlib.sha256((ROOT / key).read_bytes()).hexdigest()
        if actual != fingerprint:
            assert key.replace("\\", "/") in CHANGED, key
            changed.append(key)
        else:
            protected += 1
    return {"protected_source_files": protected, "explicit_feature_changes": changed}


async def verify(state, cleanup=False):
    from quality.nav_recovery_cleanup import bound, protected

    settings = baseline.Settings()
    engine, sessions = baseline.create_database(settings.database_url)
    owners = [u["id"] for u in state["users"]]
    try:
        async with sessions() as db:
            await db.execute(
                text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            )
            projects = list(
                await db.scalars(
                    bound("SELECT id FROM projects WHERE user_id IN :ids"),
                    {"ids": owners},
                )
            )
            chats = list(
                await db.scalars(
                    bound("SELECT id FROM chats WHERE user_id IN :ids"), {"ids": owners}
                )
            )
            docs = list(
                await db.scalars(
                    bound(
                        "SELECT id FROM presentation_documents WHERE user_id IN :ids"
                    ),
                    {"ids": owners},
                )
            )
            assert set(projects) == set(state["projects"]) and set(docs) == set(
                state["documents"]
            )
            proof, offers = await protected(
                db, state, projects or ["none"], chats or ["none"], docs or ["none"]
            )
            assert not await db.scalar(
                bound(
                    "SELECT count(*) FROM presentation_revisions WHERE document_id IN :ids AND status IN ('QUEUED','PREPARING_ASSETS','RENDERING','VALIDATING')"
                ),
                {"ids": docs or ["none"]},
            )
        evidence = source_proof(state) | {
            "status": "PASS",
            "original_records": proof,
            "original_offer_identities": len(state["catalog_identities"]),
            "offers_now": offers,
            "original_document_files": len(state["files"]),
            "cleanup": cleanup,
        }
        assert (
            hashlib.sha256(
                (
                    settings.presentation_storage / "keys/commercial-offers.key"
                ).read_bytes()
            ).hexdigest()
            == state["key_hash"]
        )
        for key, fingerprint in state["files"].items():
            assert (
                hashlib.sha256(
                    (settings.presentation_storage / key).read_bytes()
                ).hexdigest()
                == fingerprint
            ), key
        if cleanup:
            async with sessions.begin() as db:
                for user in state["users"]:
                    assert user["name"].startswith("clean-start-" + state["run"])
                    assert (
                        await db.scalar(
                            text("SELECT display_name FROM users WHERE id=:id"),
                            {"id": user["id"]},
                        )
                        == user["name"]
                    )
                for identifier in projects:
                    await db.execute(
                        bound("DELETE FROM projects WHERE id=:id AND user_id IN :ids"),
                        {"id": identifier, "ids": owners},
                    )
                for identifier in chats:
                    await db.execute(
                        bound("DELETE FROM chats WHERE id=:id AND user_id IN :ids"),
                        {"id": identifier, "ids": owners},
                    )
                for identifier in owners:
                    await db.execute(
                        text("DELETE FROM users WHERE id=:id"), {"id": identifier}
                    )
            base = (settings.presentation_storage / "documents").resolve()
            for identifier in docs:
                target = (base / identifier).resolve()
                assert target.parent == base and identifier in state["documents"]
                if target.exists():
                    shutil.rmtree(target)
            if state.get("edge_pid") and psutil.pid_exists(state["edge_pid"]):
                process = psutil.Process(state["edge_pid"])
                profile = Path(state["edge_profile"]).resolve()
                assert profile.parent == (ROOT / ".local").resolve()
                assert profile.name == "client-selection-edge-" + state["run"]
                assert "--user-data-dir=" + str(profile) in process.cmdline()
                owned = [process, *process.children(recursive=True)]
                for p in reversed(owned):
                    try:
                        p.terminate()
                    except psutil.NoSuchProcess:
                        pass
                _, alive = await asyncio.to_thread(psutil.wait_procs, owned, timeout=5)
                for p in alive:
                    p.kill()
                await asyncio.to_thread(psutil.wait_procs, alive, timeout=5)
                if profile.exists():
                    shutil.rmtree(profile)
            async with sessions() as db:
                await db.execute(text("SET TRANSACTION READ ONLY"))
                await protected(
                    db, state, projects or ["none"], chats or ["none"], docs or ["none"]
                )
                assert not await db.scalar(
                    bound("SELECT count(*) FROM users WHERE id IN :ids"),
                    {"ids": owners},
                )
                owned_notifications = await db.scalar(bound("SELECT count(*) FROM manager_notifications WHERE user_id IN :ids"), {"ids": owners})
                owned_visitors = await db.scalar(bound("SELECT count(*) FROM offer_visitors WHERE document_id IN :ids"), {"ids": docs or ["none"]})
                owned_events = await db.scalar(bound("SELECT count(*) FROM manager_events WHERE user_id IN :ids"), {"ids": owners})
                assert owned_notifications == owned_visitors == owned_events == 0
                evidence["remaining_owned_visitors_notifications_events"] = 0
            evidence["removed"] = {
                "users": len(owners),
                "projects": len(projects),
                "documents": len(docs),
            }
        async with httpx.AsyncClient(timeout=30) as client:
            evidence["health"] = {
                name: (await client.get(url)).status_code
                for name, url in [
                    ("api", "http://127.0.0.1:8000/api/health"),
                    ("web", "http://127.0.0.1:3000/"),
                    ("external", EXTERNAL + "/api/health"),
                ]
            }
        assert all(code == 200 for code in evidence["health"].values())
        baseline.save(
            PUBLIC / ("cleanup.json" if cleanup else "preservation.json"), evidence
        )
        if cleanup:
            LEDGER.unlink()
        print("Preservation verified; exact-ID cleanup:", cleanup)
    finally:
        await engine.dispose()


async def main():
    phase = sys.argv[1]
    if phase == "prepare":
        baseline.LEDGER, baseline.PUBLIC = LEDGER, PUBLIC
        return await baseline.prepare()
    state = json.loads(LEDGER.read_text("utf-8"))
    if phase == "restart-verify":
        from quality.chat_workflow_acceptance import run

        offer = state["offers"][0]

        async def capture():
            async with (
                httpx.AsyncClient(
                    base_url=EXTERNAL, cookies=state["users"][0]["cookies"], timeout=60
                ) as manager,
                httpx.AsyncClient(
                    base_url=EXTERNAL, cookies=state["http_guests"][0], timeout=60
                ) as guest,
            ):
                values = {}
                for name, client, path in [
                    ("notifications", manager, "/api/notifications"),
                    ("visitor_state", guest, offer["path"] + "/selection"),
                    ("published_snapshot", guest, offer["path"]),
                ]:
                    response = await client.get(path)
                    assert response.status_code == 200, (name, response.status_code)
                    values[name] = hashlib.sha256(
                        json.dumps(
                            response.json(), sort_keys=True, ensure_ascii=True
                        ).encode()
                    ).hexdigest()
                return values

        before = await capture()
        await run("restart", ROOT, LEDGER, PUBLIC)
        after = await capture()
        assert before == after
        baseline.save(
            PUBLIC / "restart.json",
            {
                "status": "PASS",
                "path": EXTERNAL,
                "unchanged_digests": before,
                "persisted_visitor_and_unread_groups": True,
            },
        )
        print("Real HTTPS state survived main API restart")
        return
    if phase in ("deploy", "restart", "web"):
        from quality.chat_workflow_acceptance import run

        source_proof(state)
        return await run(phase, ROOT, LEDGER, PUBLIC)
    if phase in ("setup", "http", "processes", "load", "control", "race"):
        from quality.client_selection_http import run

        return await run(phase, state, ROOT, LEDGER, PUBLIC)
    if phase == "browser":
        from check_client_selection_browser import run

        return await run(state, ROOT, LEDGER, PUBLIC)
    if phase == "lifecycle":
        from quality.client_selection_lifecycle import run

        return await run(state, ROOT, LEDGER, PUBLIC)
    if phase in ("verify", "cleanup"):
        return await verify(state, phase == "cleanup")
    raise ValueError(phase)


if __name__ == "__main__":
    asyncio.run(main())
