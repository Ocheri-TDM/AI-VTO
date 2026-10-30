"""HTTP purpose regression and HUD-state acceptance; exact-ID resource cleanup."""

import argparse
import asyncio
import json
import re
import secrets
import sys
import time
from pathlib import Path
from uuid import uuid4

import httpx
from sqlalchemy import delete, select, text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))
from app.config import Settings
from app.database.connection import create_database
from app.database.models import ResearchRecord, SupplierOffer, User
from app.domain.product_purpose import identity_inputs, purpose_match
from app.domain.research import ProductBranch


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", action="store_true")
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--old-base")
    args = parser.parse_args()
    engine, sessions = create_database(Settings().database_url)
    user = project = None
    evidence = {}
    async with httpx.AsyncClient(base_url=args.base, timeout=180) as c:

        async def commercial_fingerprint():
            async with sessions() as db:
                row = (
                    (
                        await db.execute(
                            text(
                                "SELECT count(*) offers,md5(string_agg(o.id||o.product_id||o.source_url||coalesce(o.payload->>'primary_image','')||coalesce(p.payload::text,'')||coalesce(p.observed_at::text,''),'' ORDER BY o.id)) fingerprint FROM supplier_offers o LEFT JOIN product_observations p ON p.offer_id=o.id"
                            )
                        )
                    )
                    .mappings()
                    .one()
                )
                return dict(row)

        async def call(method, path, **kwargs):
            r = await c.request(method, path, **kwargs)
            r.raise_for_status()
            return r

        async def submit(query, label):
            start = time.perf_counter()
            r = await call(
                "POST", f"/api/projects/{project}/requests", json={"query": query}
            )
            value = r.json()["research"]
            evidence[label] = {
                "seconds": round(time.perf_counter() - start, 3),
                "bytes": len(r.content),
                "base_total": value["base_total"],
                "filtered_total": value["filtered_total"],
                "branches": value["intent"]["branches"],
                "timings": value["timings"],
                "sql_queries": r.headers.get("X-SQL-Queries"),
                "vto_requests": r.headers.get("X-VTO-Requests"),
            }
            return value

        try:
            user = (
                await call(
                    "POST",
                    "/api/auth/register",
                    json={
                        "name": "purpose-check-" + uuid4().hex,
                        "password": secrets.token_urlsafe(24),
                    },
                )
            ).json()["id"]
            await call(
                "POST", "/api/chats", json={"project_name": "Purpose acceptance"}
            )
            project = (await call("GET", "/api/projects")).json()[0]["id"]
            before = await commercial_fingerprint()
            if args.old_base:
                old = (
                    await call(
                        "POST",
                        args.old_base + f"/api/projects/{project}/requests",
                        json={"query": "ручки бутылки"},
                    )
                ).json()["research"]
                # Preserve a real previously admitted bad selection across the
                # derived-state version upgrade, without rewriting its messages.
                async with sessions() as db:
                    snapshot = await db.get(ResearchRecord, old["id"])
                    offers = (
                        await db.scalars(
                            select(SupplierOffer).where(
                                SupplierOffer.id.in_(snapshot.payload["pool_offer_ids"])
                            )
                        )
                    ).all()
                    bad = next(
                        o
                        for o in offers
                        if not any(
                            purpose_match(
                                identity_inputs(
                                    o.payload["name"],
                                    o.payload["metadata"].get("supplier_category"),
                                    o.payload["metadata"].get("breadcrumbs"),
                                    o.payload["metadata"].get("attributes"),
                                    o.payload.get("description"),
                                    o.payload.get("brand"),
                                ),
                                ProductBranch.model_validate(b),
                            )["accepted"]
                            for b in old["intent"]["branches"]
                        )
                    )
                old = (
                    await call(
                        "PATCH",
                        args.old_base + f"/api/researches/{old['id']}/view",
                        json={
                            "version": old["view"]["version"],
                            "selected_ids": [bad.id],
                        },
                    )
                ).json()
                upgraded = (await call("GET", f"/api/researches/{old['id']}")).json()
                assert upgraded["messages"] == old["messages"] and upgraded["view"][
                    "selected_ids"
                ] == [bad.id]
                assert (
                    upgraded["hidden_selected_count"] == 1
                    and upgraded["selected_products"][0]["category_match"] == "REJECTED"
                )
                evidence["upgrade"] = {
                    "before": old["base_total"],
                    "after": upgraded["base_total"],
                    "selected_preserved": bad.id,
                    "history_preserved": True,
                }
            r = await submit("ручки бутылки", "initial")
            async with sessions() as db:
                record = await db.get(ResearchRecord, r["id"])
                ids = record.payload["pool_offer_ids"]
                rows = (
                    await db.scalars(
                        select(SupplierOffer).where(SupplierOffer.id.in_(ids))
                    )
                ).all()
                samples = []
                for o in rows:
                    if re.search(
                        r"набор|комплект|футляр|коробк|блокнот|ежедневник|зонт|чехол|крышк",
                        o.payload["name"],
                        re.IGNORECASE,
                    ):
                        samples.append(
                            {
                                "id": o.id,
                                "parent": o.payload["metadata"].get("vto_product_id"),
                                "name": o.payload["name"],
                                "category": o.payload.get("category"),
                                "supplier_category": o.payload["metadata"].get(
                                    "supplier_category"
                                ),
                                "source_categories": o.payload["metadata"].get(
                                    "vto_categories"
                                ),
                                "branch_matches": [
                                    b["category"]
                                    for b, pool in zip(
                                        record.payload["intent"]["branches"],
                                        record.payload["branch_offer_ids"],
                                    )
                                    if o.id in pool
                                ],
                            }
                        )
                evidence["suspects"] = samples[:30]
                evidence["all_offer_ids"] = ids
            red = await submit("красные", "red")
            if args.baseline:
                return
            assert [b["category"] for b in r["intent"]["branches"]] == ["pen", "bottle"]
            assert (
                red["id"] == r["id"]
                and red["intent"]["categories"] == r["intent"]["categories"]
            )
            assert all(
                any(x["normalized_color"] == "RED" for x in p["colors"])
                for p in red["products"]
            )
            assert all(
                p.get("purpose_match", {}).get("accepted") for p in red["products"]
            )
            evidence["checks"] = {"red_keeps_subjects": True}
            r = (
                await call(
                    "PATCH",
                    f"/api/researches/{r['id']}/view",
                    json={"version": red["view"]["version"], "restore": "undo"},
                )
            ).json()
            assert r["filtered_total"] == r["base_total"]
            first = r["products"][0]["id"]
            cursor = r["next_cursor"]
            r = (
                await call(
                    "PATCH",
                    f"/api/researches/{r['id']}/view",
                    json={"version": r["view"]["version"], "selected_ids": [first]},
                )
            ).json()
            selected = [first]
            if cursor:
                page = (
                    await call(
                        "GET",
                        f"/api/researches/{r['id']}/products",
                        params={"cursor": cursor},
                    )
                ).json()
                assert not {p["id"] for p in page["products"]} & {
                    p["id"] for p in r["products"]
                }
                selected.append(page["products"][0]["id"])
                r = (
                    await call(
                        "PATCH",
                        f"/api/researches/{r['id']}/view",
                        json={
                            "version": r["view"]["version"],
                            "selected_ids": selected,
                        },
                    )
                ).json()
            facet = next(b for b in r["facets"]["brand"] if b["value"] == "molti")
            started = time.perf_counter()
            reply = await call(
                "PATCH",
                f"/api/researches/{r['id']}/view",
                json={
                    "version": r["view"]["version"],
                    "filters": r["view"]["filters"] | {"brands": ["molti"]},
                },
            )
            r = reply.json()
            assert r["filtered_total"] == facet["count"]
            assert all(
                (p["brand"] or "").strip().casefold() == "molti" for p in r["products"]
            )
            assert (
                r["view"]["selected_ids"] == selected
                and r["intent"]["raw_query"] == "ручки бутылки"
            )
            evidence["brand"] = {
                "total": r["filtered_total"],
                "seconds": round(time.perf_counter() - started, 3),
                "hidden_selected": r["hidden_selected_count"],
                "vto_requests": reply.headers.get("X-VTO-Requests"),
            }
            restored = (
                await call("GET", f"/api/researches/chat/{r['chat_id']}")
            ).json()
            assert restored["view"] == r["view"] and set(
                restored["view"]["selected_ids"]
            ) == set(selected)
            r = (
                await call(
                    "PATCH",
                    f"/api/researches/{r['id']}/view",
                    json={"version": r["view"]["version"], "restore": "reset"},
                )
            ).json()
            assert r["filtered_total"] == r["base_total"]
            for label, query, scope in [
                ("case", "футляры для ручек", "PACKAGING"),
                ("notebook_pen", "блокнот с ручкой", "ASSOCIATED"),
                ("set", "подарочный набор с ручкой и повербанком", "SET"),
                ("expanded", "ручки, включая наборы", "STANDALONE"),
            ]:
                value = await submit(query, label)
                assert value["intent"]["branches"][0]["scope"] == scope
                assert value["base_total"] > 0
                assert all(p["purpose_match"]["accepted"] for p in value["products"])
            constrained = await submit("чёрные ручки от 300 шт.", "source_constraints")
            assert constrained["base_total"] > 0
            original_intent = constrained["intent"]
            assert original_intent["branches"][0]["colors"] == ["BLACK"]
            assert original_intent["branches"][0]["quantity"] == 300
            filtered = (
                await call(
                    "PATCH",
                    f"/api/researches/{constrained['id']}/view",
                    json={
                        "version": constrained["view"]["version"],
                        "filters": constrained["view"]["filters"]
                        | {"brands": ["molti"]},
                    },
                )
            ).json()
            reset = (
                await call(
                    "PATCH",
                    f"/api/researches/{constrained['id']}/view",
                    json={
                        "version": filtered["view"]["version"],
                        "restore": "reset",
                    },
                )
            ).json()
            assert (
                reset["intent"] == original_intent
                and reset["filtered_total"] == constrained["base_total"]
            )
            recolored = await submit("красные", "replace_source_color")
            assert recolored["id"] == constrained["id"]
            assert recolored["intent"]["branches"][0]["colors"] == ["RED"]
            assert recolored["intent"]["branches"][0]["quantity"] == 300
            assert not recolored["view"]["filters"]["colors"]
            evidence["checks"]["reset_preserves_source_constraints"] = True
            evidence["checks"]["chat_replaces_source_color"] = True
            old_path = ROOT / ".local/purpose-before.json"
            if old_path.exists():
                previous = json.loads(old_path.read_text("utf-8"))
                excluded = set(previous["all_offer_ids"]) - set(ids)
                async with sessions() as db:
                    removed = (
                        await db.scalars(
                            select(SupplierOffer).where(SupplierOffer.id.in_(excluded))
                        )
                    ).all()
                seen = set()
                examples = []
                for o in removed:
                    identity = identity_inputs(
                        o.payload["name"],
                        o.payload["metadata"].get("supplier_category"),
                        o.payload["metadata"].get("breadcrumbs"),
                        o.payload["metadata"].get("attributes"),
                        o.payload.get("description"),
                        o.payload.get("brand"),
                    )
                    key = (
                        identity.role
                        if identity.role != "PRODUCT"
                        else ",".join(identity.primary)
                    )
                    if key not in seen:
                        seen.add(key)
                        examples.append(
                            {
                                "id": o.id,
                                "parent": o.payload["metadata"].get("vto_product_id"),
                                "name": o.payload["name"],
                                "category": o.payload["metadata"].get(
                                    "supplier_category"
                                ),
                                "identity": identity.public(),
                            }
                        )
                evidence["removed_count"] = len(excluded)
                evidence["removed_examples"] = examples
            async with httpx.AsyncClient(base_url=args.base, timeout=180) as other:
                uid = pid = None
                try:
                    uid = (
                        await other.post(
                            "/api/auth/register",
                            json={
                                "name": "purpose-other-" + uuid4().hex,
                                "password": secrets.token_urlsafe(24),
                            },
                        )
                    ).json()["id"]
                    await other.post(
                        "/api/chats", json={"project_name": "Purpose isolation"}
                    )
                    pid = (await other.get("/api/projects")).json()[0]["id"]
                    value = (
                        await other.post(
                            f"/api/projects/{pid}/requests", json={"query": "бутылки"}
                        )
                    ).json()
                    assert value["research"]["id"] != r["id"]
                    assert (
                        await other.get(f"/api/researches/{r['id']}")
                    ).status_code == 404
                    evidence["checks"].update(
                        {
                            "selections_across_pages": len(selected) > 1,
                            "f5_state": True,
                            "brand_reset": True,
                            "two_users_isolated": True,
                        }
                    )
                finally:
                    if pid:
                        (await other.delete(f"/api/projects/{pid}")).raise_for_status()
                    if uid:
                        async with sessions.begin() as db:
                            await db.execute(delete(User).where(User.id == uid))
            after = await commercial_fingerprint()
            evidence["commercial"] = {
                "before": before,
                "after": after,
                "unchanged": before == after,
            }
            assert before == after, (
                "Commercial observations changed during acceptance; inspect background activity"
            )
        finally:
            if project:
                await call("DELETE", f"/api/projects/{project}")
            if user:
                async with sessions.begin() as db:
                    await db.execute(delete(User).where(User.id == user))
            await engine.dispose()
            path = (
                ROOT
                / ".local"
                / ("purpose-before.json" if args.baseline else "purpose-after.json")
            )
            path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), "utf-8")
            print(
                json.dumps(
                    {
                        k: v
                        for k, v in evidence.items()
                        if k not in ("suspects", "all_offer_ids")
                    },
                    ensure_ascii=True,
                )
            )
            print("saved", path.name, "suspects", len(evidence.get("suspects", [])))


if __name__ == "__main__":
    asyncio.run(main())
