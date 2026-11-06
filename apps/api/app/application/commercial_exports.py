"""Static PDF and XLSX from one immutable, allowlisted client snapshot.

Runs exclusively in the existing supervised document worker subprocess.
"""

import hashlib
import io
import time
from html import escape
from pathlib import Path

from PIL import Image
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    Flowable,
    Frame,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus import Image as PdfImage

from app.application.document_previews import pdf_previews
from app.application.presentation_assets import AssetPreparer, safe_path
from app.application.presentation_logo import frozen_logo_path
from app.domain.commercial_offer import client_snapshot, snapshot_hash, validate_document

FONT_ROOT = Path(__file__).resolve().parents[1] / "assets/fonts"
INK = colors.HexColor("#151918")
ORANGE = colors.HexColor("#f42902")
GREEN = colors.HexColor("#83FD80")
PAPER = colors.HexColor("#FDFDFF")


def fonts():
    for name, file in [
        ("OfferRegular", "Geist-Regular.ttf"),
        ("OfferBold", "Geist-Bold.ttf"),
        ("OfferFallback", "NotoSans-Regular.ttf"),
    ]:
        if name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(name, str(FONT_ROOT / file)))
    pdfmetrics.registerFontFamily(
        "OfferRegular", normal="OfferRegular", bold="OfferBold", italic="OfferRegular", boldItalic="OfferBold"
    )


def text(value):
    # The existing Geist subsets lack U+20B8; embed the established Noto fallback.
    return escape(str(value)).replace("₸", '<font name="OfferFallback">₸</font>')


def money(value):
    integer, decimals = value.split(".")
    groups = []
    while integer:
        groups.insert(0, integer[-3:])
        integer = integer[:-3]
    return " ".join(groups) + "," + decimals + " ₸"


def paragraph(value, size=10, bold=False, space=8):
    return Paragraph(
        text(value).replace("\n", "<br/>"),
        ParagraphStyle(
            "offer",
            fontName="OfferBold" if bold else "OfferRegular",
            fontSize=size,
            leading=size * 1.45,
            textColor=INK,
            spaceAfter=space,
            splitLongWords=True,
        ),
    )


class ProductStart(Flowable):
    def __init__(self, title):
        super().__init__()
        self.title = title
        self.width = self.height = 0

    def draw(self):
        self.canv._doctemplate.product_title = self.title
        self.canv._doctemplate.new_product = True


class Banner(Flowable):
    def __init__(self, width, path=None):
        super().__init__()
        self.width = width
        self.height = 190
        self.path = path

    def draw(self):
        c = self.canv
        c.setFillColor(GREEN)
        c.rect(0, 0, self.width, self.height, fill=1, stroke=0)
        if self.path:
            c.drawImage(
                ImageReader(str(self.path)),
                0,
                0,
                width=self.width,
                height=self.height,
                preserveAspectRatio=True,
                anchor="c",
                mask="auto",
            )
        else:
            c.setFillColor(ORANGE)
            p = c.beginPath()
            p.moveTo(self.width * 0.70, 0)
            p.lineTo(self.width, 0)
            p.lineTo(self.width, self.height)
            p.lineTo(self.width * 0.90, self.height)
            p.close()
            c.drawPath(p, fill=1, stroke=0)
            c.setStrokeColor(INK)
            c.setLineWidth(0.8)
            for x in [36, 68, 100]:
                c.line(x, 30, x + 50, 100)


def image_flow(path, width, height):
    with Image.open(path) as src:
        factor = min(width / src.width, height / src.height)
        w, h = src.width * factor, src.height * factor
    return PdfImage(str(path), width=w, height=h, hAlign="LEFT")


