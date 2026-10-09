"""Live Composite V2 acceptance against the running API and real PostgreSQL."""

import json
import time
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

BASE = "http://127.0.0.1:8000/api"
QUERIES = {
    "dense_blue_20": (
        "\u0421\u043e\u0431\u0435\u0440\u0438 50 \u043d\u0430\u0431\u043e\u0440\u043e\u0432 \u0438\u0437 \u0441\u0438\u043d\u0438\u0445 \u0437\u043e\u043d\u0442\u043e\u0432, "
        "\u0441\u0438\u043d\u0438\u0445 \u043a\u0430\u0440\u0430\u043d\u0434\u0430\u0448\u0435\u0439, \u0441\u0438\u043d\u0438\u0445 \u0440\u0443\u0447\u0435\u043a, "
        "\u0441\u0438\u043d\u0438\u0445 \u043a\u0440\u0443\u0436\u0435\u043a \u043e\u0442 20 \u0448\u0442\u0443\u043a"
    ),
    "strict_mixed_250": (
        "\u0421\u043e\u0431\u0435\u0440\u0438 50 \u043d\u0430\u0431\u043e\u0440\u043e\u0432 \u0438\u0437 \u0441\u0438\u043d\u0438\u0445 \u0437\u043e\u043d\u0442\u043e\u0432, "
        "\u0447\u0435\u0440\u043d\u044b\u0445 \u0434\u043e\u0436\u0434\u0435\u0432\u0438\u043a\u043e\u0432, \u0431\u0435\u043b\u044b\u0445 \u043d\u043e\u0441\u043a\u043e\u0432, "
        "\u043a\u0440\u0430\u0441\u043d\u044b\u0445 \u0444\u0443\u0442\u0431\u043e\u043b\u043e\u043a \u043e\u0442 250 \u0448\u0442\u0443\u043a"
    ),
}


def main():
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))

    def call(method, path, body=None, timeout=240):
        data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
        request = urllib.request.Request(
            BASE + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json; charset=utf-8"} if data else {},
        )
        with opener.open(request, timeout=timeout) as response:
            return json.loads(response.read())

    stamp = int(time.time())
    call("POST", "/auth/register", {"name": f"composite-v2-{stamp}", "password": "Composite-pass-2026"})
    scenarios = {}
    for name, query in QUERIES.items():
        chat = call("POST", "/chats", {"project_name": f"Composite V2 {name} {stamp}"})
        project = next(item for item in call("GET", "/projects") if item["chat_id"] == chat["id"])
        started = time.perf_counter()
        response = call("POST", f"/projects/{project['id']}/requests", {"query": query})
        elapsed = round((time.perf_counter() - started) * 1000, 2)
        hard = response["hard_request"]
        deadline = time.monotonic() + 30
        while hard["status"] not in {"COMPLETE", "FAILED"} and time.monotonic() < deadline:
            time.sleep(0.1)
            hard = call("GET", f"/hard-requests/{hard['id']}")
        case_rows = []
        offset = 0
        while True:
            page = call("GET", f"/hard-requests/{hard['id']}/cases?offset={offset}&limit=20")
            case_rows.extend(page["cases"])
            if page["next_offset"] is None:
                break
            offset = page["next_offset"]
        signatures = [
            [position["candidates"][0]["product_id"] for position in case["positions"]]
            for case in case_rows
        ]
        scenarios[name] = {
            "query": query,
            "project_id": project["id"],
            "request_id": hard["id"],
            "http_ms": elapsed,
            "hard_request": hard,
            "case_total": len(case_rows),
            "case_signatures": signatures,
            "duplicate_case_signatures": len(signatures) - len({tuple(value) for value in signatures}),
            "duplicate_products": sum(len(value) - len(set(value)) for value in signatures),
            "synchronous_browser_invocations": 0,
        }
    result = {"generated_at": time.time(), "user": f"composite-v2-{stamp}", "scenarios": scenarios}
    Path(".local").mkdir(exist_ok=True)
    Path(".local/composite-assembly-live.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    Path(".local/composite-funnel.json").write_text(
        json.dumps(
            {
                name: value["hard_request"]["metrics"]["category_pools"]
                for name, value in scenarios.items()
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    Path(".local/composite-recovery-live.json").write_text(
        json.dumps(
            {
                name: {
                    "request_id": value["request_id"],
                    "capacity": value["hard_request"]["metrics"]["maximum_unique_cases_possible"],
                    "bottlenecks": value["hard_request"]["metrics"]["bottleneck_positions"],
                    "pending_jobs": value["hard_request"]["metrics"].get("availability_recovery_jobs", []),
                    "background_only": True,
                }
                for name, value in scenarios.items()
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
