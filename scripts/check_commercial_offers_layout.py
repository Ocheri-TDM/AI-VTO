"""Offline continuation fixture from real saved VTO photos, no database writes.

Repeat the same published product prose to exercise length; never catalog data.
"""

import asyncio
import copy
import json
import shutil
from pathlib import Path

import pypdfium2 as pdfium
from app.application.commercial_exports import render_offer
from app.config import Settings
from app.database.connection import create_database
from app.database.models import PresentationDocument, PresentationRevision
from check_commercial_offers import FIXTURES, LEDGER, save


async def main():
    state = json.loads(LEDGER.read_text("utf-8"))
    settings = Settings()
    engine, sessions = create_database(settings.database_url)
    try:
        async with sessions() as db:
            doc = await db.get(PresentationDocument, state["primary"])
            revision = await db.get(PresentationRevision, state["primary_revision"])
        value = copy.deepcopy(doc.draft)
        value["document_id"] = doc.id
        value["resolved_logo"] = revision.input["resolved_logo"]
        item = value["items"][1]
        item["quantity"] = 100
        original = item["description"]
        assert original
        item["description"] = "\n\n".join([original] * 10)
        value["items"] = [item]
        value["settings"]["title"] = "Проверка переноса полного описания"
        result = await asyncio.to_thread(
            render_offer, settings.presentation_storage, value, "layout-continuation"
        )
        root = Path(settings.presentation_storage)
        shutil.copyfile(root / result["pdf"], FIXTURES / "long-description.pdf")
        shutil.copyfile(root / result["xlsx"], FIXTURES / "long-description.xlsx")
        with pdfium.PdfDocument(str(FIXTURES / "long-description.pdf")) as pages:
            for n, page in enumerate(pages):
                page.render(scale=1.2).to_pil().save(
                    FIXTURES / f"long-page-{n + 1}.png"
                )
            count = len(pages)
        save(
            FIXTURES / "long-description.json",
            {
                "source": "offline layout fixture; actual VTO description repeated ten times without catalog writes",
                "sku": item["sku"],
                "description_characters": len(item["description"]),
                "pages": count,
                "line_amount": result["client_snapshot"]["items"][0]["line_amount"],
            },
        )
        print("Continuation fixture:", count, "pages")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
