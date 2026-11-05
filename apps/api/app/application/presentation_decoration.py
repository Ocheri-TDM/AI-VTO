"""Measured free-space decoration. The same closed primitives serve every style."""

from reportlab.lib import colors

from app.application.presentation_material_layout import intersects


class DecorationLayers:
    def __init__(self, plan, roles):
        self.plan, self.roles, self.pages = plan, roles, []

    def resolve(self, page, number, protected, custom):
        reserved = [b["rect"] for b in protected] + [(0, 0, 960, 54)]
        result = {
            "page": number + 1,
            "family": self.plan.family,
            "intensity": self.plan.intensity,
            "allowed_regions": [],
            "elements": [],
            "reason": None,
        }
        self.pages.append(result)
        if not self.plan.enabled:
            result["reason"] = "DISABLED"
            return
        if custom:
            result["reason"] = "CUSTOM_MATERIAL_PRIORITY"
            return
        # Shared measured corridors, not randomized positions over content.
        regions = [
            (24, 96, 14, 306),
            (924, 106, 14, 294),
            (724, 484, 176, 25),
            (448, 62, 180, 12),
            (440, 486, 150, 22),
        ]
        free = [r for r in regions if not any(intersects(r, b, padding=6) for b in reserved)]
        result["allowed_regions"] = free
        if not free:
            result["reason"] = "NO_SAFE_SPACE"
            return
        offset = (self.plan.seed + number * 2 + int(page.layout == "cover")) % len(free)
        free = free[offset:] + free[:offset]
        count = {"SUBTLE": 1, "NORMAL": 2, "BOLD": 3}[self.plan.intensity]
        for n, rect in enumerate(free[:count]):
            result["elements"].append(
                {
                    "rect": rect,
                    "family": self.plan.family,
                    "density": 3 + (self.plan.seed + number + n) % 3,
                    "variation": (self.plan.seed * 3 + number + n) % 4,
                }
            )

    def paint(self, canvas, number, ink):
        page = self.pages[number]
        canvas.saveState()
        alpha = {"SUBTLE": 0.22, "NORMAL": 0.36, "BOLD": 0.55}[self.plan.intensity]
        canvas.setStrokeAlpha(alpha)
        canvas.setFillAlpha(alpha)
        canvas.setStrokeColor(colors.HexColor(ink))
        canvas.setFillColor(colors.HexColor(ink))
        canvas.setLineWidth(0.7 if self.plan.intensity == "SUBTLE" else 1)
        for item in page["elements"]:
            x, y, w, h = item["rect"]
            family, density = item["family"], item["density"]
            canvas.saveState()
            clip = canvas.beginPath()
            clip.rect(x, y, w, h)
            canvas.clipPath(clip, stroke=0, fill=0)
            if family == "corners":
                path = canvas.beginPath()
                path.moveTo(x + w * 0.75, y + 2)
                path.lineTo(x + 2, y + 2)
                path.lineTo(x + 2, y + h * 0.8)
                path.moveTo(x + w * 0.3, y + h - 2)
                path.lineTo(x + w - 2, y + h - 2)
                path.lineTo(x + w - 2, y + h * 0.2)
                canvas.drawPath(path)
            elif family in ("waves", "ribbons"):
                for n in range(density):
                    path = canvas.beginPath()
                    if w > h:
                        yy = y + h * (n + 1) / (density + 1)
                        path.moveTo(x, yy)
                        path.curveTo(x + w * 0.3, yy + h * 0.35, x + w * 0.6, yy - h * 0.35, x + w, yy)
                    else:
                        xx = x + w * (n + 1) / (density + 1)
                        path.moveTo(xx, y)
                        path.curveTo(xx + w * 0.35, y + h * 0.3, xx - w * 0.35, y + h * 0.6, xx, y + h)
                    canvas.drawPath(path)
            elif family == "grid":
                for n in range(density + 1):
                    xx = x + w * n / density
                    canvas.line(xx, y + 1, xx, y + h - 1)
                for n in range(1, density * 2):
                    yy = y + h * n / (density * 2)
                    canvas.line(x + 1, yy, x + w - 1, yy)
            elif family == "dots":
                for n in range(density * 3):
                    t = (n + 0.5) / (density * 3)
                    canvas.circle(
                        x + w * t if w > h else x + w / 2,
                        y + h / 2 if w > h else y + h * t,
                        1.2,
                        stroke=0,
                        fill=1,
                    )
            else:
                for n in range(density):
                    inset = (n + 1) * min(w, h) / (density + 1)
                    path = canvas.beginPath()
                    if w > h:
                        path.moveTo(x, y + inset)
                        path.curveTo(
                            x + w * 0.35,
                            y + inset,
                            x + w * 0.65,
                            y + h - inset * 0.5,
                            x + w,
                            y + h - inset * 0.5,
                        )
                    else:
                        path.moveTo(x + inset, y)
                        path.curveTo(
                            x + inset,
                            y + h * 0.35,
                            x + w - inset * 0.5,
                            y + h * 0.65,
                            x + w - inset * 0.5,
                            y + h,
                        )
                    canvas.drawPath(path)
            canvas.restoreState()
        canvas.restoreState()

    def result(self):
        return self.plan.model_dump() | {"pages": self.pages}
