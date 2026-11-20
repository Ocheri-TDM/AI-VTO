"""Local-only facet acceptance. Never starts a tunnel or a catalog import."""

import asyncio
import copy
import hashlib
import json
import sys

import check_clean_start as baseline
import httpx
from check_chat_workflow import call, save

ROOT = baseline.ROOT
LEDGER = ROOT / ".local/contextual-facets-ledger.json"
PUBLIC = ROOT / "docs/screenshots/contextual-facets"
CHANGED = {
    "apps/api/app/database/models.py",
    "apps/api/app/database/index.py",
    "apps/api/app/domain/research.py",
    "apps/api/app/domain/intent.py",
    "apps/api/app/domain/product_purpose.py",
    "apps/api/app/application/product_grid.py",
    "apps/api/app/api/research.py",
}


async def controls(state, phase):
    output = {"cases": []}
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:8000",
        cookies=state["users"][0]["cookies"],
        timeout=180,
    ) as client:
        for contextual in (False, True):
            chat = await call(
                client,
                "POST",
                "/api/chats",
                json={"project_name": "Facet acceptance " + state["run"]},
            )
            projects = await call(client, "GET", "/api/projects")
            project = next(p for p in projects if p["chat_id"] == chat["id"])["id"]
            state["projects"].append(project)
            save(LEDGER, state)
            for query in (["Футболки", "Ежедневники"] if contextual else []) + [
                "Бутылки, кружки, зонты"
            ]:
                value = await call(
                    client,
                    "POST",
                    "/api/researches",
                    json={"chat_id": chat["id"], "query": query},
                )
                ids = [p["id"] for p in value["products"]]
                cursor = value["next_cursor"]
                complete = query == "Бутылки, кружки, зонты"
                while cursor and complete:
                    page = await call(
                        client,
                        "GET",
                        f"/api/researches/{value['id']}/products",
                        params={"cursor": cursor, "limit": 100},
                    )
                    ids.extend(p["id"] for p in page["products"])
                    cursor = page["next_cursor"]
                assert len(ids) == len(set(ids))
                if complete:
                    assert len(ids) == value["matching_count"]
                output["cases"].append(
                    {
                        "query": query,
                        "contextual": contextual,
                        "research_id": value["id"],
                        "intent": value["intent"],
                        "total": value["matching_count"],
                        "offer_ids": ids,
                        "offer_ids_scope": "complete pagination"
                        if complete
                        else "first page; totals and facets cover full scope",
                        "facets": value["facets"],
                        "effective_filters": value.get("effective_filters"),
                        "category_facets": value.get("category_facets"),
                        "timings": value.get("timings"),
                    }
                )
                save(PUBLIC / (phase + ".json"), output)
                state.setdefault("researches", []).append(value["id"])
                save(LEDGER, state)
                print(phase, query, "offers:", value["matching_count"], flush=True)


async def main():
    baseline.LEDGER, baseline.PUBLIC = LEDGER, PUBLIC
    phase = sys.argv[1]
    if phase == "prepare":
        return await baseline.prepare()
    state = json.loads(LEDGER.read_text("utf-8"))
    if phase in ("before", "after"):
        return await controls(state, phase)
    if phase in ("accept", "benchmark", "browser"):
        module = __import__(
            "check_contextual_facets_"
            + {"accept": "http", "benchmark": "benchmark", "browser": "browser"}[phase]
        )
        return await module.run(state, ROOT, LEDGER, PUBLIC)
    if phase in ("verify", "cleanup"):
        verified = copy.deepcopy(state)
        changed = []
        for key, digest in state["backend"].items():
            actual = hashlib.sha256((ROOT / key).read_bytes()).hexdigest()
            if actual != digest:
                assert key.replace("\\", "/") in CHANGED, key
                changed.append(key)
                verified["backend"][key] = actual
        await baseline.verify(verified, phase == "cleanup")
        path = PUBLIC / ("cleanup.json" if phase == "cleanup" else "preservation.json")
        result = json.loads(path.read_text("utf-8"))
        result["protected_backend_files"] = result.pop(
            "backend_source_files_unchanged"
        ) - len(changed)
        result["explicit_projection_and_facet_changes"] = changed
        save(path, result)
        return
    if phase in ("web", "restart"):
        from quality.chat_workflow_acceptance import run

        return await run(phase, ROOT, LEDGER, PUBLIC)
    raise ValueError(phase)


if __name__ == "__main__":
    asyncio.run(main())
