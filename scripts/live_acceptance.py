"""Read-only end-to-end verification against running API and real suppliers.

Run: .venv/Scripts/python.exe scripts/live_acceptance.py
Writes only a summary of the application's search response to .local.
No fixture products or supplier traffic outside the application's browser flow.
"""
import asyncio
import json
from pathlib import Path

import httpx

QUERY = "Темно-синие термокружки, рюкзаки, ежедневники и ручки. Тираж 300 шт."


async def main():
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8000", timeout=30) as client:
        health = await client.get("/api/health")
        health.raise_for_status()
        assert health.json()["rub_kzt_rate_configured"], "Configure RUB_KZT_RATE first"
        response = await client.post("/api/searches", json={"query": QUERY})
        response.raise_for_status()
        snapshot = response.json()
        session_id = snapshot["id"]
        for _ in range(180):
            if snapshot["status"] not in ("queued", "running"):
                break
            await asyncio.sleep(2)
            response = await client.get(f"/api/searches/{session_id}")
            response.raise_for_status()
            snapshot = response.json()
        assert snapshot["status"] not in ("queued", "running"), "Search did not finish within six minutes"
        assert snapshot["intent"]["quantity"] == 300
        assert len(snapshot["intent"]["categories"]) == 4
        for card in snapshot["products"]:
            assert isinstance(card["price_kzt"], int)
            assert not {"supplier", "stock_quantity", "source_url", "metadata"} & card.keys()
            detail = await client.get(f"/api/searches/{session_id}/products/{card['id']}")
            detail.raise_for_status()
            product = detail.json()
            assert product["stock_quantity"] >= 300
            assert any(color["normalized_color"] in ("NAVY", "DARK_BLUE") for color in product["colors"])
        if snapshot["status"] != "failed":
            again = await client.post("/api/searches", json={"query": QUERY})
            again.raise_for_status()
            assert again.json()["id"] == session_id and again.json()["cache_hit"]
        filtered = await client.post(f"/api/searches/{session_id}/filter", json={"max_price_kzt": 10000})
        filtered.raise_for_status()
        assert all(p["price_kzt"] <= 10000 for p in filtered.json()["products"])
        summary = {
            "id": session_id, "status": snapshot["status"], "products": len(snapshot["products"]),
            "intent": snapshot["intent"], "suppliers": snapshot["suppliers"],
            "created_at": snapshot["created_at"], "expires_at": snapshot["expires_at"],
        }
        Path(".local").mkdir(exist_ok=True)
        Path(".local/live-acceptance.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        assert snapshot["products"], "No verified matching products: see supplier statuses; no fake fallback used"


if __name__ == "__main__":
    asyncio.run(main())
