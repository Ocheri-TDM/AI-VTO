"""One 16:9 visual system. Vector text; PDFium previews of the actual PDF."""

import hashlib
import io
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
        "accent": base if luminance(base) <= 0.16 else "#23414C",
        "secondary": palette[1] if len(palette) > 1 else "#B79B73",
        "line": "#CDD3D5",
    }


def text(canvas, value, x, y, width, height, size=18, color="#152B37", bold=False, alignment="left"):
    style = ParagraphStyle(
        "block",
        fontName="NotoBold" if bold else "NotoRegular",
        fontSize=size,
        leading=size * 1.24,
        textColor=colors.HexColor(color),
        splitLongWords=True,
        alignment=1 if alignment == "center" else 0,
    )
    paragraph = Paragraph(escape(str(value)).replace("\n", "<br/>"), style)
    _, actual = paragraph.wrap(width, height)
    if actual > height + 0.1:
        raise ValueError("Текст не помещается: сократите заголовок или подпись")
    if hasattr(canvas, "_presentation_bounds") and actual:
        canvas._presentation_bounds.append(
            {
                "kind": "text",
                "rect": (x, y - actual, width, actual),
                "color": color,
                "size": size,
                "bold": bold,
            }
        )
    if actual and getattr(canvas, "_presentation_background", None) is not None:
        from app.application.presentation_contrast import protect_text

        protect_text(canvas, (x, y - actual, width, actual), color, size, bold)
    paragraph.drawOn(canvas, x, y - actual)
    return actual


def photo(canvas, path, x, y, width, height, *, optical=False):
    with Image.open(path) as image:
        if optical and image.mode == "RGBA":
            # Crop transparent margins only. Keep every nonzero alpha pixel,
            # including faint straps, highlights and original photographic edges.
            bounds = image.getchannel("A").getbbox()
            if bounds is None:
                raise ValueError("Изображение не содержит видимых пикселей")
            image = image.crop(bounds)
        ratio = min(width / image.width, height / image.height)
        w, h = image.width * ratio, image.height * ratio
        if hasattr(canvas, "_presentation_bounds"):
            canvas._presentation_bounds.append(
                {"kind": "photo", "rect": (x + (width - w) / 2, y + (height - h) / 2, w, h)}
            )
        canvas.drawImage(
            ImageReader(image), x + (width - w) / 2, y + (height - h) / 2, width=w, height=h, mask="auto"
        )


def logo(canvas, root, settings, x, y):
    if not settings.get("use_logo") or not settings.get("logo"):
        return
    if hasattr(canvas, "_presentation_bounds"):
        canvas._presentation_bounds.append({"kind": "logo", "rect": (x - 10, y - 8, 154, 56)})
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
    from app.domain.presentation_design import deterministic_plan

    by_id = {i["offer_id"]: i for i in items}
    plan = deterministic_plan(items, settings, category_order)
    return [{"type": p.layout, "items": [by_id[id] for id in p.product_refs]} for p in plan.pages]


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
    from app.application.presentation_layouts import PagePainter
    from app.domain.presentation_composition import novelty
    from app.domain.presentation_design import deterministic_plan, validate_plan

    materials = input_value.get("materials", [])
    plan = (
        validate_plan(input_value["design_plan"], items, settings, materials)
        if input_value.get("design_plan")
        else deterministic_plan(items, settings, input_value["category_order"], materials=materials)
    )
    pages = plan.pages
    roles = theme_roles([settings["palette"][n] for n in plan.palette_order])
    if settings.get("palette_plan"):
        from app.domain.presentation_palette import painter_roles

        roles = painter_roles(settings["palette_plan"])
    canvas = Canvas(str(target), pagesize=(WIDTH, HEIGHT), pageCompression=1, invariant=1)
    canvas.setTitle(settings.get("title") or "Каталог выбранных товаров")
    canvas.setAuthor("Souvenir Studio")
    placements = []
    painter = PagePainter(canvas, root, settings, roles, assets, plan.style, plan.style_version)
    from app.application.presentation_decoration import DecorationLayers

    decoration = DecorationLayers(plan.decoration, roles) if plan.decoration else None
    painter.decoration_layers = decoration
    from app.application.presentation_material_layout import MaterialLayers

    layers = MaterialLayers(root, materials, roles) if materials else None
    by_id = {i["offer_id"]: i for i in items}
    for index, page in enumerate(pages):
        group = [by_id[id] for id in page.product_refs]
        if decoration or settings.get("palette_plan") or layers and any(p.materials for p in pages):
            measuring = Canvas(io.BytesIO(), pagesize=(WIDTH, HEIGHT), invariant=1)
            measuring._presentation_bounds = []
            PagePainter(measuring, root, settings, roles, assets, plan.style, plan.style_version).paint(
                page, group, index, len(pages)
            )
            if layers:
                layers.resolve(page, index, measuring._presentation_bounds)
            if decoration:
                decoration.resolve(
                    page,
                    index,
                    measuring._presentation_bounds,
                    layers.resolved.get(index, []) if layers else [],
                )
            del measuring
            painter.material_layers = layers
        painter.paint(page, group, index, len(pages))
        if page.layout not in ("cover", "title", "ending"):
            placements.extend(page.product_refs)
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
        "design_plan": plan.model_dump(),
        "render_seconds": render_seconds,
        "preview_seconds": round(time.perf_counter() - preview_started, 3),
        **(
            {
                "resolved_logo": settings["resolved_logo"],
                "design_signature": novelty(plan, input_value.get("recent_designs", [])),
                "component_trace": getattr(canvas, "_presentation_components", []),
            }
            if settings.get("cover_policy")
            else {}
        ),
        **(
            {
                "palette_plan": settings["palette_plan"],
                "contrast_audit": getattr(canvas, "_presentation_contrast", []),
            }
            if settings.get("palette_plan")
            else {}
        ),
        **({"decoration_plan": decoration.result()} if decoration else {}),
        **(
            {
                "material_usage": layers.results(),
                "material_issues": layers.issues(),
                "material_reservations": {str(n + 1): boxes for n, boxes in layers.reservations.items()},
                "material_schema_version": 1,
            }
            if layers
            else {}
        ),
    }
