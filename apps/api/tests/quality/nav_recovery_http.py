"""HTTP lifecycle acceptance, catalog snapshots and exact-run cleanup."""

import json

from sqlalchemy import text

from app.config import Settings
from app.database.connection import create_database


async def run(phase, state, root, ledger, public):
    from check_chat_workflow import save
    from check_nav_delete_recovery import catalog

    if phase == "sample":
        engine, sessions = create_database(Settings().database_url)
        try:
            async with sessions() as db:
                await db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
                current = await catalog(db)
            samples = (
                json.loads((public / "import-samples.json").read_text("utf-8"))
                if (public / "import-samples.json").exists()
                else [state["catalog_before"]]
            )
            samples.append(current)
            save(public / "import-samples.json", samples)
            print("Catalog", current["at"], current["offers"], current["families"], current["state"])
        finally:
            await engine.dispose()
        return
    if phase == "http":
        from quality.nav_recovery_http_cases import run as cases

        return await cases(state, root, ledger, public)
    if phase == "race":
        from quality.nav_recovery_http_cases import race

        return await race(state, ledger, public)
    if phase == "browser-draft":
        import httpx
        from check_chat_workflow import call

        project = state["http_project"]["id"]
        async with httpx.AsyncClient(
            base_url="http://127.0.0.1:8000", cookies=state["users"][0]["cookies"], timeout=60
        ) as client:
            current = await call(client, "GET", f"/api/projects/{project}/offer-state")
            assert current["offer"] is None
            collection = await call(client, "GET", f"/api/projects/{project}/collection")
            doc = await call(
                client,
                "POST",
                f"/api/projects/{project}/offers",
                json={"offer_generation": current["generation"], "collection_version": collection["version"]},
            )
            state["documents"].append(doc["id"])
            state["browser_document"] = doc["id"]
            save(ledger, state)
        print("Task-owned browser draft ready")
        return
    if phase in {"verify", "cleanup"}:
        from quality.nav_recovery_cleanup import run as cleanup

        return await cleanup(state, root, ledger, public, phase == "cleanup")
    raise ValueError(phase)
