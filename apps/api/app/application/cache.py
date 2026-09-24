import hashlib
import json

from app.domain.models import SearchIntent
from app.domain.services import normalize_text


def cache_key(
    intent: SearchIntent, supplier_names: list[str], rate: str | None, limits: tuple[int, int]
) -> str:
    payload = intent.model_dump(mode="json")
    payload["raw_query"] = normalize_text(intent.raw_query)
    payload["categories"] = sorted(set(payload["categories"]))
    # NAVY and DARK_BLUE are one search equivalence class.
    payload["colors"] = sorted(
        {"NAVY" if color in ("NAVY", "DARK_BLUE") else color for color in payload["colors"]}
    )
    payload.update(suppliers=sorted(supplier_names), rate=rate, limits=limits, schema_version=1)
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()
