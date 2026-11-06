"""Local original OKLCH recipes and authoritative, frozen sRGB roles.

Oklab matrices: https://bottosson.github.io/posts/oklab/
No third-party palette collection or random RGB perturbation.
"""

import copy
import math
import re
from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

HEX = re.compile(r"#[0-9A-Fa-f]{6}")
DIRECTIONS = {
    "red": ("Красное", 22),
    "orange": ("Оранжевое", 55),
    "yellow": ("Жёлтое", 98),
    "green": ("Зелёное", 145),
    "teal": ("Бирюзовое", 185),
    "sky": ("Голубое", 225),
    "blue": ("Синее", 265),
    "violet": ("Фиолетовое", 300),
    "pink": ("Розовое", 345),
    "brown": ("Коричневое", 65),
    "neutral": ("Нейтральное", 0),
}
RECIPES = ("Бархат", "Бумага", "Графит", "Фарфор", "Минерал", "Туман")
NEAR_DISTANCE = 0.020


def rgb(value):
    return tuple(int(value[n : n + 2], 16) / 255 for n in (1, 3, 5))


def linear(v):
    return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4


@lru_cache(maxsize=65536)
def luminance(value):
    return sum(linear(v) * w for v, w in zip(rgb(value), (0.2126, 0.7152, 0.0722), strict=True))


def contrast(first, second):
    a, b = sorted((luminance(first), luminance(second)))
    return (b + 0.05) / (a + 0.05)


def lab(value):
    r, g, b = map(linear, rgb(value))
    cone_l = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)
    m = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
    s = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
    return (
        0.2104542553 * cone_l + 0.793617785 * m - 0.0040720468 * s,
        1.9779984951 * cone_l - 2.428592205 * m + 0.4505937099 * s,
        0.0259040371 * cone_l + 0.7827717662 * m - 0.808675766 * s,
    )


def lch(value):
    light, a, b = lab(value)
    chroma = math.hypot(a, b)
    return light, chroma, math.degrees(math.atan2(b, a)) % 360 if chroma >= 0.02 else 0


def hue_distance(a, b):
    return abs((a - b + 180) % 360 - 180)


def oklch(light, chroma, hue):
    """Reduce chroma at constant hue/lightness until inside sRGB; never RGB-clip."""

    def convert(c):
        a, b = c * math.cos(math.radians(hue)), c * math.sin(math.radians(hue))
        cone_l = (light + 0.3963377774 * a + 0.2158037573 * b) ** 3
        m = (light - 0.1055613458 * a - 0.0638541728 * b) ** 3
        s = (light - 0.0894841775 * a - 1.291485548 * b) ** 3
        return (
            4.0767416621 * cone_l - 3.3077115913 * m + 0.2309699292 * s,
            -1.2684380046 * cone_l + 2.6097574011 * m - 0.3413193965 * s,
            -0.0041960863 * cone_l - 0.7034186147 * m + 1.707614701 * s,
        )

    low, high = 0.0, chroma
    values = convert(chroma)
    if not all(0 <= v <= 1 for v in values):
        for _ in range(22):
            mid = (low + high) / 2
            if all(0 <= v <= 1 for v in convert(mid)):
                low = mid
            else:
                high = mid
        values = convert(low)
    encoded = [12.92 * v if v <= 0.0031308 else 1.055 * v ** (1 / 2.4) - 0.055 for v in values]
    return "#" + "".join(f"{round(v * 255):02X}" for v in encoded)


class PalettePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    preset_id: str | None = Field(default=None, max_length=80)
    origin_preset_id: str | None = Field(default=None, max_length=80)
    name: str = Field(default="Своя палитра", max_length=90)
    direction: str = Field(default="custom", max_length=15)
    direction_label: str = Field(default="Свой цвет", max_length=30)
    primary: str
    locked: bool = False
    manual: bool = False
    edited_slots: list[Literal[0, 1, 2, 3, 4, 5]] = Field(default_factory=list, max_length=6)
    recipe: int = Field(default=0, ge=0, le=999)
    character: Literal["calm", "soft", "saturated", "contrast"] = "calm"
    application: Literal["light", "dark", "universal"] = "universal"
    contrast_accent: bool = False
    colors: list[str] = Field(min_length=1, max_length=6)
    roles: dict[str, str] = Field(max_length=30)

    @field_validator("primary")
    @classmethod
    def primary_hex(cls, v):
        if not HEX.fullmatch(v):
            raise ValueError("Используйте цвет #RRGGBB")
        return v.upper()

    @field_validator("colors")
    @classmethod
    def color_hex(cls, values):
        if not all(HEX.fullmatch(v) for v in values):
            raise ValueError("Некорректный цвет палитры")
        return [v.upper() for v in values]


def role_colors(primary, accent, tone=0):
    light, c, h = lch(primary)
    al, ac, ah = lch(accent)
    # Nearly neutral hue is intentionally not used to classify a direction.
    c = min(c * 0.55, 0.095)
    roles = {
        "primary": primary,
        "dark_background": oklch(0.10 + light * 0.24 + 0.010 * tone, c * 0.75, h),
        "dark_peak": oklch(0.20 + light * 0.22 + 0.010 * tone, c, h),
        "dark_text": oklch(0.97, 0.008, h),
        "dark_muted": oklch(0.90, 0.012, h),
        "dark_accent": oklch(0.80 + al * 0.12, min(ac, 0.14), ah),
        "dark_line": oklch(0.48, c * 0.5, h),
        "light_background": oklch(0.999 - light * 0.05 - tone * 0.003, c * 0.22, h),
        "light_surface": oklch(0.95 - light * 0.18 - tone * 0.012, c * 0.70, h),
        "light_text": oklch(0.22, c * 0.55, h),
        "light_muted": oklch(0.36, c * 0.45, h),
        "light_accent": oklch(0.21 + al * 0.15, min(ac, 0.14), ah),
        "light_line": oklch(0.72, c * 0.4, h),
    }
    return roles


def validate_roles(plan):
    r = plan["roles"]
    if not all(HEX.fullmatch(c) for c in [*plan["colors"], *r.values()]):
        raise ValueError("INVALID_PALETTE_COLOR")
    pairs = [(f"dark_{ink}", "dark_peak") for ink in ("text", "muted", "accent")]
    pairs += [
        (f"light_{ink}", bg)
        for ink in ("text", "muted", "accent")
        for bg in ("light_background", "light_surface")
    ]
    scores = {a + "/" + b: contrast(r[a], r[b]) for a, b in pairs}
    if min(scores.values()) < 4.5:
        raise ValueError("PALETTE_CONTRAST_FAILED")
    return scores


def recipe(primary, index, direction="custom", locked=False):
    light, chroma, hue = lch(primary)
    variant = index % len(RECIPES)
    contrast_accent = variant in (4, 5)
    accent_hue = hue + (155 if variant == 5 else 85 if variant == 4 else (-22, 18, 0, 30)[variant])
    accent = oklch(
        (0.69, 0.80, 0.56, 0.88, 0.74, 0.66)[variant],
        (0.04, 0.025, 0.07, 0.035, 0.13, 0.10)[variant],
        accent_hue,
    )
    roles = role_colors(primary, accent, variant)
    colors = [
        primary,
        roles["dark_background"],
        roles["light_background"],
        accent,
        oklch(0.52 + variant * 0.025, min(chroma * 0.52, 0.09), hue),
        roles["dark_text"],
    ]
    value = PalettePlan(
        preset_id=f"custom-{primary[1:]}-{index}" if direction == "custom" else f"v1-{direction}-{index}",
        name=f"{RECIPES[variant]} · {index + 1:02}",
        direction=direction,
        direction_label=DIRECTIONS.get(direction, ("Свой цвет", 0))[0],
        primary=primary,
        locked=locked,
        recipe=index,
        colors=colors,
        roles=roles,
        character="contrast"
        if contrast_accent
        else "soft"
        if chroma < 0.1
        else "saturated"
        if chroma > 0.16
        else "calm",
        application=("dark", "light", "universal")[index % 3],
        contrast_accent=contrast_accent,
    ).model_dump()
    validate_roles(value)
    return value


