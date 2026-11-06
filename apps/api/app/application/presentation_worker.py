"""Bounded child-process entrypoint. No database, supplier lease or AI service."""

import json
import os
import sys
import time
from pathlib import Path


def run(input_path):
    import psutil
    from PIL import Image

    from app.application.presentation_assets import AssetPreparer, safe_path

    payload = json.loads(Path(input_path).read_text("utf-8"))
    root = Path(payload["storage"])
    value = payload["input"]
    revision = payload["revision"]

    def progress(phase):
        print(json.dumps({"phase": phase}), flush=True)

    if value.get("kind") == "COMMERCIAL_OFFER":
        from app.application.commercial_exports import render_offer

        result = render_offer(root, value, revision, progress)
        Path(payload["output"]).write_text(json.dumps(result, ensure_ascii=False), "utf-8")
        return

    from app.application.presentation_render import render_document
    from app.domain.presentation_design import deterministic_plan

    if value.get("kind") == "PREPARE_MATERIAL":
        from app.application.presentation_assets import prepare_material

        progress("PREPARING_ASSETS")
        try:
            material = prepare_material(root, value["material"], value["limits"])
            result = {"status": "READY", "material": material}
        except Exception as exc:
            if not isinstance(exc, ValueError):
                import traceback

                traceback.print_exc()
            result = {
                "status": "FAILED",
                "message": str(exc)
                if isinstance(exc, ValueError)
                else "Не удалось прочитать изображение. Разрешены PNG, JPEG и WebP.",
            }
        Path(payload["output"]).write_text(json.dumps(result, ensure_ascii=False), "utf-8")
        return

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
        geometry = []
        for id, asset in assets.items():
            with Image.open(safe_path(root, asset["key"])) as image:
                geometry.append(
                    {
                        "offer_id": id,
                        "width": image.width,
                        "height": image.height,
                        "aspect": round(image.width / image.height, 3),
                        "has_alpha": True,
                    }
                )
        # The parent owns the shared local-LLM queue. The child only exchanges
        # closed-schema data via an owned file, never executable model output.
        plan_file = Path(payload["plan_file"]) if payload.get("plan_file") else None
        design = None
        if plan_file:
            print(json.dumps({"phase": "PLANNING_DESIGN", "geometry": geometry}), flush=True)
            deadline = time.monotonic() + payload.get("plan_timeout", 25) + 10
            while time.monotonic() < deadline:
                if plan_file.exists():
                    design = json.loads(plan_file.read_text("utf-8"))
                    break
                time.sleep(0.1)
        if design is None:
            design = {
                "design_plan": deterministic_plan(
                    value["items"],
                    value["settings"],
                    value["category_order"],
                    geometry,
                    value.get("materials", []),
                ).model_dump(),
                "ai_used": False,
                "fallback_reason": "PARENT_PLANNER_TIMEOUT" if plan_file else "DISABLED",
                "ai_seconds": 0,
            }
        result.update(design)
        value["design_plan"] = design["design_plan"]
        progress("RENDERING")
        try:
            result.update(render_document(root, revision, value, assets, progress))
            issues.extend(result.get("material_issues", []))
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
