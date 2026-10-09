"""Compare public SIMPLE pools with Composite pre-availability membership."""

import json
import time
import urllib.request
from http.cookiejar import CookieJar

base = "http://127.0.0.1:8000/api"
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))


def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        base + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    with opener.open(request, timeout=240) as response:
        return json.loads(response.read())


name = f"parity-live-{int(time.time())}"
call("POST", "/auth/register", {"name": name, "password": "Parity-pass-2026"})
result = {}
for category in ("зонты", "карандаши", "ручки", "кружки"):
    chat = call("POST", "/chats", {"project_name": f"Parity {category}"})
    project = next(
        item for item in call("GET", "/projects") if item["chat_id"] == chat["id"]
    )
    response = call(
        "POST", f"/projects/{project['id']}/requests", {"query": f"синие {category}"}
    )
    research = response["research"]
    result[category] = {
        "pool_count": research["pool_count"],
        "matching_count": research["matching_count"],
    }
print(json.dumps(result, ensure_ascii=False, indent=2))
