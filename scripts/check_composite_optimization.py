"""Live HTTP timing and recovery trace for the optimized Composite pipeline."""

import json
import os
import time
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

BASE = "http://127.0.0.1:8000/api"
QUERY = os.environ.get(
    "COMPOSITE_QUERY",
    "Собери 50 наборов из зонтов, карандашей, ручек, кружек от 20 шт синего цвета",
)
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))


def call(method, path, body=None, timeout=240):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json; charset=utf-8"} if data else {},
    )
    with opener.open(request, timeout=timeout) as response:
        payload = response.read()
        return json.loads(payload) if payload else None


stamp = int(time.time())
call("POST", "/auth/register", {"name": f"composite-opt-{stamp}", "password": "Composite-pass-2026"})
chat = call("POST", "/chats", {"project_name": f"Composite optimization {stamp}"})
project = next(item for item in call("GET", "/projects") if item["chat_id"] == chat["id"])
started = time.perf_counter()
response = call("POST", f"/projects/{project['id']}/requests", {"query": QUERY})
initial_ms = round((time.perf_counter() - started) * 1000, 2)
initial = response["hard_request"]
timeline = [{"at_ms": initial_ms, "revision": initial["revision"], "completed": initial["completed_cases"], "metrics": initial["metrics"]}]
deadline = time.monotonic() + float(os.environ.get("RECOVERY_WAIT_SECONDS", "180"))
last = (initial["id"], initial["revision"], initial["completed_cases"])
while time.monotonic() < deadline and initial["metrics"].get("pending_availability_recovery"):
    time.sleep(2)
    current = call("GET", f"/projects/{project['id']}/active-composite")
    marker = (current["id"], current["revision"], current["completed_cases"])
    if marker != last:
        timeline.append({
            "at_ms": round((time.perf_counter() - started) * 1000, 2),
            "revision": current["revision"],
            "completed": current["completed_cases"],
            "metrics": current["metrics"],
        })
        last = marker
    if current["id"] != initial["id"] or not current["metrics"].get("pending_availability_recovery"):
        break
result = {"query": QUERY, "project": project, "initial_http_ms": initial_ms, "initial": initial, "timeline": timeline}
path = Path(os.environ.get("COMPOSITE_OUTPUT", ".local/composite-optimization-live.json"))
path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(result, ensure_ascii=False, indent=2))
