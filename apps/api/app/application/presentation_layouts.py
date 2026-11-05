"""Composition components shared by three art directions, using one PDF canvas."""

from dataclasses import dataclass
from functools import lru_cache
from math import exp

from PIL import Image, ImageStat
from reportlab.lib import colors
from reportlab.lib.utils import ImageReader

from app.application.presentation_assets import safe_path
from app.application.presentation_render import item_info, logo, luminance, photo, text
from app.domain.presentation import price_label


@dataclass(frozen=True)
class ArtDirection:
    heading: int
    title: int
    cover_title: int
    folio: int
    motif: str


DIRECTIONS = {
    "editorial": ArtDirection(38, 25, 40, 12, "rule"),
    "premium-dark": ArtDirection(30, 24, 36, 10, "disc"),
    "modern-graphic": ArtDirection(34, 25, 38, 15, "plane"),
}

# Distinct geometry, shared drawing/measurement and commercial-data binding.
VARIANT_SLOTS = {
    "editorial": [(48, 145, 320, 230), (401, 166, 220, 192), (665, 156, 230, 218)],
    "premium-dark": [(70, 174, 236, 204), (362, 174, 236, 204), (654, 174, 236, 204)],
    "modern-graphic": [(61, 167, 230, 207), (365, 190, 230, 186), (669, 167, 230, 207)],
}


def blend(first, second, weight):
    a, b = colors.HexColor(first), colors.HexColor(second)
    return "#" + "".join(
        f"{round((x * (1 - weight) + y * weight) * 255):02X}" for x, y in zip(a.rgb(), b.rgb(), strict=True)
    )


@lru_cache(maxsize=16)
def illuminated_background(base, secondary, cover):
    """A bounded, deterministic light field. Never touches product pixels."""
    # Premium Dark stays dark even when the client's first palette slot is pale.
    # Preserve its hue rather than placing white type on a bright light field.
    for _ in range(16):
        if luminance(base) <= 0.05:
            break
        base = blend(base, "#061019", 0.2)
    dark = colors.HexColor(blend(base, "#03090D", 0.77)).rgb()
    light = colors.HexColor(blend(base, "#B9CCD1", 0.29)).rgb()
    warm = colors.HexColor(blend(base, secondary, 0.3)).rgb()
    width, height = 480, 270
    image = Image.new("RGB", (width, height))
    pixels = []
    for y in range(height):
        for x in range(width):
            u, v = x / width, y / height
            main = 0.87 * exp(
                -(((u - (0.72 if cover else 0.53)) / 0.44) ** 2 + ((v - 0.51) / 0.61) ** 2) * 1.7
            )
            edge = 0.12 * exp(-(((u - 0.96) / 0.43) ** 2 + ((v - 0.9) / 0.6) ** 2) * 2)
            pixels.append(
                tuple(
                    round(255 * (d * (1 - main - edge) + lit * main + w * edge))
                    for d, lit, w in zip(dark, light, warm, strict=True)
                )
            )
    image.putdata(pixels)
    return image


@lru_cache(maxsize=24)
def palette_background(base, peak, cover):
    """New role-based light field; legacy blue-grey V3 function stays unchanged."""
    dark, light = colors.HexColor(base).rgb(), colors.HexColor(peak).rgb()
    image = Image.new("RGB", (480, 270))
    center = {"centered": 0.5, "split": 0.78, "band": 0.22, "arch": 0.85, "open": 0.26}.get(
        cover, 0.72 if cover else 0.53
    )
    image.putdata(
        [
            tuple(round(255 * (a * (1 - t) + b * t)) for a, b in zip(dark, light, strict=True))
            for y in range(270)
            for x in range(480)
            for t in [0.88 * exp(-(((x / 480 - center) / 0.46) ** 2 + ((y / 270 - 0.51) / 0.64) ** 2) * 1.6)]
        ]
    )
    return image


