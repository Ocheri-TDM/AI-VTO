"""Real concurrent HTTP acceptance for Composite project mutation locks."""

import concurrent.futures
import json
import time
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

BASE = "http://127.0.0.1:8000/api"
jar = CookieJar()
client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def request(method, path, body=None, cookie=None):
    started = time.perf_counter()
    headers = {"Content-Type": "application/json"}
    if cookie:
        headers["Cookie"] = cookie
    value = urllib.request.Request(
        BASE + path,
        data=json.dumps(body, ensure_ascii=False).encode() if body is not None else None,
        method=method,
        headers=headers,
    )
    with urllib.request.urlopen(value, timeout=600) if cookie else client.open(value, timeout=600) as response:
        return response.status, json.loads(response.read()), round((time.perf_counter() - started) * 1000, 2)


stamp = int(time.time())
request("POST", "/auth/register", {"name": f"concurrency-{stamp}", "password": "Concurrency-2026"})
cookie = "; ".join(f"{item.name}={item.value}" for item in jar)


def project(name):
    _, chat, _ = request("POST", "/chats", {"project_name": name}, cookie=cookie)
    _, projects, _ = request("GET", "/projects", cookie=cookie)
    project_row = next(item for item in projects if item["chat_id"] == chat["id"])
    status, result, _ = request("POST", f"/projects/{project_row['id']}/requests", {
        "query": "собери 5 наборов из зонтов, ручек, бутылок и футболок"
    }, cookie)
    assert status == 202 and result["mode"] == "COMPOSITE"
    return project_row["id"]


def parallel(project_id, messages):
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(messages)) as executor:
        futures = [executor.submit(
            request, "POST", f"/projects/{project_id}/requests", {"query": message}, cookie
        ) for message in messages]
        responses = [future.result() for future in futures]
    _, active, _ = request("GET", f"/projects/{project_id}/active-composite", cookie=cookie)
    _, history, _ = request("GET", f"/projects/{project_id}/queries", cookie=cookie)
    _, messages_rows, _ = request("GET", f"/projects/{project_id}/messages", cookie=cookie)
    return responses, active, history, messages_rows


def semantic(active):
    plan = active["plan"]
    values = {}
    for position in plan["positions"]:
        category = position["category_text"]
        color = (position["constraints"].get("color") or {}).get("values", [])
        values[category] = color
    return {"quantity": plan["quantity"]["minimum"], "positions": values}


results = {}

pid = project(f"nonconflicting-{stamp}")
responses, active, history, rows = parallel(pid, [
    "футболки сделай красными", "ручки сделай синими"
])
state = semantic(active)
assert any("RED" in colors for name, colors in state["positions"].items() if "футбол" in name)
assert any("BLUE" in colors for name, colors in state["positions"].items() if "руч" in name)
results["nonconflicting"] = {"responses": [r[0] for r in responses], "state": state,
    "active_count": sum(1 for item in history if item["is_active"]), "messages": len(rows),
    "latency_ms": [r[2] for r in responses]}

pid = project(f"conflicting-{stamp}")
responses, active, history, rows = parallel(pid, [
    "футболки сделай красными", "футболки сделай синими"
])
state = semantic(active)
tshirt = next(colors for name, colors in state["positions"].items() if "футбол" in name)
assert tshirt == ["RED"] or "BLUE" in tshirt
results["conflicting"] = {"responses": [r[0] for r in responses], "final_color": tshirt,
    "active_count": sum(1 for item in history if item["is_active"]), "messages": len(rows),
    "policy": "PostgreSQL advisory lock acquisition order; last committed mutation wins"}

pid = project(f"membership-{stamp}")
responses, active, history, rows = parallel(pid, ["добавь рюкзаки", "убери бутылки"])
names = " ".join(active["plan"]["categories"])
assert "backpack" in names and "бутыл" not in names
results["add_remove"] = {"responses": [r[0] for r in responses], "categories": active["plan"]["categories"],
    "active_count": sum(1 for item in history if item["is_active"])}

pid = project(f"quantity-color-{stamp}")
responses, active, history, rows = parallel(pid, ["теперь от 250 штук", "футболки красные"])
state = semantic(active)
assert state["quantity"] == 250
assert any("RED" in colors for name, colors in state["positions"].items() if "футбол" in name)
results["quantity_color"] = {"responses": [r[0] for r in responses], "state": state,
    "active_count": sum(1 for item in history if item["is_active"])}

assert all(value["active_count"] == 1 for value in results.values())
Path(".local/composite-concurrency.json").write_text(
    json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
)
print(json.dumps(results, ensure_ascii=False, indent=2))
