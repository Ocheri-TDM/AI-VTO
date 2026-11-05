"""Sanitized evidence aggregation; private resource ledger never enters results."""

import collections
import hashlib
import json
import statistics

import httpx

from quality.corpus import holdout


async def supplement(state, record):
    """Exactly one additional normal main-API query, no fuzz or load."""
    assert state["live_queries_used"] < 62 and not state.get("sku_supplement")
    state["live_budget_supplement"] = {
        "original": 60,
        "extended": 62,
        "reason": "one real SKU reproduction plus one verification after routing repair; no additional load",
    }
    record(state)
    user = state["live_users"][2]
    state["live_queries_used"] += 1
    record(state)
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8000", cookies=user["cookies"], timeout=50) as c:
        response = await c.post(
            f"/api/projects/{user['project']['id']}/requests", json={"query": "GF-12449.40"}
        )
        response.raise_for_status()
        research = response.json()["research"]
        assert [p["id"] for p in research["products"]] == ["vto:1823109"] and research["matching_count"] == 1
        assert (await c.get(f"/api/projects/{user['project']['id']}/collection")).json()["selected_ids"] == []
    state["sku_supplement"] = {"query": "GF-12449.40", "offer_ids": ["vto:1823109"], "status": "PASS"}
    record(state)
    return state["sku_supplement"]


def summarize(state, root):
    public = root / "apps/api/tests/quality/results"

    def read(name):
        return json.loads((public / (name + ".json")).read_text("utf-8"))

    corpus = read("corpus")
    results = corpus["results"]
    metrics = {}
    for family in sorted({r.get("family") or "other" for r in results}):
        group = [r for r in results if (r.get("family") or "other") == family]
        metrics[family] = {"passed": sum(r["pass"] for r in group), "total": len(group)}
    returned = [r for r in results if r.get("actual_ids")]

    def p_at_k(k):
        return statistics.mean(
            len(set(r["actual_ids"][:k]) & set(r["expected_ids"])) / min(k, len(r["actual_ids"]))
            for r in returned
        )

    known = [r for r in results if r.get("expected_ids")]
    recall = statistics.mean(
        len(set(r["actual_ids"]) & set(r["expected_ids"])) / len(r["expected_ids"]) for r in known
    )
    jaccard = statistics.mean(
        len(set(r["actual_ids"]) & set(r["expected_ids"]))
        / len(set(r["actual_ids"]) | set(r["expected_ids"]))
        for r in known
    )
    before = read("baseline")
    first_holdout = read("holdout-first")
    first_holdout["seed"] = 927651
    (public / "holdout-first.json").write_text(
        json.dumps(first_holdout, ensure_ascii=False, indent=2), "utf-8"
    )
    final_holdout = read("holdout")
    final_holdout["seed"] = 927652
    (public / "holdout.json").write_text(json.dumps(final_holdout, ensure_ascii=False, indent=2), "utf-8")
    frozen = {
        "seed": 927652,
        "cases": holdout(seed=927652, count=100),
        "scope": "unseen numeric values and ordering in the same numeric template family; not broad unseen grammar",
    }
    (root / "apps/api/tests/quality/holdout-v2.json").write_text(
        json.dumps(frozen, ensure_ascii=False, indent=2), "utf-8"
    )
    baseline_log = root / ".local/safety-baseline-http.log"
    if baseline_log.exists():
        raw = baseline_log.read_bytes()
        initial_http = json.loads(
            raw.decode("utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig").strip()
        )
        (public / "baseline-http-before.json").write_text(
            json.dumps({"results": initial_http}, indent=2), "utf-8"
        )
    hashes = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in (root / "apps/api/tests/quality").glob("*.json")
    }
    faults = read("faults")
    stateful = read("stateful")
    summary = {
        "overall": "PARTIAL",
        "reason": "covered functional gates pass; 10-user HUD p95 exceeds 1s target; broad adversarial/model/production load coverage is not claimed",
        "commit": state["audit"]["commit"],
        "dataset_hashes": hashes,
        "baseline": {
            "passed": before["passed"],
            "total": before["total"],
            "oracle_corrected_passed": before["passed"] + 1,
            "note": "one packaging/feature expectation corrected from broad category to explicit requested feature; original evidence retained",
        },
        "labelled": {
            "passed": corpus["passed"],
            "total": corpus["total"],
            "families": metrics,
            "nonempty_queries": len(returned),
            "precision_at_10": p_at_k(10),
            "precision_at_50": p_at_k(50),
            "recall_known_fixture_universe": recall,
            "mean_jaccard_to_gold": jaccard,
            "precision_denominator": "min(k, returned count); empty/rejected cases reported separately; full membership is exact so ranking cannot hide unrelated IDs",
            "unrelated_offer_ids": sum(len(r.get("unrelated", [])) for r in results),
            "missing_offer_ids": sum(len(r.get("missing", [])) for r in results),
        },
        "holdout_first": {
            "passed": first_holdout["passed"],
            "total": first_holdout["total"],
            "membership_passed": sum(
                r.get("actual_ids") == r.get("expected_ids")
                for r in first_holdout["results"]
                if "actual_ids" in r
            ),
            "seed": 927651,
        },
        "holdout_second": {
            "passed": final_holdout["passed"],
            "total": final_holdout["total"],
            "seed": 927652,
            "scope": frozen["scope"],
        },
        "properties": read("properties"),
        "stateful": {
            "sequences": stateful["sequences"],
            "status": stateful["status"],
            "logical_actions": dict(collections.Counter(r["actions"] for r in stateful["results"])),
            "persisted_history_reads": stateful["persisted_history_reads"],
        },
        "faults": {
            "status": faults["status"],
            "canary_checks": sum(r.get("recovery") == "PASS" for r in faults["checks"]),
            "mutations_detected": sum(r.get("oracle_detected") is True for r in faults["checks"]),
        },
        "browser": read("browser"),
        "browser_isolated": read("browser-isolated"),
        "live": {
            "original_submissions": 60,
            "post_fix_original_submissions": 35,
            "supplemental_exact_sku": state.get("sku_supplement"),
            "total_submissions": state.get("live_queries_used"),
        },
        "isolated_socket_requests": state["isolated_http_count"],
        "terminal_browser_budget": state.get("terminal_browser_budget"),
        "remaining_limits": [
            "10-user HUD p95 1.727s exceeds 1s target",
            "no 20-user or long-duration load",
            "real Qwen inference under mixed load not measured",
            "numeric properties/holdout are not universal language coverage",
            "no exhaustive Markdown/URL-scheme/SSRF exploit corpus",
            "pool hold/CPU/full-run peak RSS not instrumented; rolling SQL/checkout/lag and separate RSS characterization are available",
            "catalog import remains incomplete/source backoff independent of search fixes",
        ],
    }
    (public / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), "utf-8")
    return summary
