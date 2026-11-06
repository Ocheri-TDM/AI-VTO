"""Audit actual next/font binaries; FontTools/Brotli are isolated verification tools.

Install those tools into .local/geist-font-audit-tools, not the application env.
"""

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".local/geist-font-audit-tools"))
from fontTools.ttLib import TTFont


def main():
    required = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyzАБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯабвгдеёжзийклмнопрстуфхцчшщъыьэюяӘҒҚҢӨҰҮҺІәғқңөұүһі0123456789№—·"
    rows = []
    coverage = set()
    for path in sorted((ROOT / "apps/web/.next/static/media").glob("*.woff2")):
        binary = path.read_bytes()
        font = TTFont(path)
        if font["name"].getDebugName(1) != "Geist":
            continue
        assert binary[:4] == b"wOF2"
        cmap = font.getBestCmap()
        coverage.update(cmap)
        features = [r.FeatureTag for r in font["GSUB"].table.FeatureList.FeatureRecord]
        axes = [
            {"tag": a.axisTag, "min": a.minValue, "max": a.maxValue}
            for a in font["fvar"].axes
        ]
        rows.append(
            {
                "file": path.name,
                "bytes": len(binary),
                "sha256": hashlib.sha256(binary).hexdigest(),
                "format": "WOFF2",
                "family": font["name"].getDebugName(1),
                "version": font["name"].getDebugName(5),
                "copyright": font["name"].getDebugName(0),
                "license": font["name"].getDebugName(14),
                "cmap_size": len(cmap),
                "kazakh": "".join(c for c in "ӘҒҚҢӨҰҮҺІәғқңөұүһі" if ord(c) in cmap),
                "tenge": 0x20B8 in cmap,
                "tabular_feature": "tnum" in features,
                "axes": axes,
            }
        )
    missing = "".join(c for c in required if ord(c) not in coverage)
    assert rows and not missing, missing
    assert any(r["tabular_feature"] for r in rows)
    result = {
        "status": "PASS",
        "fonts": rows,
        "total_build_bytes": sum(r["bytes"] for r in rows),
        "required_cmap_missing": missing,
        "tenge_in_geist": 0x20B8 in coverage,
        "tenge_policy": "Explicit Segoe UI / Arial / sans-serif fallback; browser audit verifies the actual family",
        "tools": {
            "fonttools": __import__("fontTools").__version__,
            "brotli": __import__("brotli").__version__,
        },
    }
    output = ROOT / "docs/screenshots/geist-ui/font-assets.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), "utf-8")
    print(
        "Actual WOFF2 audit PASS:",
        len(rows),
        "files,",
        result["total_build_bytes"],
        "bytes; tenge_in_geist=",
        result["tenge_in_geist"],
    )


if __name__ == "__main__":
    main()
