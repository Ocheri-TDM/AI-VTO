"""Live acceptance for index-first typo normalization."""

import json
import time
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

BASE = "http://127.0.0.1:8000/api"
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))


def call(method, path, body=None, timeout=120):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body else None
    request = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json; charset=utf-8"} if data else {},
    )
    with opener.open(request, timeout=timeout) as response:
        return json.loads(response.read())


stamp = int(time.time())
user = f"query-understanding-{stamp}"
password = "Query-pass-2026"
project_name = f"Query understanding {stamp}"
call("POST", "/auth/register", {"name": user, "password": password})
chat = call("POST", "/chats", {"project_name": project_name})
project = next(item for item in call("GET", "/projects") if item["chat_id"] == chat["id"])
started = time.perf_counter()
response = call("POST", f"/projects/{project['id']}/requests", {"query": "корондаш"})
elapsed = round((time.perf_counter() - started) * 1000, 2)
history = call("GET", f"/projects/{project['id']}/queries")
messages = call("GET", f"/projects/{project['id']}/messages")
research = response["research"]
simple = {
    "http_ms": elapsed,
    "mode": response["mode"],
    "pool_count": research["pool_count"],
    "matching_count": research["matching_count"],
    "background_job_ids": research.get("background_job_ids", []),
    "intent": research["intent"],
    "query": history[0],
    "messages": messages,
}
composite_query = (
    "Сабери 50 набороф из синих зонтоф, чорных дождевеков, "
    "белых наскофф, красных футболак от 250 штук"
)
second_chat = call("POST", "/chats", {"project_name": f"Composite typo {stamp}"})
second_project = next(
    item for item in call("GET", "/projects") if item["chat_id"] == second_chat["id"]
)
composite_started = time.perf_counter()
composite_response = call(
    "POST",
    f"/projects/{second_project['id']}/requests",
    {"query": composite_query},
    timeout=180,
)
composite = {
    "http_ms": round((time.perf_counter() - composite_started) * 1000, 2),
    "mode": composite_response["mode"],
    "request": composite_response.get("hard_request"),
}
result = {
    "user": user,
    "password": password,
    "project_name": project_name,
    "simple": simple,
    "composite": composite,
}
Path(".local/query-understanding-live.json").write_text(
    json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
)
print(json.dumps(result, ensure_ascii=False, indent=2))