def render_pdf(target, snapshot, paths, revision, digest):
    fonts()
    width, height = A4
    doc = BaseDocTemplate(
        str(target),
        pagesize=A4,
        leftMargin=36,
        rightMargin=36,
        topMargin=54,
        bottomMargin=44,
        title=snapshot["title"],
        author="Souvenir Studio",
    )
    doc.product_title = ""
    doc.new_product = True

    def page(c, d):
        c.setTitle(snapshot["title"])
        c.setAuthor("Souvenir Studio")
        c.setKeywords("revision=" + revision + ";snapshot=" + digest)
        c.setFillColor(PAPER)
        c.rect(0, 0, width, height, fill=1, stroke=0)
        c.setFont("OfferRegular", 8)
        c.setFillColor(INK)
        if d.page > 1:
            c.drawString(36, height - 28, "Souvenir Studio")
            if not d.new_product and d.product_title:
                title = d.product_title
                # Header is auxiliary; the full title is on the product's first page.
                while pdfmetrics.stringWidth(title + " — продолжение", "OfferRegular", 8) > width - 160:
                    title = title[:-1]
                label = title + " — продолжение"
                c.drawRightString(width - 36, height - 28, label)
            d.new_product = False
        c.setStrokeColor(colors.HexColor("#d7dcd9"))
        c.line(36, 34, width - 36, 34)
        c.drawString(36, 21, "Варианты для выбора. Наличие уточняется перед заказом.")
        c.drawRightString(width - 36, 21, str(d.page))

    doc.addPageTemplates(
        PageTemplate(
            id="offer",
            frames=[
                Frame(
                    36,
                    44,
                    width - 72,
                    height - 98,
                    leftPadding=0,
                    rightPadding=0,
                    topPadding=0,
                    bottomPadding=0,
                )
            ],
            onPage=page,
        )
    )
    logo = Table([[image_flow(paths["logo"], 166, 54)]], colWidths=[186], hAlign="LEFT")
    logo.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(snapshot.get("logo_background", "#151918"))),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 9),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
            ]
        )
    )
    story = [
        Banner(width - 72, paths.get("banner")),
        Spacer(1, 30),
        logo,
        Spacer(1, 26),
        paragraph(snapshot["title"], 28, True, 20),
    ]
    if snapshot.get("company") and snapshot["company"] not in snapshot["title"]:
        story.append(paragraph(snapshot["company"], 15))
    story.append(paragraph("Коммерческое предложение", 12))
    story.append(paragraph("Каждая позиция — отдельный вариант для выбора.", 11))
    for number, item in enumerate(snapshot["items"], 1):
        story.extend(
            [
                ProductStart(item["title"]),
                PageBreak(),
                paragraph(f"{number:02d}  {item['title']}", 18, True),
                paragraph("Артикул: " + item["sku"], 11),
            ]
        )
        path = paths.get(item["main_image"])
        photo = image_flow(path, 190, 230) if path else paragraph("Фотография недоступна", 11)
        metrics = [
            paragraph(money(item["unit_price"]) + " / шт.", 14, True),
            paragraph(f"Тираж: {item['quantity']} шт.", 11),
            paragraph("Стоимость позиции", 10),
            paragraph(money(item["line_amount"]), 18, True),
            paragraph(item["availability"], 11),
        ]
        if item.get("brand"):
            metrics.append(paragraph(item["brand"], 10))
        table = Table([[photo, metrics]], colWidths=[210, width - 282], hAlign="LEFT")
        table.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                    ("TOPPADDING", (0, 0), (-1, -1), 10),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 14),
                ]
            )
        )
        story.append(table)
        thumbnails = [
            image_flow(paths[id], 55, 50)
            for id in item["image_ids"]
            if id in paths and id != item["main_image"]
        ]
        for start in range(0, len(thumbnails), 7):
            story.append(Table([thumbnails[start : start + 7]], hAlign="LEFT"))
        if item["characteristics"]:
            story.append(paragraph("Характеристики", 12, True))
            for attribute in item["characteristics"]:
                story.append(paragraph(attribute["name"] + ": " + attribute["value"], 10, space=4))
        if item["description"]:
            story.append(Spacer(1, 8))
            story.append(paragraph("Описание", 12, True))
            for block in item["description"].split("\n\n"):
                story.append(paragraph(block, 10))
    doc.build(story)


