"""Real saved variant images plus an explicitly offline continuation stress fixture."""

import asyncio
import copy
import hashlib
import json
import shutil
from contextlib import closing

import openpyxl
import pypdfium2 as pdfium
from app.application.commercial_exports import render_offer
from app.config import Settings
from app.database.connection import create_database
from app.database.models import (
    CommercialOfferShare,
    PresentationDocument,
    PresentationRevision,
)
from check_commercial_offers import save
from check_offer_lifecycle import FIXTURES, LEDGER


async def main():
    state = json.loads(LEDGER.read_text("utf-8"))
    settings = Settings()
    engine, sessions = create_database(settings.database_url)
    try:
        async with sessions() as db:
            doc = await db.get(PresentationDocument, state["primary"])
            share = await db.get(CommercialOfferShare, doc.id)
            revision = await db.get(PresentationRevision, share.published_revision_id)
        assert (
            revision.status == "READY" and revision.input["offer_layout_version"] == 2
        )
        value = copy.deepcopy(revision.input)
        item = value["items"][1]
        item["description"] = "\n\n".join([item["description"]] * 15)
        value["items"] = [item]
        value["settings"]["title"] = (
            "Проверка длинного описания и логотипа в конце секции"
        )
        result = await asyncio.to_thread(
            render_offer,
            settings.presentation_storage,
            value,
            "offline-lifecycle-continuation",
        )
        root = settings.presentation_storage
        for kind in ("pdf", "xlsx"):
            shutil.copyfile(
                root / result[kind], FIXTURES / ("long-description." + kind)
            )
        artifacts = {}
        for name in ("offer", "offer-custom-header", "long-description"):
            pdf = FIXTURES / (name + ".pdf")
            with pdfium.PdfDocument(str(pdf)) as pages:
                texts = []
                for n in range(len(pages)):
                    with closing(pages[n]) as page:
                        with closing(page.get_textpage()) as textpage:
                            texts.append(textpage.get_text_range())
                        with closing(page.render(scale=1.2)) as bitmap:
                            bitmap.to_pil().save(
                                FIXTURES / (name + f"-page-{n + 1}.png")
                            )
                text = "\n".join(texts)
                artifacts[name] = {
                    "pages": len(pages),
                    "pdf_sha256": hashlib.sha256(pdf.read_bytes()).hexdigest(),
                    "kzt_glyph": "₸" in text,
                    "description_characters": len(item["description"])
                    if name == "long-description"
                    else None,
                }
            formula = openpyxl.load_workbook(
                FIXTURES / (name + ".xlsx"), data_only=False
            )
            values = openpyxl.load_workbook(FIXTURES / (name + ".xlsx"), data_only=True)
            assert formula.active.protection.sheet
            assert not formula.vba_archive and not formula._external_links
            assert formula.active._images
            assert all(
                "SUM(" not in str(cell.value) for row in formula.active for cell in row
            )
            # Each variant retains numeric price/quantity plus cached decimal amount.
            assert (
                formula.active["G2"].data_type == "n"
                and formula.active["H2"].data_type == "n"
            )
            assert (
                formula.active["I2"].data_type == "f"
                and values.active["I2"].data_type == "n"
            )
            artifacts[name]["xlsx_structure"] = (
                "PASS: numeric values/cached formulas, protected, thumbnails, no SUM/macros/external links"
            )
        save(
            FIXTURES / "layout-manifest.json",
            {
                "source": "Real saved VTO variants; continuation repeats its existing prose offline without DB writes",
                "published_revision_id": revision.id,
                "snapshot_hash": revision.result["snapshot_hash"],
                "artifacts": artifacts,
                "visual_review": "Required separately; rasterization and structure alone do not certify layout",
            },
        )
        print(
            "Rasterized",
            sum(a["pages"] for a in artifacts.values()),
            "pages; all XLSX structures checked",
        )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
