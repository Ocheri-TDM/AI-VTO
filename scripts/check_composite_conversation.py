"""Live HTTP acceptance for persistent composite revisions."""

import json
import os
import time
import urllib.request
from http.cookiejar import CookieJar

base = "http://127.0.0.1:8000/api"
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))


def call(method, path, body=None, timeout=240):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        base + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    with opener.open(request, timeout=timeout) as response:
        payload = response.read()
        return json.loads(payload) if payload else None


name = f"composite-live-{int(time.time())}"
r = call("POST", "/auth/register", {"name": name, "password": "Composite-pass-2026"})
r = call("POST", "/chats", {"project_name": "Composite persistence acceptance"})
chat_id = r["id"]
projects = call("GET", "/projects")
project = next(item for item in projects if item["chat_id"] == chat_id)

queries = json.loads(os.environ.get("COMPOSITE_QUERIES", "null")) or [
    "Собери 50 наборов из зонтов, рюкзаков, носков, футболок от 50 шт красного цвета",
    "Собери 50 наборов из зонтов синего цвета, дождевиков черного, носков белого, футболок красного от 250 шт",
    "Футболки сделай черными",
    "Теперь от 50 штук",
]
results = []
for query in queries:
    response = call("POST", f"/projects/{project['id']}/requests", {"query": query})
    value = response["hard_request"]
    cases = call("GET", f"/hard-requests/{value['id']}/cases")
    results.append(
        {
            "query": query,
            "request": value,
            "case_ids": [x["id"] for x in cases["cases"]],
        }
    )

active = call("GET", f"/projects/{project['id']}/active-composite")
messages = call("GET", f"/projects/{project['id']}/messages")
output = {
    "user": name,
    "project": project,
    "results": results,
    "active": active,
    "messages": messages,
}
print(json.dumps(output, ensure_ascii=False, indent=2))
