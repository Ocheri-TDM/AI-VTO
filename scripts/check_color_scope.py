"""Main API color acceptance. Exact-ID disposable ledger; catalog is read-only.

prepare -> run -> check_color_scope_browser.py -> cleanup.
No resets, importer calls, synthetic main-catalog data or PDF generation.
"""

import argparse
import asyncio
import json
import secrets
import sys
import time
from pathlib import Path
from uuid import uuid4

import httpx
from sqlalchemy import delete, func, select, text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))
from app.application.catalog_identity import brand_registry
from app.application.index_research import IndexedResearchService
from app.config import Settings
from app.database.connection import create_database
from app.database.index import IndexRepository
from app.database.models import (
    IndexedProduct,
    ProductObservation,
    ResearchRecord,
    SupplierOffer,
    User,
)
from app.database.research import ResearchRepository
from app.domain.research import ResearchIntent

LEDGER = ROOT / ".local/color-scope-ledger.json"
PUBLIC = ROOT / "docs/screenshots/color-scope"
QUERY = "Темно синий цвет. 250 шт. ручка, ежедневник, шоппер, термокружка."
EXPLICIT = (
    "Тёмно-синие ручки от 250 шт, тёмно-синие ежедневники от 250 шт, "
    "тёмно-синие шопперы от 250 шт, тёмно-синие термокружки от 250 шт."
)


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str), "utf-8"
    )


