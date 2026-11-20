"""Real local HTTP, independently verified source rows and persisted view state."""

import json

import httpx
from app.config import Settings
from app.database.connection import create_database
from app.database.models import OfferAttributeValue, SupplierOffer
from app.domain.research import ResearchSession
from check_chat_workflow import call, save
from sqlalchemy import any_, bindparam, func, select, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.types import String


async def run(state, root, ledger, public):
    evidence = {"checks": [], "witnesses": []}

    def check(name, condition, **details):
        evidence["checks"].append(
            {"check": name, "status": "PASS" if condition else "FAIL", **details}
        )
        save(public / "http.json", evidence)
        print(("PASS " if condition else "FAIL ") + name, flush=True)
        assert condition, name

    controls = json.loads((public / "after.json").read_text("utf-8"))
    sample = next(c for c in controls["cases"] if not c["contextual"])
    engine, sessions = create_database(Settings().database_url)
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:8000",
        cookies=state["users"][0]["cookies"],
        timeout=180,
    ) as client:
        path = "/api/researches/" + sample["research_id"]
        current = await call(client, "GET", path)
        initial = {b["concept"]: b["count"] for b in current["query_categories"]}
        branches = {b["concept"]: b["id"] for b in current["query_categories"]}

        async def change(**changes):
            nonlocal current
            current = await call(
                client,
                "PATCH",
                path + "/view",
                json={"version": current["view"]["version"], **changes},
            )
            return current

        def branch_counts():
            return {b["concept"]: b["count"] for b in current["query_categories"]}

        def values(category, attribute):
            return next(
                a
                for g in current["category_facets"]
                if g["category"] == category
                for a in g["attributes"]
                if a["id"] == attribute
            )["values"]

        try:
            # One aggregate per scope verifies every returned value. The witness
            # offer and its retained raw evidence are real catalog data.
            async with sessions() as db:
                await db.execute(
                    text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                )
                payload = await db.scalar(
                    text("SELECT payload FROM research_sessions WHERE id=:id"),
                    {"id": current["id"]},
                )
                research = ResearchSession.model_validate(payload)
                for group in current["category_facets"]:
                    pos = next(
                        i
                        for i, b in enumerate(research.intent.branches)
                        if b.id == group["branch_id"]
                    )
                    ids = research.branch_offer_ids[pos]
                    predicate = OfferAttributeValue.offer_id == any_(
                        bindparam(None, value=ids, type_=ARRAY(String))
                    )
                    rows = (
                        await db.execute(
                            select(
                                OfferAttributeValue.attribute_id,
                                OfferAttributeValue.value_key,
                                func.count(func.distinct(OfferAttributeValue.offer_id)),
                                func.min(OfferAttributeValue.offer_id),
                            )
                            .where(predicate)
                            .group_by(
                                OfferAttributeValue.attribute_id,
                                OfferAttributeValue.value_key,
                            )
                        )
                    ).all()
                    found = {(a, key): (count, offer) for a, key, count, offer in rows}
                    for attribute in group["attributes"]:
                        for option in attribute["values"]:
                            if not option["count"]:
                                continue
                            count, offer = found[attribute["id"], option["value"]]
                            assert count == option["count"]
                            value = await db.get(
                                OfferAttributeValue,
                                (offer, attribute["id"], option["value"]),
                            )
                            name = await db.scalar(
                                select(SupplierOffer.payload["name"].as_string()).where(
                                    SupplierOffer.id == offer
                                )
                            )
                            evidence["witnesses"].append(
                                {
                                    "category": group["category"],
                                    "attribute": attribute["id"],
                                    "value": option["value"],
                                    "count": count,
                                    "offer_id": offer,
                                    "name": name,
                                    "provenance": value.evidence,
                                }
                            )
            check(
                "every nonzero typed facet value has exact SQL count and real scoped offer evidence",
                bool(evidence["witnesses"]),
                values=len(evidence["witnesses"]),
            )
            check(
                "untyped size values and global size replacement list are absent",
                not current["facets"]["size"]
                and not current["effective_filters"]["options"]["sizes"],
            )
            project = next(
                p
                for p in await call(client, "GET", "/api/projects")
                if p["chat_id"] == current["chat_id"]
            )["id"]
            chosen = current["products"][0]["id"]
            await call(
                client,
                "POST",
                f"/api/projects/{project}/collection/items",
                json={"offer_id": chosen},
            )
            f = current["view"]["filters"]
            f["branch_attributes"] = {branches["bottle"]: {"capacity_ml": ["500"]}}
            await change(filters=f)
            check(
                "bottle 500ml retains both neighboring categories",
                branch_counts()["mug"] == initial["mug"]
                and branch_counts()["umbrella"] == initial["umbrella"]
                and branch_counts()["bottle"] > 0,
            )
            check(
                "self exclusion retains alternative bottle capacities",
                any(
                    v["value"] == "750" and v["count"] > 0
                    for v in values("bottle", "capacity_ml")
                ),
            )
            f["branch_attributes"][branches["mug"]] = {"capacity_ml": ["350"]}
            await change(filters=f)
            check(
                "mug 350ml independently narrows mugs and leaves umbrellas",
                branch_counts()["mug"] > 0
                and branch_counts()["mug"] < initial["mug"]
                and branch_counts()["umbrella"] == initial["umbrella"],
            )
            f["branch_attributes"][branches["bottle"]] = {"capacity_ml": ["999999"]}
            await change(filters=f)
            check(
                "zero bottle condition retains mugs, umbrellas and removable active zero",
                branch_counts()["bottle"] == 0
                and branch_counts()["mug"] > 0
                and branch_counts()["umbrella"] == initial["umbrella"]
                and any(
                    v["value"] == "999999"
                    and v["count"] == 0
                    and v["selected"]
                    and not v["disabled"]
                    for v in values("bottle", "capacity_ml")
                ),
            )
            await change(
                branch_actions=[{"action": "hide", "branch_id": branches["bottle"]}]
            )
            check(
                "hidden branch contributes no facet group but keeps its local filter",
                not any(g["category"] == "bottle" for g in current["category_facets"])
                and current["view"]["filters"]["branch_attributes"][branches["bottle"]][
                    "capacity_ml"
                ]
                == ["999999"],
            )
            await change(
                branch_actions=[{"action": "show", "branch_id": branches["bottle"]}]
            )
            check(
                "restoring visibility restores the same local condition",
                any(v["value"] == "999999" for v in values("bottle", "capacity_ml")),
            )
            await change(
                branch_actions=[{"action": "remove", "branch_id": branches["bottle"]}]
            )
            removal = current["category_notice"]["id"]
            check(
                "removed branch is absent from intent and contextual facets",
                "bottle" not in current["intent"]["categories"]
                and not any(
                    g["category"] == "bottle" for g in current["category_facets"]
                ),
            )
            await change(branch_actions=[{"action": "restore", "removal_id": removal}])
            check(
                "branch restore preserves stable scope and local filter",
                branches["bottle"] in current["view"]["filters"]["branch_attributes"],
            )
            f["branch_attributes"][branches["bottle"]] = {"capacity_ml": ["500"]}
            f["colors"], f["minimum_quantity"] = ["RED"], 100
            await change(filters=f)
            valid_ids = [p["id"] for p in current["products"]]
            async with sessions() as db:
                await db.execute(text("SET TRANSACTION READ ONLY"))
                rows = (
                    await db.execute(
                        text(
                            "SELECT o.id,p.stock,o.normalized_colors FROM supplier_offers o JOIN product_observations p ON p.offer_id=o.id WHERE o.id=ANY(:ids)"
                        ),
                        {"ids": valid_ids},
                    )
                ).all()
                check(
                    "global red and quantity apply alongside local category attributes",
                    bool(rows)
                    and all(
                        stock is not None and stock >= 100 and "RED" in colors
                        for _, stock, colors in rows
                    ),
                )
                saved = await db.scalar(
                    text("SELECT payload FROM research_sessions WHERE id=:id"),
                    {"id": current["id"]},
                )
                check(
                    "persisted revision and filters match HTTP effective state",
                    saved["view"] == current["view"],
                )
            read = await call(client, "GET", path)
            check(
                "F5 read retains source requirements and branch filters",
                read["view"] == current["view"] and read["intent"] == current["intent"],
            )
            collection = await call(
                client, "GET", f"/api/projects/{project}/collection"
            )
            check(
                "project collection remains selected through filtering",
                chosen in collection["selected_ids"],
            )
            before = current["category_facets"]
            await change(sorting="price_desc")
            check(
                "sorting does not alter contextual facet values or counts",
                before == current["category_facets"],
            )
            for invalid in [
                {branches["bottle"]: {"paper_format": ["A5"]}},
                {branches["bottle"]: {"stock": ["500"]}},
                {branches["bottle"]: {"capacity_ml": ["not-a-number"]}},
                {branches["bottle"]: {"capacity_ml": ["1e-999999999"]}},
            ]:
                bad = {**current["view"]["filters"], "branch_attributes": invalid}
                response = await client.patch(
                    path + "/view",
                    json={"version": current["view"]["version"], "filters": bad},
                )
                check(
                    "invalid attribute/type/scope is rejected without SQL field access",
                    response.status_code == 422,
                )
            await change(restore="reset")
            check(
                "reset restores source scope and retains project selection",
                not current["view"]["filters"]["branch_attributes"]
                and current["intent"]["categories"] == ["bottle", "mug", "umbrella"]
                and chosen
                in (await call(client, "GET", f"/api/projects/{project}/collection"))[
                    "selected_ids"
                ],
            )
            # Preserve a representative exact-model case and one source-hard case
            # for the browser; standalone new requests start with empty extras.
            for query in [
                "Рюкзаки Melango",
                "Бутылки, кружки, зонты. Все красные, от 100 шт.",
            ]:
                value = await call(
                    client,
                    "POST",
                    "/api/researches",
                    json={"chat_id": current["chat_id"], "query": query},
                )
                state.setdefault("acceptance_cases", []).append(
                    {"query": query, "id": value["id"], "chat_id": value["chat_id"]}
                )
                check(
                    "standalone request has no inherited category attributes",
                    not value["view"]["filters"]["branch_attributes"],
                )
                if "Melango" in query:
                    check(
                        "exact model constrains facets and actual cards",
                        bool(value["products"])
                        and all(
                            "melango" in p["name"].casefold() for p in value["products"]
                        )
                        and len(value["category_facets"]) <= 1,
                    )
                else:
                    check(
                        "global original red and quantity stay in effective controls",
                        value["effective_filters"]["fields"]["colors"]["values"]
                        == ["RED"]
                        and value["effective_filters"]["fields"]["minimum_quantity"][
                            "number"
                        ]
                        == 100,
                    )
            state["ui_project"], state["ui_chat"] = project, current["chat_id"]
            save(ledger, state)
            save(public / "http.json", evidence)
        finally:
            await engine.dispose()
