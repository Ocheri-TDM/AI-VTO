"""Bounded child-process entrypoint. No database, supplier lease or AI service."""

import json
import os
import sys
import time
from pathlib import Path


def run(input_path):
    import psutil

    from app.application.presentation_assets import AssetPreparer
    from app.application.presentation_render import render_document

    payload = json.loads(Path(input_path).read_text("utf-8"))
    root = Path(payload["storage"])
    value = payload["input"]
    revision = payload["revision"]

    def progress(phase):
        print(json.dumps({"phase": phase}), flush=True)

    assets = {}
    issues = []
    preparer = AssetPreparer(root)
    started = time.perf_counter()
    progress("PREPARING_ASSETS")
    for item in value["items"]:
        try:
            assets[item["offer_id"]] = preparer.prepare(
                item, item["offer_id"] in value["original_fallback_ids"]
            )
        except Exception as exc:
            message = (
                str(exc)
                if isinstance(exc, ValueError)
                else "Источник фотографии недоступен. Выберите другое фото или загрузите подготовленное."
            )
            issues.append({"offer_id": item["offer_id"], "message": message[:250]})
    result = {"assets": assets, "issues": issues, "asset_seconds": round(time.perf_counter() - started, 3)}
    if not issues:
        progress("RENDERING")
        try:
            result.update(render_document(root, revision, value, assets, progress))
        except ValueError as exc:
            issues.append({"offer_id": None, "message": str(exc)})
    result["status"] = (
        "NEEDS_REVIEW" if issues or any(a["review_required"] for a in assets.values()) else "READY"
    )
    result["total_seconds"] = round(time.perf_counter() - started, 3)
    result["end_rss_bytes"] = psutil.Process().memory_info().rss
    Path(payload["output"]).write_text(json.dumps(result, ensure_ascii=False), "utf-8")
    progress(result["status"])


if __name__ == "__main__":
    os.environ["OMP_NUM_THREADS"] = "2"
    os.environ["OPENBLAS_NUM_THREADS"] = "2"
    run(sys.argv[1])
