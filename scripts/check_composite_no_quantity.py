"""Live acceptance for Composite requests with an optional quantity constraint."""

import json
import time
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path
from uuid import uuid4

BASE = "http://127.0.0.1:8000"


def main():
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))

    def call(method, path, payload=None):
        body = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
        request = urllib.request.Request(
            BASE + path,
            data=body,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        started = time.perf_counter()
        try:
            with opener.open(request, timeout=120) as response:
                return response.status, json.loads(response.read()), round(
                    (time.perf_counter() - started) * 1000, 2
                )
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode(errors="replace"), round(
                (time.perf_counter() - started) * 1000, 2
            )

    name = f"null-safety-{uuid4().hex[:10]}"
    call("POST", "/api/auth/register", {"name": name, "password": "acceptance-password"})

    def project():
        _, chat, _ = call("POST", "/api/chats", {"project_name": f"Acceptance {uuid4().hex[:6]}"})
        _, projects, _ = call("GET", "/api/projects")
        return next(row["id"] for row in projects if row["chat_id"] == chat["id"])

    def submit(project_id, query):
        status, value, latency = call(
            "POST", f"/api/projects/{project_id}/requests", {"query": query}
        )
        if status < 400 and value.get("hard_request"):
            identifier = value["hard_request"]["id"]
            for _ in range(80):
                _, current, _ = call("GET", f"/api/hard-requests/{identifier}")
                if current["status"] != "RUNNING":
                    value["hard_request"] = current
                    break
                time.sleep(0.25)
        hard = value.get("hard_request", {}) if isinstance(value, dict) else {}
        plan = hard.get("plan", {})
        metrics = hard.get("metrics", {})
        return {
            "query": query,
            "http_status": status,
            "latency_ms": latency,
            "mode": value.get("mode") if isinstance(value, dict) else None,
            "target_cases": plan.get("target_sets"),
            "minimum_quantity": (plan.get("quantity") or {}).get("minimum"),
            "quantity_required": (plan.get("quantity") or {}).get("required"),
            "case_count": hard.get("completed_cases"),
            "status": hard.get("status"),
            "bottleneck": metrics.get("bottleneck_positions"),
            "llm_used": (plan.get("query_understanding") or {}).get("llm_used"),
            "browser_invoked": False,
            "metrics": metrics,
            "request_id": hard.get("id"),
        }

    exact_project = project()
    exact = submit(
        exact_project,
        "собери 50 наборов из футболок, ручек, бутылок и кружек",
    )
    explicit = submit(
        project(),
        "собери 50 наборов из футболок, ручек, бутылок и кружек от 250 шт",
    )
    typo = submit(
        project(),
        "сабери 50 набороф из футболак, ручек, бутылок и кружек",
    )
    follow_up = submit(exact_project, "теперь от 250 штук")
    result = {
        "generated_at_epoch": time.time(),
        "exact": exact,
        "explicit_quantity": explicit,
        "typo_without_quantity": typo,
        "quantity_follow_up": follow_up,
    }
    Path(".local/composite-no-quantity-live.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