def chunks(value, width=55):
    # Keep whole text readable within Excel's maximum row height, including
    # explicit line breaks. Substrings concatenate to the exact original text.
    result = []
    start = 0
    column = 0
    lines = 1
    for position, char in enumerate(value):
        if char == "\n" or column >= width:
            lines += 1
            column = 0
        if lines > 17:
            result.append(value[start:position])
            start = position
            lines = 1
        column += int(char != "\n")
    result.append(value[start:])
    return result


def render_xlsx(target, snapshot, paths, revision, digest):
    import xlsxwriter

    with xlsxwriter.Workbook(str(target), {"strings_to_formulas": False, "strings_to_urls": False}) as book:
        book.set_properties(
            {
                "title": snapshot["title"],
                "author": "Souvenir Studio",
                "comments": "Revision " + revision + "; snapshot " + digest,
            }
        )
        book.set_custom_property("RevisionId", revision)
        book.set_custom_property("SnapshotHash", digest)
        sheet = book.add_worksheet("Предложение")
        common = {"font_name": "Geist", "font_size": 11, "text_wrap": True, "valign": "top", "locked": True}
        body = book.add_format(common)
        header = book.add_format(common | {"bold": True, "bg_color": "#83FD80", "bottom": 1})
        text_format = book.add_format(common | {"num_format": "@"})
        amount = book.add_format(common | {"num_format": '#,##0.00" ₸"'})
        whole = book.add_format(common | {"num_format": "0"})
        labels = [
            "№",
            "Фото",
            "Артикул",
            "Название",
            "Описание",
            "Характеристики",
            "Цена за единицу (KZT)",
            "Тираж",
            "Стоимость позиции",
            "Наличие",
        ]
        for column, label in enumerate(labels):
            sheet.write_string(0, column, label, header)
        for col, w in enumerate([5, 22, 20, 36, 65, 48, 23, 12, 25, 24]):
            sheet.set_column(col, col, w)
        sheet.freeze_panes(1, 0)
        sheet.set_row(0, 34)
        sheet.protect(options={"select_locked_cells": True, "select_unlocked_cells": False})
        sheet.set_landscape()
        sheet.fit_to_pages(1, 0)
        sheet.repeat_rows(0)
        sheet.set_paper(9)
        images = []
        row = 1
        for number, item in enumerate(snapshot["items"], 1):
            descriptions = chunks(item["description"])
            attributes = chunks("\n".join(a["name"] + ": " + a["value"] for a in item["characteristics"]), 40)
            sheet.set_row(row, 280)
            sheet.write_number(row, 0, number, whole)
            sheet.write_string(row, 2, item["sku"], text_format)
            sheet.write_string(row, 3, item["title"], body)
            sheet.write_string(row, 4, descriptions[0], body)
            sheet.write_string(row, 5, attributes[0], body)
            sheet.write_number(row, 6, float(item["unit_price"]), amount)
            sheet.write_number(row, 7, item["quantity"], whole)
            sheet.write_formula(
                row, 8, f"=ROUND(G{row + 1}*H{row + 1},2)", amount, float(item["line_amount"])
            )
            sheet.write_string(row, 9, item["availability"], body)
            path = paths.get(item["main_image"])
            if path:
                with Image.open(path) as picture:
                    thumb = picture.convert("RGBA")
                    thumb.thumbnail((145, 190))
                data = io.BytesIO()
                thumb.save(data, format="PNG")
                data.seek(0)
                images.append(data)
                sheet.insert_image(
                    row,
                    1,
                    "variant.png",
                    {
                        "image_data": data,
                        "x_offset": 5,
                        "y_offset": 6,
                        "object_position": 1,
                        "description": item["title"],
                    },
                )
            row += 1
            for index in range(1, max(len(descriptions), len(attributes))):
                sheet.set_row(row, 280)
                sheet.write_string(row, 3, item["title"] + " — продолжение", body)
                if index < len(descriptions):
                    sheet.write_string(row, 4, descriptions[index], body)
                if index < len(attributes):
                    sheet.write_string(row, 5, attributes[index], body)
                row += 1
        sheet.autofilter(0, 0, row - 1, 9)
        sheet.print_area(0, 0, row - 1, 9)


