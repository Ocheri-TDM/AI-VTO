"""Main API/SQL acceptance. Only exact ledger-owned resources are disposable.

prepare -> run -> check_effective_filters_browser.py -> cleanup.
No importer calls, catalog writes or presentation mutations.
"""

import argparse
import asyncio
import json
import secrets
import sys
import time
from uuid import uuid4

import httpx
from check_color_scope import EXPLICIT, QUERY, ROOT, fingerprint, save
from sqlalchemy import delete, select

sys.path.insert(0, str(ROOT / "apps/api"))
from app.config import Settings
from app.database.connection import create_database
from app.database.models import ResearchRecord, User

LEDGER = ROOT / ".local/effective-filters-ledger.json"
PUBLIC = ROOT / "docs/screenshots/effective-filters"
MIXED = "Красные ручки от 500 шт и синие бутылки от 100 шт."


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=["prepare", "run", "cleanup"])
    phase = parser.parse_args().phase
    state = (
        json.loads(LEDGER.read_text("utf-8"))
        if LEDGER.exists()
        else {"run": uuid4().hex, "users": [], "projects": []}
    )
    engine, sessions = create_database(Settings().database_url)
    evidence = {"checks": [], "cases": {}}

    async def call(c, method, path, **kw):
        response = await c.request(method, path, **kw)
        response.raise_for_status()
        return response.json() if response.status_code != 204 else None

    def check(label, valid):
        assert valid, label
        evidence["checks"].append({"check": label, "status": "PASS"})

    try:
        async with httpx.AsyncClient(
            base_url="http://127.0.0.1:8000", timeout=180
        ) as c:
            if phase == "prepare":
                assert not LEDGER.exists()
                state["catalog_before"] = await fingerprint(sessions)
                owner = await call(
                    c,
                    "POST",
                    "/api/auth/register",
                    json={
                        "name": "effective-filters-" + state["run"],
                        "password": secrets.token_urlsafe(24),
                    },
                )
                state["users"].append({"id": owner["id"], "cookies": dict(c.cookies)})
                save(LEDGER, state)
                for _ in range(2):
                    chat = await call(
                        c,
                        "POST",
                        "/api/chats",
                        json={"project_name": "Effective filter acceptance"},
                    )
                    project = next(
                        p
                        for p in await call(c, "GET", "/api/projects")
                        if p["chat_id"] == chat["id"]
                    )
                    state["projects"].append(project["id"])
                    save(LEDGER, state)
                print("Prepared exact-ID isolated owner and projects")
                return
            assert state["users"] and state["projects"]
            c.cookies.update(state["users"][0]["cookies"])
            assert (await call(c, "GET", "/api/auth/me"))["id"] == state["users"][0][
                "id"
            ]
            if phase == "cleanup":
                for project in state["projects"]:
                    response = await c.delete("/api/projects/" + project)
                    assert response.status_code in (204, 404)
                async with sessions.begin() as db:
                    owner = await db.get(User, state["users"][0]["id"])
                    assert (
                        owner
                        and owner.display_name == "effective-filters-" + state["run"]
                    )
                    await db.execute(delete(User).where(User.id == owner.id))
                save(
                    PUBLIC / "cleanup.json",
                    {
                        "catalog_before": state["catalog_before"],
                        "catalog_after": await fingerprint(sessions),
                        "deleted_project_ids": state["projects"],
                        "deleted_user_ids": [state["users"][0]["id"]],
                    },
                )
                LEDGER.unlink()
                print("Removed only exact ledger-owned resources")
                return

            async def record(label, r, seconds=None):
                products = list(r["products"])
                cursor = r["next_cursor"]
                while cursor:
                    page = await call(
                        c,
                        "GET",
                        f"/api/researches/{r['id']}/products",
                        params={"limit": 100, "cursor": cursor},
                    )
                    products.extend(page["products"])
                    cursor = page["next_cursor"]
                ids = {p["id"] for p in products}
                check(
                    label + " full matching pagination",
                    len(ids) == len(products) == r["filtered_total"],
                )
                async with sessions() as db:
                    stored = (
                        await db.execute(
                            select(ResearchRecord.payload).where(
                                ResearchRecord.id == r["id"]
                            )
                        )
                    ).scalar_one()
                check(label + " view persisted", stored["view"] == r["view"])
                evidence["cases"][label] = {
                    "seconds": seconds,
                    "response": {
                        key: r[key]
                        for key in (
                            "id",
                            "intent",
                            "view",
                            "query_categories",
                            "filtered_total",
                            "base_total",
                            "revision",
                        )
                    },
                    "effective_filters": {
                        key: r["effective_filters"][key]
                        for key in ("fields", "branches")
                    },
                    "persisted": {
                        key: stored[key]
                        for key in (
                            "intent",
                            "view",
                            "branch_offer_ids",
                            "pool_offer_ids",
                        )
                    },
                    "offer_ids": sorted(ids),
                    "product_evidence": [
                        {
                            key: p[key]
                            for key in (
                                "id",
                                "name",
                                "colors",
                                "availability",
                                "price_kzt",
                            )
                        }
                        for p in products
                    ],
                }
                save(PUBLIC / "http.json", evidence)
                return ids

            async def submit(query, label, project=None):
                start = time.perf_counter()
                value = await call(
                    c,
                    "POST",
                    f"/api/projects/{project or state['projects'][0]}/requests",
                    json={"query": query},
                )
                r = value["research"]
                await record(label, r, round(time.perf_counter() - start, 4))
                return r, value

            async def edit(r, label, **kw):
                start = time.perf_counter()
                value = await call(
                    c,
                    "PATCH",
                    f"/api/researches/{r['id']}/view",
                    json={"version": r["view"]["version"], **kw},
                )
                await record(label, value, round(time.perf_counter() - start, 4))
                return value

            def fields(r):
                return r["effective_filters"]["fields"]

            r, first = await submit(QUERY, "common")
            baseline = set(evidence["cases"]["common"]["offer_ids"])
            source = r["intent"]
            branches = [b["id"] for b in r["query_categories"]]
            check(
                "common exact canonical fields",
                fields(r)["colors"]["values"] == ["DARK_BLUE"]
                and fields(r)["minimum_quantity"]["number"] == 250,
            )
            check(
                "no copied additional filters",
                not r["view"]["effective_overrides"]
                and not r["view"]["filters"]["colors"]
                and r["view"]["filters"]["minimum_quantity"] is None,
            )
            # Independent raw SQL truth: published title/color/stock only. No production resolver.
            from sqlalchemy import text

            async with sessions() as db:
                rows = (
                    (
                        await db.execute(
                            text(
                                "SELECT o.id,o.normalized_colors,p.stock,lower(o.payload->>'name') name FROM supplier_offers o JOIN product_observations p ON p.offer_id=o.id WHERE o.lifecycle<>'REMOVED'"
                            )
                        )
                    )
                    .mappings()
                    .all()
                )

            def branch_ids(color, quantity):
                # Standalone title subjects; a box "for pens" or a set "with a
                # mug" is not an independently requested pen/mug. This is an
                # acceptance oracle over public names, not the runtime matcher.
                names = {
                    "pen": ("ручка", "шариковая ручка"),
                    "notebook": ("ежедневник",),
                    "shopperbag": ("сумка-шоппер", "шоппер"),
                    "thermomug": ("термокружка", "вакуумная термокружка"),
                }
                return {
                    key: {
                        row["id"]
                        for row in rows
                        if row["name"].casefold().startswith(term)
                        and color in row["normalized_colors"]
                        and row["stock"] is not None
                        and row["stock"] >= quantity
                    }
                    for key, term in names.items()
                }

            expected = branch_ids("DARK_BLUE", 250)
            evidence["independent_sql_branch_ids"] = {
                k: sorted(v) for k, v in expected.items()
            }
            # Category matcher is precision-first; explicitly inspect any SQL lexical surplus.
            check(
                "every common result satisfies raw evidence",
                baseline <= set().union(*expected.values()),
            )
            check(
                "SQL full common equality", baseline == set().union(*expected.values())
            )
            if baseline:
                await call(
                    c,
                    "POST",
                    f"/api/projects/{state['projects'][0]}/collection/items",
                    json={"offer_id": min(baseline)},
                )
            collection = await call(
                c, "GET", f"/api/projects/{state['projects'][0]}/collection"
            )
            r = await edit(
                r,
                "replace_red",
                effective_changes=[
                    {"field": "colors", "action": "set", "values": ["RED"]}
                ],
            )
            check(
                "red replaces source rather than AND",
                set(evidence["cases"]["replace_red"]["offer_ids"])
                == set().union(*branch_ids("RED", 250).values()),
            )
            r = await edit(
                r,
                "quantity_500",
                effective_changes=[
                    {"field": "minimum_quantity", "action": "set", "number": 500}
                ],
            )
            check(
                "quantity matches actual rows",
                set(evidence["cases"]["quantity_500"]["offer_ids"])
                == set().union(*branch_ids("RED", 500).values()),
            )
            r = await edit(
                r,
                "clear_color_quantity",
                effective_changes=[
                    {"field": "colors", "action": "clear"},
                    {"field": "minimum_quantity", "action": "clear"},
                ],
            )
            check(
                "explicit clear controls",
                fields(r)["colors"]["mode"] == "none"
                and fields(r)["minimum_quantity"]["mode"] == "none",
            )
            check(
                "clear really widens matching pool",
                baseline < set(evidence["cases"]["clear_color_quantity"]["offer_ids"]),
            )
            r = await edit(r, "reset", restore="reset")
            check(
                "reset restores source IDs",
                set(evidence["cases"]["reset"]["offer_ids"]) == baseline,
            )
            check(
                "original intent unchanged",
                evidence["cases"]["reset"]["persisted"]["intent"] == source,
            )
            r = await edit(
                r, "hide", branch_actions=[{"action": "hide", "branch_id": branches[0]}]
            )
            check(
                "hidden branches retain common state",
                fields(r)["colors"]["values"] == ["DARK_BLUE"]
                and fields(r)["minimum_quantity"]["number"] == 250,
            )
            r = await edit(r, "show", branch_actions=[{"action": "show_all"}])
            check(
                "show restores IDs and stable branches",
                set(evidence["cases"]["show"]["offer_ids"]) == baseline
                and [b["id"] for b in r["query_categories"]] == branches,
            )
            same, _ = await submit(EXPLICIT, "same_local")
            check(
                "same local projection",
                fields(same)["colors"]["values"] == ["DARK_BLUE"]
                and fields(same)["minimum_quantity"]["number"] == 250
                and set(evidence["cases"]["same_local"]["offer_ids"]) == baseline,
            )
            mixed, _ = await submit(MIXED, "mixed")
            check(
                "mixed common controls",
                fields(mixed)["colors"]["mode"] == "mixed"
                and fields(mixed)["minimum_quantity"]["number"] is None,
            )
            red = next(
                b["id"] for b in mixed["query_categories"] if b["concept"] == "pen"
            )
            mixed = await edit(
                mixed,
                "local_quantity",
                effective_changes=[
                    {
                        "field": "minimum_quantity",
                        "action": "set",
                        "branch_id": red,
                        "number": 501,
                    }
                ],
            )
            check(
                "local edit preserves bottle quantity",
                next(
                    b for b in mixed["intent"]["branches"] if b["category"] == "bottle"
                )["quantity"]
                == 100,
            )
            mixed = await edit(
                mixed,
                "remove_pen",
                branch_actions=[{"action": "remove", "branch_id": red}],
            )
            check(
                "removed branch excluded from projection",
                fields(mixed)["colors"]["values"] == ["BLUE"]
                and fields(mixed)["minimum_quantity"]["number"] == 100,
            )
            await edit(mixed, "undo_remove", restore="undo")
            unbounded, _ = await submit(
                "ручки и синие бутылки от 100 шт", "one_unconstrained"
            )
            check(
                "unconstrained and constrained stays mixed",
                fields(unbounded)["colors"]["mode"] == "mixed",
            )
            fresh, _ = await submit("красные ручки от 500 шт", "new_standalone")
            check(
                "new request has own conditions",
                fields(fresh)["colors"]["values"] == ["RED"]
                and fields(fresh)["minimum_quantity"]["number"] == 500
                and len(fresh["query_categories"]) == 1,
            )
            restored = await call(
                c,
                "POST",
                f"/api/projects/{state['projects'][0]}/queries/{first['query']['id']}/activate",
                json={},
            )
            await record("history", restored["research"])
            check(
                "history restores fields and IDs",
                fields(restored["research"])["colors"]["values"] == ["DARK_BLUE"]
                and set(evidence["cases"]["history"]["offer_ids"]) == baseline,
            )
            check(
                "project collection unchanged",
                await call(c, "GET", f"/api/projects/{state['projects'][0]}/collection")
                == collection,
            )
            state["browser_project"] = state["projects"][0]
            save(LEDGER, state)
            evidence["catalog_end"] = await fingerprint(sessions)
            save(PUBLIC / "http.json", evidence)
            print(
                json.dumps(
                    {
                        "checks": len(evidence["checks"]),
                        "common": len(baseline),
                        "red": len(evidence["cases"]["replace_red"]["offer_ids"]),
                        "red500": len(evidence["cases"]["quantity_500"]["offer_ids"]),
                        "cleared": len(
                            evidence["cases"]["clear_color_quantity"]["offer_ids"]
                        ),
                    }
                )
            )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