def signature(plan):
    # Compare actual rendered roles, not IDs, ordering or unused swatches.
    return tuple(
        lab(plan["roles"][role])
        for role in (
            "dark_background",
            "dark_peak",
            "dark_accent",
            "light_background",
            "light_surface",
            "light_accent",
            "light_text",
        )
    )


def distance(a, b):
    return math.sqrt(
        sum(sum((x - y) ** 2 for x, y in zip(c, d, strict=True)) for c, d in zip(a, b, strict=True)) / len(a)
    )


@lru_cache(maxsize=16)
def direction_library(direction):
    if direction not in DIRECTIONS:
        raise ValueError("Неизвестное цветовое направление")
    accepted, signatures, exact = [], [], set()
    rejected_exact = rejected_near = 0
    _, hue = DIRECTIONS[direction]
    # Interleave the designed recipes before neighboring hue/lightness values.
    for li, light in enumerate((0.36, 0.53, 0.70, 0.82)):
        for ci, c in enumerate((0.075, 0.14, 0.22)):
            for hi, offset in enumerate((-14, 0, 14)):
                for variant in range(6):
                    index = ((li * 3 + ci) * 3 + hi) * 6 + variant
                    if direction == "neutral":
                        primary = oklch(light, (0.0, 0.012, 0.025)[ci], (hue + offset) % 360)
                    elif direction == "brown":
                        primary = oklch(0.24 + li * 0.15, c * 0.62, hue + offset)
                    else:
                        primary = oklch(light, c, hue + offset)
                    value = recipe(primary, index, direction)
                    key, sig = tuple(sorted(value["colors"])), signature(value)
                    if key in exact:
                        rejected_exact += 1
                    elif any(distance(sig, old) < NEAR_DISTANCE for old in signatures):
                        rejected_near += 1
                    else:
                        accepted.append(value)
                        signatures.append(sig)
                        exact.add(key)
    return accepted, {"exact_duplicates_removed": rejected_exact, "near_duplicates_removed": rejected_near}


@lru_cache(maxsize=1)
def library():
    pools = {d: direction_library(d)[0] for d in DIRECTIONS}
    chosen = {d: [] for d in DIRECTIONS}
    sigs, exact = [], set()
    stats = {"exact_duplicates_removed": 0, "near_duplicates_removed": 0}
    for d in DIRECTIONS:
        for k, count in direction_library(d)[1].items():
            stats[k] += count

    def add(direction, value):
        key, sig = tuple(sorted(value["colors"])), signature(value)
        if key in exact:
            stats["exact_duplicates_removed"] += 1
        elif any(distance(sig, old) < NEAR_DISTANCE for old in sigs):
            stats["near_duplicates_removed"] += 1
        else:
            chosen[direction].append(value)
            sigs.append(sig)
            exact.add(key)

    # Reserve the requested broad red collection, then balance all directions.
    for value in pools["red"][:40]:
        add("red", value)
    for n in range(max(map(len, pools.values()))):
        for direction, values in pools.items():
            if n < len(values) and not (direction == "red" and n < 40):
                add(direction, values[n])
    return chosen, stats