def render_offer(root, value, revision, progress=lambda phase: None):
    started = time.perf_counter()
    root = Path(root)
    issues = validate_document(value)
    if issues:
        raise ValueError("Публикация содержит незаполненные обязательные поля")
    folder = safe_path(root, f"documents/{value['document_id']}/revisions/{revision}")
    folder.mkdir(parents=True, exist_ok=True)
    progress("PREPARING_ASSETS")
    preparer = AssetPreparer(root)
    keys = {"logo": value["resolved_logo"]["key"]}
    paths = {"logo": frozen_logo_path(root, value["resolved_logo"])}
    warnings = []
    originals = {}
    assets_started = time.perf_counter()
    if value.get("resolved_banner"):
        banner = value["resolved_banner"]
        path = safe_path(root, banner["key"])
        if hashlib.sha256(path.read_bytes()).hexdigest() != banner["hash"]:
            raise ValueError("Сохранённый баннер повреждён")
        keys["banner"], paths["banner"] = banner["key"], path
    for item in value["items"]:
        for photo in item["photos"]:
            try:
                asset = preparer.prepare(
                    {"image_url": photo["source_url"], "original_key": photo.get("original_key")},
                    original_fallback=True,
                )
                keys[photo["id"]] = asset["key"]
                paths[photo["id"]] = safe_path(root, asset["key"])
                originals[photo["id"]] = {
                    "original_key": asset["original"],
                    "cached_key": asset["key"],
                    "source_hash": asset["hash"],
                }
            except Exception:
                warnings.append(
                    {
                        "item_id": item["id"],
                        "code": "IMAGE_UNAVAILABLE",
                        "message": "Фотография варианта недоступна. Выберите другое подтверждённое фото или обновите данные.",
                    }
                )
    snapshot = client_snapshot(value)
    for item in snapshot["items"]:
        item["image_ids"] = [id for id in item["image_ids"] if id in keys]
        # Keep the requested primary; an error never substitutes a sibling.
    digest = snapshot_hash(
        {
            "document": snapshot,
            "assets": {id: hashlib.sha256(path.read_bytes()).hexdigest() for id, path in paths.items()},
        }
    )
    assets_seconds = time.perf_counter() - assets_started
    progress("RENDERING")
    stamp = time.perf_counter()
    pdf = folder / "offer.pdf"
    render_pdf(pdf, snapshot, paths, revision, digest)
    pdf_seconds = time.perf_counter() - stamp
    stamp = time.perf_counter()
    xlsx = folder / "offer.xlsx"
    render_xlsx(xlsx, snapshot, paths, revision, digest)
    xlsx_seconds = time.perf_counter() - stamp
    if max(pdf.stat().st_size, xlsx.stat().st_size) > 100 * 1024 * 1024:
        raise ValueError("Экспорт превышает допустимый размер")
    progress("VALIDATING")
    stamp = time.perf_counter()
    pages = pdf_previews(root, pdf, folder)
    preview_seconds = time.perf_counter() - stamp

    def relative(p):
        return str(p.relative_to(root)).replace("\\", "/")

    return {
        "status": "READY",
        "client_snapshot": snapshot,
        "snapshot_hash": digest,
        "pdf": relative(pdf),
        "xlsx": relative(xlsx),
        "pdf_sha256": hashlib.sha256(pdf.read_bytes()).hexdigest(),
        "xlsx_sha256": hashlib.sha256(xlsx.read_bytes()).hexdigest(),
        "pages": pages,
        "asset_keys": keys,
        "photo_original_keys": originals,
        "banner": bool(paths.get("banner")),
        "warnings": warnings,
        "timings": {
            "assets": round(assets_seconds, 3),
            "pdf": round(pdf_seconds, 3),
            "xlsx": round(xlsx_seconds, 3),
            "preview": round(preview_seconds, 3),
            "total": round(time.perf_counter() - started, 3),
        },
    }
