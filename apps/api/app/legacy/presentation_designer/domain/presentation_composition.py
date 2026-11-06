"""Closed geometry vocabulary shared by planning, fingerprints and the painter."""

import hashlib
import json

TITLE_GEOMETRY = {
    "centered": {
        "heading": (160, 330, 640, 150),
        "logo": (324, 388, 312, 56),
        "subtitle": (200, 158, 560, 68),
        "alignment": "center",
        "graphic": "horizon",
    },
    "split": {
        "heading": (70, 365, 500, 160),
        "logo": (70, 415, 260, 52),
        "subtitle": (70, 170, 470, 80),
        "alignment": "left",
        "graphic": "orbit",
    },
    "band": {
        "heading": (178, 350, 690, 145),
        "logo": (178, 410, 270, 54),
        "subtitle": (178, 170, 630, 70),
        "alignment": "left",
        "graphic": "band",
    },
    "arch": {
        "heading": (70, 310, 570, 140),
        "logo": (70, 386, 300, 60),
        "subtitle": (70, 145, 570, 70),
        "alignment": "left",
        "graphic": "arch",
    },
    "open": {
        "heading": (342, 380, 550, 160),
        "logo": (70, 402, 220, 46),
        "subtitle": (342, 180, 510, 80),
        "alignment": "left",
        "graphic": "steps",
    },
}


def signature(plan):
    title = next((p for p in plan.pages if p.layout == "title"), None)
    geometry = TITLE_GEOMETRY[title.title_family] if title else {}
    features = {k: geometry.get(k) for k in ("heading", "logo", "alignment", "graphic")}

    def profile(page):
        if page.layout == "hero" and page.variant == 1:
            return "photo-left"
        if (
            page.layout == "variants"
            and plan.style == "premium-dark"
            and plan.style_version == 3
            and page.variant == 2
        ):
            return "raised-triptych"
        return "base"

    features["product_compositions"] = [
        (p.layout, profile(p)) for p in plan.pages if p.layout not in ("title", "cover", "ending")
    ]
    # JSON normalization also makes persisted lists compare equal to tuples.
    features = json.loads(json.dumps(features))
    return {
        "hash": hashlib.sha256(json.dumps(features, sort_keys=True).encode()).hexdigest(),
        "features": features,
    }


def differences(first, second):
    return sum(value != second.get(key) for key, value in first.items())


def novelty(plan, recent):
    value = signature(plan)
    value["compared_count"] = len(recent)
    value["minimum_feature_changes"] = min(
        (differences(value["features"], r["features"]) for r in recent), default=None
    )
    value["status"] = "DISTINCT" if not recent or value["minimum_feature_changes"] >= 2 else "CONSTRAINED"
    return value
