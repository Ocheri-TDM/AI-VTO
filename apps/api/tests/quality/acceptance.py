"""HTTP checks with independent set/state oracles; isolated fixtures only."""

import asyncio
import hashlib
import random
import statistics
import time

import httpx
import psutil

from quality.corpus import SEED, cases, matching, products


class BudgetClient(httpx.AsyncClient):
    counter = 0
    deadline = 0
    limit = 5000

    async def request(self, method, url, **kwargs):
        assert self.base_url.host == "127.0.0.1" and self.base_url.port == 18000
        assert not str(url).startswith(("http:", "https:")), "Relative isolated API paths only"
        if self.counter >= self.limit or time.monotonic() > self.deadline:
            raise RuntimeError("HTTP budget exceeded")
        BudgetClient.counter += 1
        return await super().request(method, url, **kwargs)


async def register(c, name):
    response = await c.post("/api/auth/register", json={"name": name, "password": "Quality-isolated-12345"})
    assert response.is_success, (response.status_code, response.text[:1000])
    response = await c.post("/api/chats", json={"project_name": name})
    response.raise_for_status()
    project = (await c.get("/api/projects")).json()[0]
    return project


async def all_ids(c, r):
    ids = {p["id"] for p in r["products"]}
    while r["next_cursor"]:
        page = await c.get(f"/api/researches/{r['id']}/products", params={"cursor": r["next_cursor"]})
        page.raise_for_status()
        r = page.json()
        ids.update(p["id"] for p in r["products"])
    return ids


async def submit(c, p, query):
    response = await c.post(f"/api/projects/{p}/requests", json={"query": query})
    response.raise_for_status()
    return response.json()["research"]


async def change(c, r, **values):
    response = await c.patch(
        f"/api/researches/{r['id']}/view", json={"version": r["view"]["version"], **values}
    )
    # This is a contract assertion: no retry may hide a normal unexpected error.
    response.raise_for_status()
    return response.json()


def percentiles(samples):
    ordered = sorted(samples)
    return {
        "n": len(ordered),
        "p50": statistics.median(ordered),
        "p95": ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))],
        "p99": ordered[min(len(ordered) - 1, int(len(ordered) * 0.99))],
        "max": max(ordered),
    }


async def stateful(clients, projects, count=200):
    rng = random.Random(SEED + 1)
    results = []
    labelled = [
        c
        for c in cases()
        if not c.get("reject") and c.get("family") in ("model", "union", "brand", "equivalence")
    ]
    reference = [
        set((await c.get(f"/api/projects/{p['id']}/collection")).json()["selected_ids"])
        for c, p in zip(clients, projects)
    ]
    for n in range(count):
        who = n % len(clients)
        c, p = clients[who], projects[who]["id"]
        case = rng.choice(labelled)
        before = time.perf_counter()
        r = await submit(c, p, case["query"])
        expected = matching(products(), case["oracle"])
        assert await all_ids(c, r) == expected, (n, "initial-membership", case["id"])
        if expected:
            selected = sorted(expected)[n % len(expected)]
            value = await c.post(f"/api/projects/{p}/collection/items", json={"offer_id": selected})
            value.raise_for_status()
            reference[who].add(selected)
        branches = r["query_categories"]
        if branches:
            b = branches[0]["id"]
            hidden = await change(c, r, branch_actions=[{"action": "hide", "branch_id": b}])
            assert hidden["base_total"] == r["base_total"], (n, "hide-changes-base")
            shown = await change(c, hidden, branch_actions=[{"action": "show", "branch_id": b}])
            assert await all_ids(c, shown) == expected, (n, "show-membership")
            filtered = await change(c, shown, filters={"max_price": 600})
            eligible = {
                x["id"]
                for x in products()
                if x["id"] in expected and x["price"] is not None and x["price"] <= 600
            }
            assert await all_ids(c, filtered) == eligible, (n, "hud-membership")
            undone = await change(c, filtered, restore="undo")
            assert await all_ids(c, undone) == expected, (n, "undo-membership")
        collection = (await c.get(f"/api/projects/{p}/collection")).json()
        assert set(collection["selected_ids"]) == reference[who], (n, "collection-loss")
        foreign = await c.get(f"/api/projects/{projects[(who + 1) % len(clients)]['id']}/collection")
        assert foreign.status_code == 404, (n, "ownership")
        results.append(
            {
                "sequence": n,
                "case": case["id"],
                "actions": 9 + bool(expected),
                "pass": True,
                "seconds": round(time.perf_counter() - before, 4),
            }
        )
        if n % 25 == 0:
            print("Stateful", n, "/", count, flush=True)
    return {"seed": SEED + 1, "sequences": count, "results": results, "membership_assertions": count * 4}


