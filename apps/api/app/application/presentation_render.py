"""One 16:9 visual system. Vector text; PDFium previews of the actual PDF."""

import hashlib
from collections import OrderedDict
from contextlib import closing
from html import escape
from pathlib import Path

from PIL import Image, ImageStat
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Paragraph

from app.application.presentation_assets import safe_path
from app.domain.presentation import price_label

WIDTH, HEIGHT = 960, 540
FONT_ROOT = Path(__file__).resolve().parents[1] / "assets/fonts"


def fonts():
    for weight in ("Regular", "Bold"):
        name = "Noto" + weight
        if name not in pdfmetrics.getRegisteredFontNames():
            font = TTFont(name, str(FONT_ROOT / f"NotoSans-{weight}.ttf"))
            if 0x20B8 not in font.face.charToGlyph:
                raise ValueError("Font does not support tenge")
            pdfmetrics.registerFont(font)


def rgb(hexvalue):
    return tuple(int(hexvalue[i : i + 2], 16) / 255 for i in (1, 3, 5))


def luminance(value):
    values = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in rgb(value)]
    return sum(v * w for v, w in zip(values, (0.2126, 0.7152, 0.0722), strict=True))


def theme_roles(palette):
    base = palette[0]
    brightness = luminance(base)
    white_ok = 1.05 / (brightness + 0.05) >= 4.5
    dark = "#14252D" if (brightness + 0.05) / (luminance("#14252D") + 0.05) >= 4.5 else "#000000"
    # Neutral support roles are deliberate. Never alter product/logo colors.
    return {
        "cover": base,
        "cover_text": "#FFFFFF" if white_ok else dark,
        "paper": "#F8F7F4",
        "surface": "#E7E9EB",
        "text": "#152B37",
        "muted": "#596A72",
        "accent": base if luminance(base) < 0.38 else "#23414C",
        "secondary": palette[1] if len(palette) > 1 else "#B79B73",
        "line": "#CDD3D5",
    }


def text(canvas, value, x, y, width, height, size=18, color="#152B37", bold=False):
    style = ParagraphStyle(
        "block",
        fontName="NotoBold" if bold else "NotoRegular",
        fontSize=size,
        leading=size * 1.24,
        textColor=colors.HexColor(color),
        splitLongWords=True,
    )
    paragraph = Paragraph(escape(str(value)).replace("\n", "<br/>"), style)
    _, actual = paragraph.wrap(width, height)
    if actual > height + 0.1:
        raise ValueError("Текст не помещается: сократите заголовок или подпись")
    paragraph.drawOn(canvas, x, y - actual)
    return actual


def photo(canvas, path, x, y, width, height):
    with Image.open(path) as image:
        ratio = min(width / image.width, height / image.height)
        w, h = image.width * ratio, image.height * ratio
        canvas.drawImage(
            ImageReader(image), x + (width - w) / 2, y + (height - h) / 2, width=w, height=h, mask="auto"
        )


def logo(canvas, root, settings, x, y):
    if not settings.get("use_logo") or not settings.get("logo"):
        return
    path = safe_path(root, settings["logo"])
    with Image.open(path) as image:
        rgba = image.convert("RGBA")
        sample = Image.new("RGB", rgba.size, "black")
        sample.paste(rgba, mask=rgba.getchannel("A"))
        light = sum(ImageStat.Stat(sample).mean) / 3 > 155
    canvas.setFillColor(colors.HexColor("#1C303A" if light else "#FFFFFF"))
    canvas.roundRect(x - 10, y - 8, 154, 56, 8, stroke=0, fill=1)
    photo(canvas, path, x, y, 134, 40)


def plan_pages(items, settings, category_order):
    grouped = OrderedDict()
    order = category_order or list(dict.fromkeys(i["category_key"] for i in items))
    for category in order:
        group = [i for i in items if i["category_key"] == category]
        if group:
            grouped[category] = group
    pages = []
    if settings["cover"]:
        pages.append({"type": "cover", "items": items[:2]})
    for group in grouped.values():
        while group:
            count = (
                1
                if len(group[0]["display_name"]) > 60 or len(group[0].get("caption", "")) > 65
                else min(2, len(group))
            )
            if count == 2 and (len(group[1]["display_name"]) > 60 or len(group[1].get("caption", "")) > 65):
                count = 1
            pages.append({"type": "hero" if count == 1 else "category", "items": group[:count]})
            group = group[count:]
    if settings.get("ending_note"):
        pages.append({"type": "ending", "items": []})
    return pages


def item_info(item, settings):
    values = [item["brand"]] if settings["show_brand"] and item.get("brand") else []
    values += [
        c["original_color"]
        for c in (item.get("colors") or [])
        if c.get("original_color") and c.get("normalized_color") not in (None, "UNKNOWN")
    ]
    return " · ".join(dict.fromkeys(values))


