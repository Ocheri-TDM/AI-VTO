"""Measured page reservations and deterministic, role-specific custom layers."""

import hashlib

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps, ImageStat
from reportlab.lib.utils import ImageReader

from app.application.presentation_assets import safe_path
from app.domain.presentation_materials import on_page


def intersects(a, b, padding=6):
    x, y, w, h = a
    bx, by, bw, bh = b
    return x < bx + bw + padding and x + w + padding > bx and y < by + bh + padding and y + h + padding > by


def fitted(image, size, crop, focal, position="AUTO"):
    if crop:
        return ImageOps.fit(image, size, Image.Resampling.LANCZOS, centering=focal)
    inside = ImageOps.contain(image, size, Image.Resampling.LANCZOS)
    result = Image.new("RGBA", size)
    x, y = (size[0] - inside.width) // 2, (size[1] - inside.height) // 2
    if position == "LEFT":
        x = 0
    elif position == "RIGHT":
        x = size[0] - inside.width
    elif position == "TOP":
        y = 0
    elif position == "BOTTOM":
        y = size[1] - inside.height
    result.alpha_composite(inside, (x, y))
    return result


class MaterialLayers:
    def __init__(self, root, materials, roles):
        self.roles = roles
        self.materials = {m["id"]: m for m in materials}
        self.images, self.usage, self.resolved, self.reservations = {}, {}, {}, {}
        self.last = {}
        for material in materials:
            status, reason = "SKIPPED", "Материал не выбран планом для допустимых страниц"
            if material["status"] == "FAILED":
                status, reason = "FAILED", material.get("error", "Ошибка подготовки файла")
            elif not material.get("enabled"):
                status, reason = "DISABLED", "Выключен пользователем"
            elif material["status"] != "READY":
                status, reason = "PREPARING", "Материал ещё готовится"
            else:
                try:
                    path = safe_path(root, material["key"])
                    if hashlib.sha256(path.read_bytes()).hexdigest() != material["prepared_hash"]:
                        raise ValueError("Подготовленный файл повреждён")
                    with Image.open(path) as source:
                        image = source.convert("RGBA")
                    if material["role"] == "DECORATION":
                        image = image.crop(image.getchannel("A").getbbox())
                        if material.get("harmonize"):
                            tinted = Image.new("RGBA", image.size, roles["secondary"])
                            tinted.putalpha(image.getchannel("A"))
                            image = tinted
                    self.images[material["id"]] = image
                except (OSError, ValueError, KeyError):
                    status, reason = "FAILED", "Подготовленное изображение недоступно или повреждено"
            self.usage[material["id"]] = {
                "asset_ref": material["id"],
                "filename": material["filename"],
                "role": material["role"],
                "hash": material["hash"],
                "prepared_hash": material.get("prepared_hash"),
                "preparation_version": material.get("preparation_version"),
                "status": status,
                "reason": reason,
                "pages": [],
                "placements": [],
                "skipped_pages": [],
            }

    def skip(self, id, number, reason):
        use = self.usage[id]
        use["skipped_pages"].append({"page": number + 1, "reason": reason})
        if use["status"] == "SKIPPED":
            use["reason"] = reason

    def resolve(self, page, number, protected):
        protected = protected + [{"kind": "footer", "rect": (0, 0, 960, 52)}]
        self.reservations[number] = protected
        placed = []
        for request in page.materials:
            id = request.asset_ref
            material = self.materials[id]
            image = self.images.get(id)
            if image is None or not on_page(material, page.layout):
                continue
            if material["opacity"] < 0.08:
                self.skip(id, number, "Прозрачность слишком мала: материал не будет различим")
                continue
            role = material["role"]
            position = material["position"] if material["position"] != "AUTO" else request.preset
            if role == "COVER_PHOTO":
                if page.layout in ("cover", "title") and position == "LEFT":
                    self.skip(id, number, "Слева нет свободной области: здесь находятся заголовок и логотип")
                    continue
                rect = (430, 72, 506, 405)
                if page.layout == "title":
                    # Title art is non-catalog background. Actual text/logo bounds
                    # determine the safe photo field for each title family.
                    candidates = [(640, 80, 286, 355), (48, 75, 258, 305), (48, 70, 864, 68)]
                    rect = next(
                        (r for r in candidates if not any(intersects(r, b["rect"]) for b in protected)), None
                    )
                    if rect is None:
                        self.skip(id, number, "Нет безопасной области для фото рядом с титулом")
                        continue
                elif page.layout != "cover":
                    photos = [b["rect"] for b in protected if b["kind"] == "photo"]
                    blockers = [b["rect"] for b in protected if b["kind"] != "photo"]
                    if not photos:
                        self.skip(id, number, "На странице нет допустимой фотографической области")
                        continue
                    left, bottom = min(r[0] for r in photos), min(r[1] for r in photos)
                    right, top = max(r[0] + r[2] for r in photos), max(r[1] + r[3] for r in photos)
                    union = (left, bottom, right - left, top - bottom)
                    candidates = [union, *sorted(photos, key=lambda r: r[2] * r[3], reverse=True)]
                    rect = next((r for r in candidates if not any(intersects(r, b) for b in blockers)), None)
                    if rect is None:
                        self.skip(id, number, "Фотографическая область пересекает текст или логотип")
                        continue
                if (
                    not material["crop"]
                    and min(image.size) * min(rect[2] / image.width, rect[3] / image.height) < 80
                ):
                    self.skip(
                        id,
                        number,
                        "Пропорции фотографии не подходят: разрешите обрезку или замените изображение",
                    )
                    continue
            elif role == "BACKGROUND":
                rect = (0, 0, 960, 540)
            else:
                # Presets are margins/corridors, not arbitrary user coordinates.
                width = {"SUBTLE": 72, "NORMAL": 96, "BOLD": 126}[material["expression"]]
                scale = min(width / image.width, 42 / image.height)
                w, h = image.width * scale, image.height * scale
                candidates = [
                    ("TOP", (620, 486, w, h)),
                    ("TOP", (904 - w, 482, w, h)),
                    ("TOP", (450, 486, w, h)),
                    ("BOTTOM", (430, 60, w, h)),
                    ("LEFT", (24, 240, min(w, 24), min(h, 70))),
                    ("RIGHT", (912, 260, min(w, 24), min(h, 70))),
                ]
                if number % 2:
                    candidates[0], candidates[1] = candidates[1], candidates[0]
                optical = []
                for tag, (cx, cy, cw, ch) in candidates:
                    ratio = min(cw / image.width, ch / image.height)
                    dw, dh = image.width * ratio, image.height * ratio
                    optical.append((tag, (cx + (cw - dw) / 2, cy + (ch - dh) / 2, dw, dh)))
                candidates = optical
                rect = next(
                    (
                        r
                        for tag, r in candidates
                        if (position == "AUTO" or tag == position)
                        and r[0] >= 24
                        and r[1] >= 56
                        and r[0] + r[2] <= 936
                        and r[1] + r[3] <= 516
                        and max(r[2:]) >= 32
                        and not any(intersects(r, b["rect"]) for b in protected)
                        and not any(intersects(r, p["rect"]) for p in placed if p["role"] == "DECORATION")
                        and not (number > 1 and position == "AUTO" and self.last.get(id) == r)
                    ),
                    None,
                )
                if rect is None:
                    self.skip(
                        id,
                        number,
                        "Недостаточно свободного места с учётом текста, товаров и выбранного размещения",
                    )
                    continue
                self.last[id] = rect
            if role in ("COVER_PHOTO", "BACKGROUND"):
                probe = fitted(
                    image,
                    (round(rect[2]), round(rect[3])),
                    material["crop"],
                    (material["focal_x"], material["focal_y"]),
                    position,
                )
                bounds = probe.getchannel("A").getbbox()
                if (
                    bounds is None
                    or role == "COVER_PHOTO"
                    and min(bounds[2] - bounds[0], bounds[3] - bounds[1]) < 80
                ):
                    self.skip(
                        id,
                        number,
                        "В выбранной области изображение неразличимо: измените обрезку или точку внимания",
                    )
                    continue
            entry = {
                "asset_ref": id,
                "role": role,
                "rect": tuple(round(v, 2) for v in rect),
                "position": position,
                "opacity": material["opacity"],
                "expression": material["expression"],
                "crop": material["crop"],
                "focal_point": [material["focal_x"], material["focal_y"]],
                "harmonize": material["harmonize"],
                "edge_feather": 24 if role == "COVER_PHOTO" else 0,
            }
            placed.append(entry)
            use = self.usage[id]
            use.update(status="USED", reason=None)
            use["pages"].append(number + 1)
            use["placements"].append({"page": number + 1, **entry})
        self.resolved[number] = placed

    def suppress_motif(self, number):
        return bool(self.resolved.get(number))

    def paint(self, canvas, number):
        # Broad layers first, reserved decorative zones last. Products/text are
        # drawn afterwards by the unchanged painter, never replaced by uploads.
        for entry in sorted(self.resolved.get(number, []), key=lambda p: p["role"] == "DECORATION"):
            material = self.materials[entry["asset_ref"]]
            image = self.images[entry["asset_ref"]]
            x, y, w, h = entry["rect"]
            frame = fitted(
                image,
                (max(1, round(w)), max(1, round(h))),
                material["crop"],
                (material["focal_x"], material["focal_y"]),
                entry["position"],
            )
            if entry["edge_feather"]:
                left, top, right, bottom = frame.getchannel("A").getbbox()
                feather = min(entry["edge_feather"], (right - left) / 6, (bottom - top) / 6)
                edge = Image.new("L", frame.size)
                ImageDraw.Draw(edge).rectangle(
                    (left + feather, top + feather, right - feather, bottom - feather), fill=255
                )
                edge = edge.filter(ImageFilter.GaussianBlur(feather / 2))
                frame.putalpha(ImageChops.multiply(frame.getchannel("A"), edge))
            layer = Image.new("RGBA", (960, 540))
            layer.alpha_composite(frame, (round(x), round(540 - y - h)))
            if entry["role"] == "DECORATION":
                strength = {"SUBTLE": 0.42, "NORMAL": 0.7, "BOLD": 0.9}[material["expression"]]
                mask = Image.new("L", (960, 540), round(255 * strength * material["opacity"]))
            else:
                strength = (
                    {"SUBTLE": 0.2, "NORMAL": 0.36, "BOLD": 0.56}
                    if entry["role"] == "BACKGROUND"
                    else {"SUBTLE": 0.42, "NORMAL": 0.65, "BOLD": 0.82}
                )[material["expression"]]
                if entry["role"] == "BACKGROUND" and ImageStat.Stat(image.convert("L")).stddev[0] > 42:
                    strength = {"SUBTLE": 0.14, "NORMAL": 0.22, "BOLD": 0.36}[material["expression"]]
                alpha = round(255 * strength * material["opacity"])
                mask = Image.new("L", (960, 540), alpha)
                draw = ImageDraw.Draw(mask)
                for reservation in self.reservations[number]:
                    rx, ry, rw, rh = reservation["rect"]
                    factor = 0.38 if reservation["kind"] == "photo" else 0.14
                    draw.rectangle(
                        (rx - 14, 540 - ry - rh - 14, rx + rw + 14, 540 - ry + 14), fill=round(alpha * factor)
                    )
                mask = mask.filter(ImageFilter.GaussianBlur(7))
            layer.putalpha(ImageChops.multiply(layer.getchannel("A"), mask))
            canvas.drawImage(ImageReader(layer), 0, 0, width=960, height=540, mask="auto")
            if getattr(canvas, "_presentation_background", None) is not None:
                canvas._presentation_background = Image.alpha_composite(
                    canvas._presentation_background.convert("RGBA"), layer
                ).convert("RGB")

    def results(self):
        for id, use in self.usage.items():
            material = self.materials[id]
            if use["status"] == "SKIPPED" and material["role"] == "COVER_PHOTO":
                use["reason"] = use["reason"] if use["skipped_pages"] else "В выбранной области нет обложки"
        return list(self.usage.values())

    def issues(self):
        return [
            {
                "offer_id": None,
                "asset_id": id,
                "message": f"Обязательный материал «{self.materials[id]['filename']}» не применён: {use['reason']}",
            }
            for id, use in self.usage.items()
            if self.materials[id].get("enabled")
            and self.materials[id].get("required")
            and use["status"] != "USED"
        ]