class PagePainter:
    def __init__(self, canvas, root, settings, roles, assets, style, version=2):
        self.c, self.root, self.settings, self.roles, self.assets = canvas, root, settings, roles, assets
        self.style, self.tokens = style, DIRECTIONS[style]
        self.refined = style == "premium-dark" and version == 3
        self.material_layers = None
        self.decoration_layers = None
        self.enhanced = bool(settings.get("palette_plan"))

    def box(self, x, y, w, h, color):
        self.c.setFillColor(colors.HexColor(color))
        self.c.rect(x, y, w, h, stroke=0, fill=1)
        if self.enhanced:
            from app.application.presentation_contrast import fill_background

            fill_background(self.c, (x, y, w, h), color)

    def line(self, x1, y1, x2, y2, color, width=1):
        self.c.setStrokeColor(colors.HexColor(color))
        self.c.setLineWidth(width)
        self.c.line(x1, y1, x2, y2)

    def background(self, page, number):
        if self.refined:
            self.illuminated_background(page)
            return
        dark = page.layout in ("cover", "title", "ending") or self.style == "premium-dark" and number % 2 == 1
        if self.style == "modern-graphic" and page.layout in ("cover", "title"):
            dark = False
        self.fg = (
            self.roles["cover_text"]
            if dark and page.layout in ("cover", "title", "ending")
            else "#FFFFFF"
            if dark
            else self.roles["text"]
        )
        self.muted = "#C2CDD1" if dark else self.roles["muted"]
        self.accent = self.fg if dark else self.roles["accent"]
        if self.enhanced:
            self.fg = self.roles["dark_text" if dark else "light_text"]
            self.muted = self.roles["dark_muted" if dark else "light_muted"]
            self.accent = self.roles["dark_accent" if dark else "light_accent"]
        self.dark = dark
        bg = (
            self.roles["cover"]
            if page.layout in ("cover", "title", "ending")
            else "#13252F"
            if dark
            else self.roles["paper"]
        )
        if self.style == "modern-graphic" and page.layout in ("cover", "title"):
            bg = self.roles["paper"]
        if self.enhanced:
            if dark:
                bg = self.roles["dark_background"]
            self.c._presentation_background = Image.new("RGB", (960, 540), bg)
            self.c._presentation_protection = bg
        self.box(0, 0, 960, 540, bg)
        if self.style == "editorial":
            self.box(0, 0, 12, 540, self.roles["secondary"])
        elif self.style == "premium-dark":
            self.line(42, 40, 918, 40, "#81949E" if dark else self.roles["line"], 0.5)
        else:
            self.box(0, 0, 26, 540, self.roles["accent"])
            self.box(26, 506, 100, 34, self.roles["secondary"])
            self.line(48, 38, 912, 38, self.roles["line"])
        if page.motif == "frame":
            self.line(30, 66, 30, 475, self.roles["secondary"], 2)
            self.line(930, 66, 930, 475, self.roles["secondary"], 2)
        elif page.motif == "disc":
            self.c.setFillColor(colors.HexColor(self.roles["secondary"]))
            self.c.circle(902, 475, 8, stroke=0, fill=1)
        elif page.motif == "plane":
            self.box(916, 76, 10, 320, self.roles["secondary"])

    def illuminated_background(self, page):
        self.dark, self.fg, self.muted = True, "#F5F4EF", "#B6C2C6"
        self.accent = self.roles["secondary"]
        if luminance(self.accent) < 0.3:
            self.accent = blend(self.accent, "#FFFFFF", 0.65)
        if self.enhanced:
            self.fg, self.muted, self.accent = (
                self.roles[k] for k in ("dark_text", "dark_muted", "dark_accent")
            )
            image = palette_background(
                self.roles["dark_background"],
                self.roles["dark_peak"],
                page.title_family if page.layout == "title" else page.layout == "cover",
            )
            self.c._presentation_background = image.resize((960, 540))
            self.c._presentation_protection = self.roles["dark_background"]
        else:
            image = illuminated_background(
                self.roles["cover"], self.roles["secondary"], page.layout == "cover"
            )
        self.c.drawImage(ImageReader(image), 0, 0, width=960, height=540)
        # One family of quiet contour arcs, confined to the image field.
        # Neither model name nor prices have decoration behind them.
        if (
            page.motif != "none"
            and not (self.material_layers and self.material_layers.suppress_motif(self.page_number))
            and not (self.decoration_layers and self.decoration_layers.plan.enabled)
        ):
            self.c.saveState()
            clip = self.c.beginPath()
            clip.rect(
                425 if page.layout == "cover" else 50,
                157,
                485 if page.layout == "cover" else 860,
                312 if page.layout == "cover" else 248,
            )
            self.c.clipPath(clip, stroke=0, fill=0)
            self.c.setStrokeColor(colors.HexColor(blend(self.roles["cover"], self.accent, 0.14)))
            self.c.setLineWidth(0.45)
            for n in range(7):
                self.c.ellipse(510 + n * 17, 160 - n * 18, 1060 + n * 18, 655 + n * 18, fill=0, stroke=1)
            self.c.restoreState()
        self.line(56, 44, 904, 44, blend(self.roles["cover"], "#FFFFFF", 0.19), 0.45)

    def object(self, item, rect):
        if self.settings.get("cover_policy"):
            self.component("product_photo", item["offer_id"], rect)
        x, y, w, h = rect
        path = safe_path(self.root, self.assets[item["offer_id"]]["key"])
        if self.refined or self.enhanced or self.decoration_layers and self.decoration_layers.plan.enabled:
            photo(self.c, path, x, y, w, h, optical=self.refined)
            return
        with Image.open(path) as source:
            sample = source.convert("RGBA")
            sample.thumbnail((64, 64))
            tone = sum(ImageStat.Stat(sample.convert("RGB"), mask=sample.getchannel("A")).mean) / 3
        # Support only objects which would disappear; no mandatory product cards.
        if self.dark and tone < 120 or not self.dark and tone > 210:
            self.c.setFillColor(colors.HexColor("#E2E7E5" if self.dark else "#E9EBE8"))
            self.c.ellipse(x + w * 0.1, y + h * 0.03, x + w * 0.9, y + h * 0.97, fill=1, stroke=0)
        photo(self.c, path, x, y, w, h)

    def caption(self, item, x, top, w, floor=90, size=17, price_y=76, name=True, compact=False):
        if self.settings.get("cover_policy"):
            self.component("product_caption_price", item["offer_id"], (x, price_y, w, top - price_y))
        if compact:
            color = item_info(item, self.settings | {"show_brand": False})
            y = top - text(self.c, color[:1].upper() + color[1:], x, top, w, 31, 12, self.fg) - 6
            if self.settings.get("show_brand") and item.get("brand"):
                y -= text(self.c, item["brand"], x, y, w, 27, 10, self.muted) + 10
            if y < 72:
                raise ValueError("Подпись варианта не помещается: выберите одиночную композицию")
            text(self.c, price_label(item["unit_price_kzt"]), x, y, w, 27, 18, self.fg, True)
            return
        y = top
        if name:
            y -= text(self.c, item["display_name"], x, y, w, min(160, top - floor), size, self.fg, True) + 8
        if item.get("caption"):
            y -= text(self.c, item["caption"], x, y, w, 62, 12, self.muted) + 8
        if info := item_info(item, self.settings):
            y -= text(self.c, info, x, y, w, 44, 11, self.muted) + 8
        if y < floor:
            raise ValueError("Подписи не помещаются: сократите подпись или выберите одиночную композицию")
        text(self.c, price_label(item["unit_price_kzt"]), x, price_y, w, 32, 20, self.accent, True)

    def hero(self, items):
        if self.settings.get("cover_policy") and self.variant == 1:
            self.object(items[0], (50, 90, 410, 330))
            self.caption(items[0], 520, 382, 380, 132, self.tokens.title, 91)
            return
        if self.style == "premium-dark":
            self.object(items[0], (493, 82, 410, 340))
            self.caption(items[0], 52, 382, 380, 132, self.tokens.title, 91)
        else:
            if self.style == "modern-graphic":
                self.box(48, 110, 82, 260, self.roles["secondary"])
            self.object(items[0], (50, 92, 426, 330))
            self.caption(items[0], 530, 392, 374, 132, self.tokens.title, 91)

    def asymmetric(self, items):
        self.object(items[0], (48, 202, 465, 222))
        self.caption(items[0], 48, 178, 442)
        self.object(items[1], (576, 282, 296, 144))
        self.line(553, 80, 553, 420, self.roles["secondary"])
        self.caption(items[1], 580, 253, 320)

    def category(self, items):
        width = (864 - 32 * (len(items) - 1)) / len(items)
        for n, item in enumerate(items):
            x = 48 + n * (width + 32)
            self.object(item, (x + 8, 212, width - 16, 210))
            self.caption(item, x, 198, width)

    def variants(self, items, image_top=404):
        if self.refined:
            width = 864 / len(items)
            for n, item in enumerate(items):
                x = 48 + n * width
                text(self.c, f"{n + 1:02}", x + 12, image_top - 18, 30, 22, 11, self.accent)
                bottom = 166 if self.settings.get("cover_policy") and self.variant == 2 else 151
                self.object(item, (x + 35, bottom, width - 70, min(253, image_top - bottom)))
                self.caption(item, x + 35, 132, width - 60, name=False, compact=True)
            return
        first = items[0]
        text(self.c, first["display_name"], 48, 422, 830, 64, 23, self.fg, True)
        width = 864 / len(items)
        for n, item in enumerate(items):
            x = 48 + n * width
            rect = VARIANT_SLOTS[self.style][n] if len(items) == 3 else (x + 12, 148, width - 24, 206)
            self.object(item, rect)
            # Variant-specific color and price stay separate, even with a common name.
            self.caption(item, x + 12, 127, width - 24, 90, name=False)

    def lineup(self, items):
        width = 864 / 3
        for n, item in enumerate(items):
            x = 48 + n * width
            if self.style == "modern-graphic":
                self.box(x + 12, 207, width - 24, 10 + 12 * n, self.roles["secondary"])
            self.object(item, (x + 16, 232, width - 32, 178))
            self.caption(item, x + 10, 193, width - 20)

    def cover(self, items):
        company = self.settings.get("client", "").strip()
        heading = company or "Каталог\nвыбранных товаров"
        if self.refined:
            self.collection_cover(items, company)
            return
        # A legacy title is retained internally; a new document never requires one.
        if self.style == "modern-graphic":
            self.box(26, 345, 934, 195, self.roles["accent"])
            text(self.c, heading, 55, 477, 650, 130, 32, "#FFFFFF", True)
            if self.settings.get("subtitle"):
                text(self.c, self.settings["subtitle"], 55, 326, 830, 58, 16, self.fg)
            for n, item in enumerate(items):
                self.object(item, (52 + n * 300, 66, 252, 195))
            logo(self.c, self.root, self.settings, 770, 441)
        elif self.style == "editorial":
            logo(self.c, self.root, self.settings, 55, 449)
            top = 370 if self.settings.get("use_logo") and self.settings.get("logo") else 449
            used = text(
                self.c,
                heading,
                55,
                top,
                470,
                230,
                30 if len(heading) > 60 else self.tokens.cover_title,
                self.fg,
                True,
            )
            if self.settings.get("subtitle"):
                text(self.c, self.settings["subtitle"], 55, top - used - 28, 435, 100, 16, self.fg)
            for n, item in enumerate(items[:2]):
                self.object(item, (555 + 160 * n, 196 - 126 * n, 190, 270))
        else:
            logo(self.c, self.root, self.settings, 52, 449)
            top = 370 if self.settings.get("use_logo") and self.settings.get("logo") else 454
            used = text(
                self.c,
                heading.replace("\n", " "),
                52,
                top,
                850,
                140,
                28 if len(heading) > 60 else 36,
                self.fg,
                True,
            )
            if self.settings.get("subtitle"):
                text(self.c, self.settings["subtitle"], 52, top - used - 22, 850, 80, 15, self.fg)
            for n, item in enumerate(items):
                self.object(item, (75 + n * 295, 50, 230, 200))

    def collection_cover(self, items, company):
        from app.domain.presentation_design import same_model

        text(self.c, "ПРОЕКТНАЯ ПОДБОРКА", 56, 487, 370, 24, 10, self.accent)
        has_logo = self.settings.get("use_logo") and self.settings.get("logo")
        if has_logo:
            logo(self.c, self.root, self.settings, 66, 416)
        heading = company or "Каталог\nвыбранных\nтоваров"
        top = 381 if has_logo else 418
        height = 114 if self.settings.get("subtitle") else 156
        for size in (37, 32, 28, 24, 22):
            try:
                used = text(self.c, heading, 56, top, 358, height, size, self.fg, True)
                break
            except ValueError:
                if size == 22:
                    raise
        if self.settings.get("subtitle"):
            text(self.c, self.settings["subtitle"], 56, top - used - 24, 350, 68, 12, self.muted)
        self.line(56, 173, 105, 173, self.accent, 1.2)
        label = items[0]["display_name"] if same_model(items) else "Выбранные товары"
        text(self.c, label, 56, 150, 345, 55, 13, self.fg)
        # Supporting variants form a deliberate depth hierarchy, rather than
        # repeating the equal-size comparison row on the cover.
        if len(items) > 1:
            self.object(items[1], (429, 100, 142, 205))
        if len(items) > 2:
            self.object(items[2], (800, 92, 116, 172))
        self.object(items[0], (550, 93, 274, 372))

    def component(self, kind, ref, rect):
        if not hasattr(self.c, "_presentation_components"):
            self.c._presentation_components = []
        self.c._presentation_components.append(
            {"page": self.page_number + 1, "component": kind, "ref": ref, "rect": rect}
        )

    def title(self, page, items):
        from app.application.presentation_logo import frozen_logo_path
        from app.domain.presentation_composition import TITLE_GEOMETRY

        if items or page.product_refs or not self.settings.get("cover_policy"):
            raise ValueError("Титул не может содержать выбранные товары")
        geometry = TITLE_GEOMETRY[page.title_family]
        if not (self.material_layers and self.material_layers.suppress_motif(self.page_number)):
            self.c.saveState()
            self.c.setStrokeColor(colors.HexColor(self.accent))
            self.c.setStrokeAlpha(0.32)
            self.c.setLineWidth(1.3)
            graphic = geometry["graphic"]
            if graphic == "orbit":
                for n in range(3):
                    self.c.ellipse(650 + n * 24, 105 + n * 25, 895 - n * 8, 440 - n * 25, stroke=1, fill=0)
            elif graphic == "horizon":
                self.line(300, 85, 660, 85, self.accent, 1.2)
                self.line(390, 74, 570, 74, self.accent, 0.6)
            elif graphic == "band":
                self.box(48, 72, 68, 398, blend(self.roles["cover"], self.accent, 0.23))
                self.line(134, 72, 134, 470, self.accent, 1)
            elif graphic == "arch":
                for n in range(4):
                    path = self.c.beginPath()
                    path.moveTo(690 + n * 16, 80)
                    path.curveTo(640 + n * 16, 360, 820 + n * 12, 505, 920, 450 - n * 20)
                    self.c.drawPath(path)
            else:
                for n in range(3):
                    self.line(70, 125 + n * 55, 275 - n * 42, 125 + n * 55, self.accent, 2)
            self.c.restoreState()
        resolved = self.settings["resolved_logo"]
        if page.logo_ref != resolved["asset_id"]:
            raise ValueError("Логотип титула не соответствует сохранённому плану")
        path = frozen_logo_path(self.root, resolved)
        x, y, w, h = geometry["logo"]
        with Image.open(path) as image:
            rgba = image.convert("RGBA")
            light = sum(ImageStat.Stat(rgba.convert("RGB"), mask=rgba.getchannel("A")).mean) / 3 > 155
        backing = "#19232A" if light else "#FFFFFF"
        self.box(x - 12, y - 10, w + 24, h + 20, backing)
        photo(self.c, path, x, y, w, h, optical=True)
        self.component("title_logo", resolved["asset_id"], (x, y, w, h))
        company = self.settings.get("client", "").strip()
        heading = "Подборка для\n" + company if company else "Каталог выбранных товаров"
        x, top, w, h = geometry["heading"]
        for size in (38, 34, 30, 26, 24):
            try:
                text(self.c, heading, x, top, w, h, size, self.fg, True, geometry["alignment"])
                break
            except ValueError:
                if size == 24:
                    raise
        if self.settings.get("subtitle"):
            x, top, w, h = geometry["subtitle"]
            text(
                self.c, self.settings["subtitle"], x, top, w, h, 15, self.muted, False, geometry["alignment"]
            )

    def paint(self, page, items, index, total):
        self.page_number = index
        self.variant = page.variant
        self.background(page, index)
        if self.material_layers:
            self.material_layers.paint(self.c, index)
        if self.decoration_layers:
            self.decoration_layers.paint(self.c, index, self.accent)
        if page.layout == "title":
            self.title(page, items)
        elif page.layout == "cover":
            self.cover(items)
        elif page.layout == "ending":
            text(self.c, self.settings["ending_note"], 70, 402, 800, 300, 26, self.fg)
            logo(self.c, self.root, self.settings, 70, 70)
        else:
            title = page.heading or items[0]["category_label"]
            if self.refined and page.layout == "variants":
                text(self.c, title.upper(), 56, 493, 600, 24, 10, self.accent)
                name = items[0]["display_name"]
                used = text(self.c, name, 56, 466, 810, 60, 28 if len(name) < 56 else 24, self.fg, True)
                self.line(56, 466 - used - 16, 105, 466 - used - 16, self.accent, 1.2)
                self.variants(items, image_top=min(404, 466 - used - 28))
                text(self.c, f"{index + 1:02} / {total:02}", 844, 26, 86, 20, 10, self.muted)
                return
            text(self.c, title, 48, 496, 740, 65, self.tokens.heading, self.accent, True)
            if self.style == "editorial":
                self.line(48, 435, 912, 435, self.roles["secondary"], 1.3)
            elif self.style == "modern-graphic":
                text(self.c, f"{index + 1:02}", 835, 493, 82, 55, 38, self.accent, True)
            else:
                self.line(48, 435, 912, 435, self.roles["secondary"], 0.5)
            getattr(self, page.layout)(items)
        text(self.c, f"{index + 1:02} / {total:02}", 844, 26, 86, 20, self.tokens.folio, self.fg)
