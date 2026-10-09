"""Live API/PostgreSQL acceptance for typed Composite intent mutations."""

import json
import time
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

BASE = "http://127.0.0.1:8000/api"
client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))


def call(method, path, body=None):
    started = time.perf_counter()
    request = urllib.request.Request(
        BASE + path,
        data=json.dumps(body, ensure_ascii=False).encode() if body is not None else None,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with client.open(request, timeout=300) as response:
        value = json.loads(response.read())
        return response.status, value, round((time.perf_counter() - started) * 1000, 2)


stamp = int(time.time())
call("POST", "/auth/register", {"name": f"mutation-{stamp}", "password": "Mutation-2026"})
_, chat, _ = call("POST", "/chats", {"project_name": f"Mutation {stamp}"})
_, projects, _ = call("GET", "/projects")
project = next(item for item in projects if item["chat_id"] == chat["id"])

messages = [
    "собери 50 наборов из зонтов, ручек, бутылок и футболок",
    "футболки сделай красными",
    "ручки сделай синими",
    "теперь от 20 штук",
    "замени бутылки на кружки",
    "добавь черные рюкзаки",
    "сделай 20 наборов",
    "количество не важно",
]
steps = []
for message in messages:
    status, response, elapsed = call(
        "POST", f"/projects/{project['id']}/requests", {"query": message}
    )
    hard = response["hard_request"]
    plan = hard["plan"]
    steps.append({
        "message": message,
        "http_status": status,
        "route": response["route"]["action"],
        "request_id": hard["id"],
        "revision": hard["revision_number"],
        "target_cases": plan["target_sets"],
        "minimum_quantity": plan["quantity"]["minimum"],
        "positions": [
            {
                "category": position["category_text"],
                "color": (position["constraints"].get("color") or {}).get("values", []),
            }
            for position in plan["positions"]
        ],
        "case_count": hard["completed_cases"],
        "status": hard["status"],
        "latency_ms": elapsed,
        "llm_used": plan.get("query_understanding", {}).get("llm_used", False),
    })

assert all(step["http_status"] < 500 for step in steps)
assert steps[0]["target_cases"] == 50 and steps[0]["minimum_quantity"] is None
assert steps[3]["minimum_quantity"] == 20
assert steps[6]["target_cases"] == 20 and steps[6]["minimum_quantity"] == 20
assert steps[7]["target_cases"] == 20 and steps[7]["minimum_quantity"] is None
assert len({step["request_id"] for step in steps}) == len(steps)
assert all(not step["llm_used"] for step in steps)

result = {"project_id": project["id"], "steps": steps}
Path(".local/composite-mutation-live.json").write_text(
    json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
)
print(json.dumps(result, ensure_ascii=False, indent=2))
