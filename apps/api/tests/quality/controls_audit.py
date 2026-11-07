"""Cross-check full HTTP pools and persisted revisions, not just status codes."""

import json

from sqlalchemy import text

from app.config import Settings
from app.database.connection import create_database
from quality.regression_acceptance import PUBLIC, save


async def run(state):
    evidence = json.loads((PUBLIC / "controls-after.json").read_text("utf-8"))
    engine, sessions = create_database(Settings().database_url)
    try:
        async with sessions() as db:
            await db.execute(text("SET TRANSACTION READ ONLY"))
            for case in evidence["cases"]:
                intent = case["intent"]
                expected = {
                    "pen",
                    "notebook",
                    "thermomug",
                    "cap" if case["name"] in ("A", "C") else "baseballcap",
                }
                saved = await db.scalar(
                    text("SELECT payload FROM research_sessions WHERE id=:id"), {"id": case["research_id"]}
                )
                assert saved["intent"] == intent
                checks = {
                    "http_result": case["http_status"] in (200, 202),
                    "categories": set(intent["categories"]) == expected,
                    "four_branches": len(intent["branches"]) == 4,
                    "no_model_or_brand": not intent["models"] and not intent["brands"],
                    "global_color": intent["colors"] == ["RED"],
                    "global_quantity": intent["quantity"] == 200,
                    "branch_constraints": all(
                        b["colors"] == ["RED"] and b["quantity"] == 200 for b in intent["branches"]
                    ),
                    "persisted_intent": saved["intent"] == intent,
                    "all_pages": len(case["product_ids"]) == case["total"],
                }
                assert all(checks.values()), (case["name"], checks)
                case["checks"] = checks
                case["persisted_revision"] = saved.get("revision")
                case["persisted_source_mode"] = saved.get("source_mode")
        pools = {(c["name"], c["after_tippo"]): set(c["product_ids"]) for c in evidence["cases"]}
        for contextual in (False, True):
            assert pools["A", contextual] == pools["C", contextual]
            assert pools["B", contextual] == pools["D", contextual]
        for name in "ABCD":
            assert pools[name, False] == pools[name, True]
        evidence["cross_checks"] = {
            "punctuation_full_id_equivalence": True,
            "tippo_workspace_isolation": True,
            "persisted_intents": True,
        }
        save(PUBLIC / "controls-after.json", evidence)
        print("Eight real HTTP controls: persisted intents and complete membership equivalence verified")
    finally:
        await engine.dispose()