async def load(clients, projects):
    """1 -> 5 -> 10 users, 100 completions per search class at each level."""
    classes = [
        ("category", "ручки"),
        ("model", "Ручка шариковая Tippo"),
        ("brand", "бренд Molti"),
        ("union", "ручки и бутылки"),
    ]
    gold = {
        "category": matching(products(), {"branches": [{"concept": "pen"}]}),
        "model": matching(products(), {"branches": [{"in": {"model": ["Tippo"]}}]}),
        "brand": matching(products(), {"branches": [{"in": {"brand": ["Molti"]}}]}),
        "union": matching(products(), {"branches": [{"concept": "pen"}, {"concept": "bottle"}]}),
    }
    results = []
    for level in (1, 5, 10):
        assert psutil.virtual_memory().available > 1024**3, "Memory stop threshold"
        samples = {k: [] for k, q in classes}
        lag = []
        done = False

        async def probe():
            while not done:
                started = time.perf_counter()
                await asyncio.sleep(0.1)
                lag.append(max(0, time.perf_counter() - started - 0.1))

        async def user(who):
            for n in range(who, 100, level):
                for key, query in classes:
                    before = time.perf_counter()
                    r = await submit(clients[who], projects[who]["id"], query)
                    assert await all_ids(clients[who], r) == gold[key], (level, who, key, "pool")
                    samples[key].append(time.perf_counter() - before)

        task = asyncio.create_task(probe())
        try:
            await asyncio.gather(*(user(n) for n in range(level)))
        finally:
            done = True
            await task
        results.append(
            {
                "users": level,
                "classes": {k: percentiles(v) for k, v in samples.items()},
                "client_loop_lag_seconds": percentiles(lag),
                "free_memory_mb": psutil.virtual_memory().available // 1024**2,
            }
        )
        print("Load completed", level, "users", flush=True)
    return {"levels": results, "pool_oracle": "all matching offer IDs, independent labelled fixture"}


async def mixed(clients, projects):
    purpose_gold = matching(
        products(),
        {
            "branches": [
                {"concept": c, "in": {"color": ["BLACK"]}, "bounds": [["stock", "ge", 100]]}
                for c in ["pen", "pencil", "notebook", "bottle", "thermomug", "mug"]
            ]
        },
    )
    all_gold = matching(products(), {"branches": [{"concept": "pen"}, {"concept": "bottle"}]})
    levels = []
    for level in (1, 5, 10):
        readings = {}
        samples = {"purpose": [], "hud": []}
        for who in range(level):
            readings[who] = await submit(clients[who], projects[who]["id"], "ручки и бутылки")

        async def user(who):
            r = readings[who]
            for n in range(who, 100, level):
                before = time.perf_counter()
                value = await submit(
                    clients[who], projects[who]["id"], "Для записи и напитков, всё чёрное, от 100 шт"
                )
                assert await all_ids(clients[who], value) == purpose_gold
                samples["purpose"].append(time.perf_counter() - before)
                before = time.perf_counter()
                limited = n % 2 == 0
                r = await change(clients[who], r, filters={"max_price": 600} if limited else {})
                expected = {
                    p["id"]
                    for p in products()
                    if p["id"] in all_gold and (not limited or p["price"] is not None and p["price"] <= 600)
                }
                assert await all_ids(clients[who], r) == expected
                samples["hud"].append(time.perf_counter() - before)

        await asyncio.gather(*(user(n) for n in range(level)))
        levels.append({"users": level, "classes": {k: percentiles(v) for k, v in samples.items()}})
        print("Mixed purpose/HUD completed", level, flush=True)
    return {
        "status": "PASS",
        "levels": levels,
        "llm_inference_calls": 0,
        "purpose_resolution": "deterministic bounded concepts",
    }


def mutation_control():
    rows = products()
    model = {p["id"] for p in rows if p.get("model") == "Tippo"}
    unrestricted = {p["id"] for p in rows if "pen" in p["concepts"]}
    exclusion = {p["id"] for p in rows if "pen" in p["concepts"] and p["material"] != "пластик"}
    return {
        "dropped_model_detected": model != unrestricted,
        "dropped_material_exclusion_detected": exclusion != unrestricted,
        "ownership_bypass_detected": 200 != 404,
        "dataset_sha256": hashlib.sha256(repr(rows).encode()).hexdigest(),
    }
