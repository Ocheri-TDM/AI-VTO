"""Canonical versus typo mutation chain through the real HTTP/PostgreSQL pipeline."""

import json
import time
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

from app.domain.intent_mutation import IntentMutationService

BASE = "http://127.0.0.1:8000/api"
client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
mutation_service = IntentMutationService()


def call(method, path, body=None):
    started = time.perf_counter()
    req = urllib.request.Request(BASE + path,
        data=json.dumps(body, ensure_ascii=False).encode() if body is not None else None,
        method=method, headers={"Content-Type": "application/json"})
    with client.open(req, timeout=600) as response:
        return response.status, json.loads(response.read()), round((time.perf_counter()-started)*1000, 2)


def create_project(name):
    _, chat, _ = call("POST", "/chats", {"project_name": name})
    _, projects, _ = call("GET", "/projects")
    return next(row["id"] for row in projects if row["chat_id"] == chat["id"])


def compact(plan):
    positions = {}
    for position in plan["positions"]:
        colors = (position["constraints"].get("color") or {}).get("values", [])
        concepts = mutation_service._concepts(position["category_text"])
        positions[concepts[0] if concepts else position["category_text"]] = colors
    return {"target_cases": plan["target_sets"],
            "required_quantity": plan["quantity"]["minimum"], "positions": positions}


canonical = [
    "собери 50 наборов из зонтов, ручек, бутылок и футболок",
    "замени цвет футболок на красный",
    "ручки сделай синими",
    "добавь черные рюкзаки",
    "замени бутылки на белые кружки",
    "убери зонты",
    "теперь от 250 штук",
    "сделай 20 наборов",
    "количество товара больше не важно",
    "оставь только ручки, футболки и рюкзаки",
]
typo = [
    "сабери 50 набороф из зонтоф, ручек, бутылок и футболак",
    "замени цвет фудболок на крассный",
    "ручьки зделай синими",
    "дабавь чорные рюкзаги",
    "замени бутылки на белые крушки",
    "убири зонты",
    "теперь ат 250 штук",
    "зделай 20 наборов",
    "количество тавара больше не важно",
    "аставь только ручьки, фудболки и рюкзаги",
]

stamp = int(time.time())
call("POST", "/auth/register", {"name": f"typo-chain-{stamp}", "password": "Typo-chain-2026"})
result = {"seed": 20261005, "chains": {}}
for label, messages in (("canonical", canonical), ("typo", typo)):
    project_id = create_project(f"{label}-{stamp}")
    revisions = []
    for message in messages:
        status, response, latency = call("POST", f"/projects/{project_id}/requests", {"query": message})
        hard = response.get("hard_request")
        revisions.append({"message": message, "http_status": status,
            "mode": response.get("mode"), "route": response.get("route", {}).get("action"),
            "latency_ms": latency, "state": compact(hard["plan"]) if hard else None,
            "revision": hard.get("revision_number") if hard else None,
            "understanding": hard["plan"].get("query_understanding", {}) if hard else {}})
        if not hard:
            break
    result["chains"][label] = {"project_id": project_id, "revisions": revisions,
        "final": revisions[-1]["state"]}

canonical_final = result["chains"]["canonical"]["final"]
typo_final = result["chains"]["typo"]["final"]
result["semantic_parity"] = canonical_final == typo_final
result["unexpected_500"] = sum(
    row["http_status"] >= 500 for chain in result["chains"].values() for row in chain["revisions"]
)
Path(".local/composite-typo-chain.json").write_text(
    json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(result, ensure_ascii=False, indent=2))