async def fingerprint(sessions):
    async with sessions() as db:
        return dict(
            (
                await db.execute(
                    text(
                        "SELECT count(*) offers,md5(string_agg(o.id||o.product_id||o.source_url||coalesce(o.payload::text,'')||coalesce(p.payload::text,'')||coalesce(p.observed_at::text,''),'' ORDER BY o.id)) fingerprint FROM supplier_offers o LEFT JOIN product_observations p ON p.offer_id=o.id"
                    )
                )
            )
            .mappings()
            .one()
        )


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=["prepare", "run", "cleanup"])
    phase = parser.parse_args().phase
    state = (
        json.loads(LEDGER.read_text("utf-8"))
        if LEDGER.exists()
        else {"run": uuid4().hex, "users": [], "projects": []}
    )
    settings = Settings()
    engine, sessions = create_database(settings.database_url)

    async def call(c, method, path, **kw):
        r = await c.request(method, path, **kw)
        r.raise_for_status()
        return r.json() if r.status_code != 204 else None

    async def project(c):
        chat = await call(
            c, "POST", "/api/chats", json={"project_name": "Color scope acceptance"}
        )
        p = next(
            p
            for p in await call(c, "GET", "/api/projects")
            if p["chat_id"] == chat["id"]
        )
        state["projects"].append(p["id"])
        save(LEDGER, state)
        return p["id"]

    try:
        if phase == "prepare":
            assert not LEDGER.exists(), "Existing ledger must be cleaned first"
            state["catalog_before"] = await fingerprint(sessions)
            async with httpx.AsyncClient(
                base_url="http://127.0.0.1:8000", timeout=180
            ) as c:
                u = await call(
                    c,
                    "POST",
                    "/api/auth/register",
                    json={
                        "name": "color-scope-" + state["run"],
                        "password": secrets.token_urlsafe(24),
                    },
                )
                state["users"].append({"id": u["id"], "cookies": dict(c.cookies)})
                save(LEDGER, state)
                await project(c)
                await project(c)
            print("Isolated color-scope owner/projects prepared")
            return
        assert state["users"] and state["projects"], "Run prepare first"
        async with httpx.AsyncClient(
            base_url="http://127.0.0.1:8000",
            timeout=180,
            cookies=state["users"][0]["cookies"],
        ) as c:
            if phase == "cleanup":
                assert (await call(c, "GET", "/api/auth/me"))["id"] == state["users"][
                    0
                ]["id"]
                for id in state["projects"]:
                    r = await c.delete("/api/projects/" + id)
                    assert r.status_code in (204, 404), r.text
                async with sessions.begin() as db:
                    owner = await db.get(User, state["users"][0]["id"])
                    assert (
                        owner is not None
                        and owner.display_name == "color-scope-" + state["run"]
                    )
                    await db.execute(delete(User).where(User.id == owner.id))
                save(
                    PUBLIC / "cleanup.json",
                    {
                        "deleted_exact_project_ids": state["projects"],
                        "deleted_exact_user_ids": [state["users"][0]["id"]],
                        "catalog_after": await fingerprint(sessions),
                        "catalog_before": state["catalog_before"],
                    },
                )
                LEDGER.unlink()
                print("Only ledger-owned test resources removed")
                return
            evidence = {
                "checks": [],
                "cases": {},
                "catalog_start": await fingerprint(sessions),
            }

            def check(label, valid):
                assert valid, label
                evidence["checks"].append({"check": label, "status": "PASS"})

            async def record(label, r, action=None, seconds=None):
                ids = [p["id"] for p in r["products"]]
                products = list(r["products"])
                cursor = r["next_cursor"]
                while cursor:
                    page = await call(
                        c,
                        "GET",
                        f"/api/researches/{r['id']}/products",
                        params={"cursor": cursor, "limit": 100},
                    )
                    products.extend(page["products"])
                    ids.extend(p["id"] for p in page["products"])
                    cursor = page["next_cursor"]
                check(
                    label + " full pagination",
                    len(ids) == len(set(ids)) == r["filtered_total"],
                )
                async with sessions() as db:
                    stored = (
                        await db.execute(
                            select(ResearchRecord.payload).where(
                                ResearchRecord.id == r["id"]
                            )
                        )
                    ).scalar_one()
                check(label + " persisted intent", stored["intent"] == r["intent"])
                evidence["cases"][label] = {
                    "route": action,
                    "seconds": seconds,
                    "response": r,
                    "offer_ids": ids,
                    "all_products": products,
                    "persisted": stored,
                }
                save(PUBLIC / "after.json", evidence)
                return set(ids)

            async def submit(p, query, label):
                start = time.perf_counter()
                result = await call(
                    c, "POST", f"/api/projects/{p}/requests", json={"query": query}
                )
                ids = await record(
                    label,
                    result["research"],
                    result["route"]["action"],
                    round(time.perf_counter() - start, 4),
                )
                return result["research"], ids, result

            fresh = await project(c)
            state["browser_project"] = fresh
            save(LEDGER, state)
            r, ids_a, result = await submit(fresh, QUERY, "A_fresh")
            state["query_a"] = result["query"]["id"]
            check(
                "A four exact requirements",
                [b["category"] for b in r["intent"]["branches"]]
                == ["pen", "notebook", "shopperbag", "thermomug"]
                and all(
                    b["colors"] == ["DARK_BLUE"]
                    and b["quantity"] == 250
                    and not b["models"]
                    and not b["brands"]
                    for b in r["intent"]["branches"]
                ),
            )
            check(
                "A no duplicate view color",
                r["view"]["filters"]["colors"] == []
                and r["color_state"]["source_values"] == ["DARK_BLUE"],
            )
            check(
                "Actual same-offer shades and quantities",
                all(
                    any(
                        color["normalized_color"] == "DARK_BLUE"
                        for color in p["colors"]
                    )
                    and p["availability"]["availableNow"] is not None
                    and p["availability"]["availableNow"] >= 250
                    for p in evidence["cases"]["A_fresh"]["all_products"]
                ),
            )
            choice = next(iter(sorted(ids_a)), "vto:1823109")
            await call(
                c,
                "POST",
                f"/api/projects/{fresh}/collection/items",
                json={"offer_id": choice},
            )
            selected = await call(c, "GET", f"/api/projects/{fresh}/collection")
            r, ids_b, _ = await submit(fresh, EXPLICIT, "B_explicit")
            check("A/B complete IDs equal", ids_a == ids_b)
            contextual = state["projects"][1]
            await submit(contextual, "красные бутылки от 50 шт", "context_seed")
            r, contextual_ids, result = await submit(contextual, QUERY, "A_context")
            check(
                "Standalone replaces prior context",
                contextual_ids == ids_a and result["route"]["action"] == "NEW_SEARCH",
            )
            r, _, _ = await submit(fresh, QUERY, "A_state")
            branch_ids = [b["id"] for b in r["intent"]["branches"]]

            async def change(**kw):
                nonlocal r
                r = await call(
                    c,
                    "PATCH",
                    f"/api/researches/{r['id']}/view",
                    json={"version": r["view"]["version"], **kw},
                )
                return r

            await change(filters=r["view"]["filters"] | {"colors": ["RED"]})
            check(
                "Conflicting additional color is explicit",
                r["filtered_total"] == 0 and r["color_state"]["mode"] == "conflict",
            )
            await change(restore="reset")
            check(
                "Additional reset retains source color",
                r["color_state"]["source_values"] == ["DARK_BLUE"]
                and r["filtered_total"] == len(ids_a),
            )
            await change(
                branch_actions=[{"action": "hide", "branch_id": branch_ids[0]}]
            )
            check(
                "Hide retains other source colors",
                r["color_state"]["source_values"] == ["DARK_BLUE"],
            )
            await change(branch_actions=[{"action": "show_all"}])
            await change(
                branch_actions=[{"action": "remove", "branch_id": branch_ids[1]}]
            )
            notice = r["category_notice"]["id"]
            await change(branch_actions=[{"action": "restore", "removal_id": notice}])
            check(
                "Remove/restore stable branch IDs",
                [b["id"] for b in r["intent"]["branches"]] == branch_ids,
            )
            await change(restore="undo")
            await change(restore="undo")
            check(
                "Undo restores source state",
                [b["id"] for b in r["intent"]["branches"]] == branch_ids,
            )
            reread = await call(c, "GET", f"/api/researches/{r['id']}")
            check("F5 read preserves intent", reread["intent"] == r["intent"])
            r, _, _ = await submit(fresh, "теперь красные", "red_refinement")
            check(
                "Replacement has no hidden color intersection",
                all(b["colors"] == ["RED"] for b in r["intent"]["branches"])
                and r["intent"]["colors"] == ["RED"]
                and r["view"]["filters"]["colors"] == [],
            )
            r, _, _ = await submit(fresh, "цвет не важен", "remove_color")
            check(
                "Explicit removal clears source colors",
                r["color_state"]["mode"] == "none",
            )
            await submit(fresh, "бутылки", "independent_bottles")
            activated = await call(
                c, "POST", f"/api/projects/{fresh}/queries/{state['query_a']}/activate"
            )
            check(
                "History restores original corrected intent",
                activated["research"]["intent"]
                == evidence["cases"]["A_fresh"]["response"]["intent"],
            )
            check(
                "Project collection unchanged",
                await call(c, "GET", f"/api/projects/{fresh}/collection") == selected,
            )
            state["collection_before_browser"] = selected
            save(LEDGER, state)

            # Actual SQL aggregates on one read-only repeatable-read snapshot.
            grid = IndexedResearchService(
                ResearchRepository(sessions),
                [],
                settings,
                None,
                IndexRepository(sessions, settings),
            )
            registry = await brand_registry(sessions)
            evidence["sql"] = {}
            cases = [
                (label, evidence["cases"][label]["response"]["intent"])
                for label in ["A_fresh", "B_explicit"]
            ]
            cases += [
                ("before_" + str(n), value["persisted"]["intent"])
                for n, value in enumerate(state.get("before", []))
            ]
            async with sessions() as db:
                await db.execute(
                    text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
                )
                for label, intent_data in cases:
                    intent = ResearchIntent.model_validate(intent_data)
                    rows = []
                    snapshot_ids = set()
                    for b in intent.branches:
                        counts, sqls = {}, {}
                        for stage, colored, quantity in [
                            ("before_color", False, False),
                            ("after_color", True, False),
                            ("after_quantity", True, True),
                        ]:
                            branch = b.model_copy(
                                update={
                                    "colors": b.colors if colored else [],
                                    "quantity": b.quantity if quantity else None,
                                }
                            )
                            stmt = (
                                select(func.count())
                                .select_from(SupplierOffer)
                                .join(
                                    ProductObservation,
                                    ProductObservation.offer_id == SupplierOffer.id,
                                )
                                .join(
                                    IndexedProduct,
                                    IndexedProduct.id == SupplierOffer.product_id,
                                )
                                .where(
                                    *grid.grid_conditions(db, intent, branch, registry)
                                )
                            )
                            counts[stage] = await db.scalar(stmt)
                            compiled = stmt.compile(dialect=db.bind.dialect)
                            sqls[stage] = {
                                "sql": str(compiled),
                                "params": compiled.params,
                            }
                        rows.append(
                            {
                                "category": b.category,
                                "colors": b.colors,
                                "quantity": b.quantity,
                                "models": intent.models + b.models,
                                "counts": counts,
                                "statements": sqls,
                            }
                        )
                        ids_stmt = (
                            select(SupplierOffer.id)
                            .join(
                                ProductObservation,
                                ProductObservation.offer_id == SupplierOffer.id,
                            )
                            .join(
                                IndexedProduct,
                                IndexedProduct.id == SupplierOffer.product_id,
                            )
                            .where(*grid.grid_conditions(db, intent, b, registry))
                        )
                        sql_ids = sorted(await db.scalars(ids_stmt))
                        rows[-1]["sql_candidate_ids_before_semantics"] = sql_ids
                        snapshot_ids.update(sql_ids)
                    evidence["sql"][label] = rows
                    evidence.setdefault("repeatable_read_sql_ids", {})[label] = sorted(
                        snapshot_ids
                    )
            check(
                "A/B same repeatable-read SQL snapshot",
                evidence["repeatable_read_sql_ids"]["A_fresh"]
                == evidence["repeatable_read_sql_ids"]["B_explicit"],
            )
            evidence["catalog_end"] = await fingerprint(sessions)
            evidence["catalog_unchanged_during_http"] = (
                evidence["catalog_start"] == evidence["catalog_end"]
            )
            evidence["catalog_matches_baseline"] = (
                evidence["catalog_end"] == state["catalog_before"]
            )
            evidence["status"] = "PASS"
            save(PUBLIC / "after.json", evidence)
            print(
                json.dumps(
                    {
                        "status": "PASS",
                        "checks": len(evidence["checks"]),
                        "A_count": len(ids_a),
                        "B_count": len(ids_b),
                        "funnel": [
                            {k: v for k, v in b.items() if k != "statements"}
                            for b in evidence["sql"]["A_fresh"]
                        ],
                    },
                    ensure_ascii=True,
                )
            )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