def render_document(root, revision, input_value, assets, progress=lambda x: None):
    import time

    import pypdfium2 as pdfium

    started = time.perf_counter()
    fonts()
    folder = safe_path(root, f"documents/{input_value['document_id']}/revisions/{revision}")
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / "presentation.pdf"
    settings = input_value["settings"]
    roles = theme_roles(settings["palette"])
    items = input_value["items"]
    pages = plan_pages(items, settings, input_value["category_order"])
    canvas = Canvas(str(target), pagesize=(WIDTH, HEIGHT), pageCompression=1, invariant=1)
    canvas.setTitle(settings["title"])
    canvas.setAuthor("Souvenir Studio")
    placements = []
    for index, page in enumerate(pages):
        dark = page["type"] in ("cover", "ending")
        bg = roles["cover"] if dark else roles["paper"]
        fg = roles["cover_text"] if dark else roles["text"]
        canvas.setFillColor(colors.HexColor(bg))
        canvas.rect(0, 0, WIDTH, HEIGHT, fill=1, stroke=0)
        if page["type"] == "cover":
            logo(canvas, root, settings, 52, 447)
            top = 390 if settings.get("use_logo") and settings.get("logo") else 444
            if settings["client"]:
                text(canvas, settings["client"], 52, top, 490, 56, 18, fg)
                top -= 70
            used = text(canvas, settings["title"], 52, top, 490, 220, 38, fg, True)
            if settings["subtitle"]:
                text(canvas, settings["subtitle"], 52, top - used - 24, 460, 100, 16, fg)
            for offset, item in enumerate(page["items"]):
                x, y = (580, 205) if offset == 0 else (720, 60)
                canvas.setFillColor(colors.HexColor(roles["surface"]))
                canvas.circle(x + 80, y + 95, 115, stroke=0, fill=1)
                photo(canvas, safe_path(root, assets[item["offer_id"]]["key"]), x - 18, y - 10, 195, 230)
        elif page["type"] == "ending":
            text(canvas, settings["ending_note"], 80, 390, 760, 300, 26, fg)
            logo(canvas, root, settings, 80, 70)
        else:
            category = page["items"][0]["category_label"]
            text(canvas, category, 48, 498, 720, 72, 28, roles["accent"], True)
            canvas.setStrokeColor(colors.HexColor(roles["secondary"]))
            canvas.setLineWidth(2)
            canvas.line(48, 408, 912, 408)
            if page["type"] == "hero":
                item = page["items"][0]
                canvas.setFillColor(colors.HexColor(roles["surface"]))
                canvas.roundRect(48, 66, 440, 318, 18, stroke=0, fill=1)
                photo(canvas, safe_path(root, assets[item["offer_id"]]["key"]), 65, 80, 406, 290)
                y = 365
                y -= text(canvas, item["display_name"], 530, y, 370, 180, 24, fg, True) + 16
                if item.get("caption"):
                    y -= text(canvas, item["caption"], 530, y, 370, 92, 13, roles["muted"]) + 12
                if info := item_info(item, settings):
                    y -= text(canvas, info, 530, y, 370, 48, 12, roles["muted"]) + 12
                if y < 120:
                    raise ValueError("Подписи слишком длинные: сократите название или краткую подпись")
                text(
                    canvas,
                    price_label(item["unit_price_kzt"]),
                    530,
                    max(80, y - 8),
                    380,
                    48,
                    22,
                    roles["accent"],
                    True,
                )
            else:
                for column, item in enumerate(page["items"]):
                    x = 48 + column * 448
                    canvas.setFillColor(colors.HexColor(roles["surface"]))
                    canvas.roundRect(x, 186, 416, 202, 14, stroke=0, fill=1)
                    photo(canvas, safe_path(root, assets[item["offer_id"]]["key"]), x + 15, 196, 386, 182)
                    y = 172
                    y -= text(canvas, item["display_name"], x, y, 414, 68, 17, fg, True) + 8
                    if item.get("caption"):
                        y -= text(canvas, item["caption"], x, y, 414, 50, 11, roles["muted"]) + 6
                    if info := item_info(item, settings):
                        y -= text(canvas, info, x, y, 414, 30, 10, roles["muted"]) + 6
                    if y < 65:
                        raise ValueError("Длинные подписи: используйте короткое название")
                    text(
                        canvas, price_label(item["unit_price_kzt"]), x, 52, 416, 30, 19, roles["accent"], True
                    )
            placements.extend(item["offer_id"] for item in page["items"])
        text(canvas, f"{index + 1:02} / {len(pages):02}", 854, 24, 70, 18, 9, fg)
        canvas.showPage()
    canvas.save()
    if len(placements) != len(items) or set(placements) != {i["offer_id"] for i in items}:
        raise ValueError("Нарушен состав документа")
    render_seconds = round(time.perf_counter() - started, 3)
    progress("VALIDATING")
    preview_started = time.perf_counter()
    previews = []
    # PDFium is called only in this sequential subprocess, never API threads.
    with pdfium.PdfDocument(str(target)) as pdf:
        if len(pdf) != len(pages):
            raise ValueError("Количество страниц PDF не совпадает")
        for number in range(len(pdf)):
            with closing(pdf[number]) as page:
                with closing(page.get_textpage()) as textpage:
                    if not textpage.get_text_range().strip():
                        raise ValueError("PDF содержит пустую страницу")
                bitmap = page.render(scale=1.5)
                image = bitmap.to_pil()
                name = f"page-{number + 1}.png"
                image.save(folder / name)
                image.thumbnail((320, 180))
                thumb = f"thumb-{number + 1}.png"
                image.save(folder / thumb)
                bitmap.close()
                previews.append(
                    {
                        "page": number + 1,
                        "preview": str((folder / name).relative_to(root)).replace("\\", "/"),
                        "thumbnail": str((folder / thumb).relative_to(root)).replace("\\", "/"),
                    }
                )
    return {
        "pdf": str(target.relative_to(root)).replace("\\", "/"),
        "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        "bytes": target.stat().st_size,
        "page_count": len(pages),
        "pages": previews,
        "placements": placements,
        "render_seconds": render_seconds,
        "preview_seconds": round(time.perf_counter() - preview_started, 3),
    }
