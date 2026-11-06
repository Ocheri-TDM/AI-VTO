"""Audit actual background composites beneath measured text; local protection only."""

from PIL import ImageDraw
from reportlab.lib import colors

from app.domain.presentation_palette import contrast


def fill_background(canvas, rect, color):
    image = getattr(canvas, "_presentation_background", None)
    if image is not None:
        x, y, w, h = rect
        ImageDraw.Draw(image).rectangle(
            (round(x), round(540 - y - h), round(x + w), round(540 - y)), fill=color
        )


def protect_text(canvas, rect, color, size, bold):
    x, y, w, h = rect
    region = (max(0, round(x)), max(0, round(540 - y - h)), min(960, round(x + w)), min(540, round(540 - y)))

    def score():
        image = canvas._presentation_background.crop(region).convert("RGB")
        # Check every distinct composite background color, not page averages.
        values = image.getcolors(maxcolors=image.width * image.height) or []
        return min((contrast(color, "#" + "".join(f"{v:02X}" for v in rgb)) for _, rgb in values), default=21)

    required = 3.0 if size >= 18 or bold and size >= 14 else 4.5
    before = score()
    if before < required:
        bg = canvas._presentation_protection
        if contrast(color, bg) < required:
            bg = "#000000" if contrast(color, "#000000") >= required else "#FFFFFF"
        canvas.saveState()
        canvas.setFillColor(colors.HexColor(bg))
        canvas.rect(x - 3, y - 3, w + 6, h + 6, fill=1, stroke=0)
        canvas.restoreState()
        fill_background(canvas, (x - 3, y - 3, w + 6, h + 6), bg)
    after = score()
    if after < required:
        raise ValueError("Не удалось обеспечить читаемость текста")
    if not hasattr(canvas, "_presentation_contrast"):
        canvas._presentation_contrast = []
    canvas._presentation_contrast.append(
        {
            "page": canvas._pageNumber,
            "rect": rect,
            "color": color,
            "size": size,
            "bold": bold,
            "required": required,
            "before": round(before, 4),
            "after": round(after, 4),
            "protected": before < required,
        }
    )
