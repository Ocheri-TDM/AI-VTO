"""Read-only comparison against trusted Git HEAD on one fixed research snapshot."""

import asyncio
import json
import statistics
import subprocess
import time

from app.application.index_research import IndexedResearchService
from app.config import Settings
from app.database.connection import create_database
from app.database.index import IndexRepository
from app.database.research import ResearchRepository
from app.domain.research import ResearchFilters
from check_chat_workflow import save
from sqlalchemy import event


async def run(state, root, ledger, public):
    original = await asyncio.to_thread(
        subprocess.run,
        ["git", "show", "HEAD:apps/api/app/application/product_grid.py"],
        cwd=root,
        check=True,
        capture_output=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    source = original.stdout.decode("utf-8")
    namespace = {"__name__": "app.application._trusted_facet_before"}
    exec(compile(source, "Git HEAD product_grid.py", "exec"), namespace)  # noqa: S102 -- locally trusted repository baseline, no external input
    old = namespace["ProductGridMixin"].grid_response
    settings = Settings()
    engine, sessions = create_database(settings.database_url)
    grid = IndexedResearchService(
        ResearchRepository(sessions),
        [],
        settings,
        None,
        IndexRepository(sessions, settings),
    )
    identifier = json.loads((public / "after.json").read_text("utf-8"))["cases"][0][
        "research_id"
    ]
    r = await grid.repository.get(identifier, hydrate=False)
    r.view.filters, r.view.effective_overrides, r.view.hidden_branch_ids = (
        ResearchFilters(),
        [],
        [],
    )
    r.view.sorting = "relevance"
    # Compare facet execution on the same freshly verified semantic membership,
    # keeping the independently recorded historical before/after controls intact.
    await grid.grid_pool(r)
    captured, recording = [], False

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def count_sql(_connection, _cursor, statement, parameters, _context, _many):
        if recording:
            captured.append((statement, parameters))

    results, explained = {}, []
    try:
        await old(grid, r)
        await grid.grid_response(r)
        for label, method in [
            ("before", lambda: old(grid, r)),
            ("after", lambda: grid.grid_response(r)),
        ]:
            samples = []
            for _ in range(3):
                captured.clear()
                recording = True
                at = time.perf_counter()
                value = await method()
                elapsed = (time.perf_counter() - at) * 1000
                recording = False
                samples.append(
                    {
                        "ms": round(elapsed, 2),
                        "sql_queries": len(captured),
                        "json_bytes": len(
                            json.dumps(
                                value, ensure_ascii=False, separators=(",", ":")
                            ).encode()
                        ),
                        "offers": value["matching_count"],
                    }
                )
            results[label] = {
                "samples": samples,
                "median_ms": statistics.median(s["ms"] for s in samples),
                "first_page_ids": [p["id"] for p in value["products"]],
                "matching_count": value["matching_count"],
                "size_options": len(value["effective_filters"]["options"]["sizes"]),
            }
            relevant = [
                (statement, params)
                for statement, params in captured
                if statement.lstrip().upper().startswith("SELECT")
                and ("DISTINCT" in statement or "offer_attribute_values" in statement)
            ]
            async with engine.connect() as connection:
                await connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                for statement, parameters in relevant[:3]:
                    plan = (
                        await connection.exec_driver_sql(
                            "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + statement,
                            parameters,
                        )
                    ).scalar()
                    if isinstance(plan, str):
                        plan = json.loads(plan)
                    nodes = []

                    def collect(node, collected=nodes):
                        collected.append(
                            {
                                k: node[k]
                                for k in [
                                    "Node Type",
                                    "Relation Name",
                                    "Index Name",
                                    "Actual Rows",
                                    "Actual Loops",
                                ]
                                if k in node
                            }
                        )
                        for child in node.get("Plans", []):
                            collect(child, collected)

                    collect(plan[0]["Plan"])
                    explained.append(
                        {
                            "phase": label,
                            "planning_ms": plan[0].get("Planning Time"),
                            "execution_ms": plan[0].get("Execution Time"),
                            "nodes": nodes,
                        }
                    )
        assert results["before"]["first_page_ids"] == results["after"]["first_page_ids"]
        assert results["before"]["matching_count"] == results["after"]["matching_count"]
        save(
            public / "performance.json",
            {
                "status": "PASS",
                "method": "same fixed three-branch research IDs and current index; warmed service, 3 measurements; no mutation, Qwen or supplier HTTP",
                "results": results,
                "plans": explained,
            },
        )
        print(
            json.dumps(
                {
                    phase: {
                        "median_ms": value["median_ms"],
                        "samples": value["samples"],
                    }
                    for phase, value in results.items()
                }
            )
        )
    finally:
        await engine.dispose()