def catalog(direction="red", page=1, character=None, application=None, contrast_accent=None, primary=None):
    if direction == "custom":
        if not primary or not HEX.fullmatch(primary):
            raise ValueError("Введите основной цвет #RRGGBB")
        values = [recipe(primary.upper(), n, locked=True) for n in range(36)]
        # Custom locked anchors share a color intentionally; distinct recipes only.
        unique, sigs = [], []
        for v in values:
            s = signature(v)
            if not any(distance(s, old) < NEAR_DISTANCE for old in sigs):
                unique.append(v)
                sigs.append(s)
        values, stats = unique, {}
    else:
        pools, stats = library()
        if direction not in pools:
            raise ValueError("Неизвестное цветовое направление")
        values = pools[direction]
    values = [
        v
        for v in values
        if (not character or v["character"] == character)
        and (not application or v["application"] == application)
        and (contrast_accent is None or v["contrast_accent"] == contrast_accent)
    ]
    start = (page - 1) * 24
    counts = {d: len(v) for d, v in library()[0].items()}
    return {
        "version": 1,
        "directions": [
            {"id": d, "label": label, "published": counts[d]} for d, (label, _) in DIRECTIONS.items()
        ],
        "published_total": sum(counts.values()),
        "items": copy.deepcopy(values[start : start + 24]),
        "total": len(values),
        "page": page,
        "has_more": start + 24 < len(values),
        "validation": stats,
        "near_distance": NEAR_DISTANCE,
    }


def manual_plan(colors, previous=None, edited_slots=None):
    colors = [v.upper() for v in colors]
    value = recipe(colors[0], 0)
    value.update(
        preset_id=None,
        origin_preset_id=(previous or {}).get("origin_preset_id") or (previous or {}).get("preset_id"),
        name="Своя палитра",
        manual=True,
        colors=colors,
        locked=False,
    )
    accent = colors[3] if len(colors) == 6 else colors[1] if len(colors) > 1 else colors[0]
    value["roles"] = role_colors(colors[0], accent)
    slots = sorted(set(edited_slots or []))
    value["edited_slots"] = slots
    for n in slots:
        if n >= len(colors) or n == 0 or n == (3 if len(colors) == 6 else 1):
            continue
        light, chroma, hue = lch(colors[n])
        role = n if len(colors) == 6 else {2: 2, 3: 4, 4: 5}.get(n, n)
        if role == 1:
            value["roles"].update(
                dark_background=oklch(0.15 + light * 0.18, min(chroma * 0.45, 0.08), hue),
                dark_peak=oklch(0.28 + light * 0.14, min(chroma * 0.5, 0.09), hue),
            )
        elif role == 2:
            value["roles"]["light_background"] = oklch(0.945 + light * 0.045, min(chroma * 0.2, 0.025), hue)
        elif role == 4:
            value["roles"].update(
                light_surface=oklch(0.78 + light * 0.14, min(chroma * 0.45, 0.06), hue),
                dark_peak=oklch(0.25 + light * 0.16, min(chroma * 0.6, 0.095), hue),
            )
        elif role == 5:
            value["roles"].update(
                dark_text=oklch(0.94 + light * 0.05, min(chroma * 0.15, 0.015), hue),
                light_text=oklch(0.15 + light * 0.08, min(chroma * 0.25, 0.04), hue),
            )
    validate_roles(value)
    return value


def resolve_choice(choice, colors):
    if choice is None:
        return None
    if choice.get("manual") or colors != choice.get("colors"):
        return manual_plan(colors, choice, choice.get("edited_slots", []))
    if choice["direction"] == "custom":
        result = recipe(choice["primary"], choice["recipe"], locked=choice.get("locked", False))
    else:
        result = next(
            (
                v
                for v in library()[0].get(choice["direction"], [])
                if v["preset_id"] == choice.get("preset_id")
            ),
            None,
        )
    if not result or result["colors"] != colors:
        raise ValueError("Палитра не соответствует выбранному сочетанию")
    return copy.deepcopy(result)


def painter_roles(plan):
    r = plan["roles"]
    return {
        **r,
        "cover": r["dark_background"],
        "cover_text": r["dark_text"],
        "paper": r["light_background"],
        "surface": r["light_surface"],
        "text": r["light_text"],
        "muted": r["light_muted"],
        "accent": r["light_accent"],
        "secondary": r["dark_accent"],
        "line": r["light_line"],
    }
