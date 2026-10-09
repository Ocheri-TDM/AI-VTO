"""Live PostgreSQL/API acceptance for contextual refinement."""

import json
import time
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

BASE = "http://127.0.0.1:8000/api"
client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))


def call(method, path, body=None):
    request = urllib.request.Request(
        BASE + path,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with client.open(request, timeout=240) as response:
        return json.loads(response.read())


stamp = int(time.time())
call("POST", "/auth/register", {"name": f"router-live-{stamp}", "password": "Router-live-2026"})
chat = call("POST", "/chats", {"project_name": f"Router live {stamp}"})
project = next(item for item in call("GET", "/projects") if item["chat_id"] == chat["id"])
base = call("POST", f"/projects/{project['id']}/requests", {"query": "карандаш"})
base_queries = call("GET", f"/projects/{project['id']}/queries")
refined = call("POST", f"/projects/{project['id']}/requests", {"query": "оставь синие"})
refined_queries = call("GET", f"/projects/{project['id']}/queries")
undo = call("POST", f"/projects/{project['id']}/requests", {"query": "отмени"})
new_search = call("POST", f"/projects/{project['id']}/requests", {"query": "найди кружки"})
new_queries = call("GET", f"/projects/{project['id']}/queries")

composite_chat = call("POST", "/chats", {"project_name": f"Router composite {stamp}"})
composite_project = next(
    item for item in call("GET", "/projects") if item["chat_id"] == composite_chat["id"]
)
composite = call(
    "POST", f"/projects/{composite_project['id']}/requests",
    {"query": "Собери 3 набора из зонтов, кружек от 20 штук"},
)
composite_refined = call(
    "POST", f"/projects/{composite_project['id']}/requests", {"query": "оставь синие"}
)

result = {
    "project_id": project["id"],
    "base": {"mode": base["mode"], "research_id": base["research"]["id"],
             "pool_count": base["research"]["pool_count"], "matching_count": base["research"]["matching_count"]},
    "refinement": {"mode": refined["mode"], "action": refined["route"]["action"],
                   "research_id": refined["research"]["id"], "pool_count": refined["research"]["pool_count"],
                   "matching_count": refined["research"]["matching_count"],
                   "colors": refined["research"]["view"]["filters"]["colors"]},
    "undo": {"mode": undo["mode"], "action": undo["route"]["action"],
             "matching_count": undo["research"]["matching_count"],
             "colors": undo["research"]["view"]["filters"]["colors"]},
    "new_search": {"mode": new_search["mode"], "action": new_search.get("route", {}).get("action"),
                   "research_id": new_search["research"]["id"]},
    "query_counts": {"after_base": len(base_queries), "after_refinement": len(refined_queries),
                     "after_new_search": len(new_queries)},
    "composite": {
        "base_mode": composite["mode"],
        "refined_mode": composite_refined["mode"],
        "action": composite_refined["route"]["action"],
        "parent_request_id": composite_refined["hard_request"]["parent_request_id"],
        "colors": [
            position["constraints"]["color"]["values"]
            for position in composite_refined["hard_request"]["plan"]["positions"]
        ],
    },
}
assert result["refinement"]["mode"] == "REFINEMENT"
assert result["refinement"]["action"] == "REFINE_CURRENT"
assert result["refinement"]["research_id"] == result["base"]["research_id"]
assert result["refinement"]["pool_count"] == result["base"]["pool_count"]
assert result["refinement"]["colors"]
assert len(refined_queries) == len(base_queries)
assert not result["undo"]["colors"]
assert result["new_search"]["mode"] == "SIMPLE"
assert result["new_search"]["research_id"] != result["base"]["research_id"]
assert len(new_queries) == len(base_queries) + 1
assert result["composite"]["base_mode"] == result["composite"]["refined_mode"] == "COMPOSITE"
assert result["composite"]["action"] == "REFINE_CURRENT"
assert result["composite"]["parent_request_id"]
assert all("BLUE" in colors for colors in result["composite"]["colors"])
Path(".local/conversation-router-live.json").write_text(
    json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
)
print(json.dumps(result, ensure_ascii=False, indent=2))
