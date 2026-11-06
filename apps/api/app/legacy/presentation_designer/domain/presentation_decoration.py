"""Versioned closed decorative grammar. Missing settings retain legacy output."""

from typing import Literal

from pydantic import BaseModel, ConfigDict

Family = Literal["contours", "waves", "grid", "corners", "ribbons", "dots"]
FAMILIES = ("contours", "waves", "grid", "corners", "ribbons", "dots")


class DecorationSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    enabled: bool = True
    intensity: Literal["SUBTLE", "NORMAL", "BOLD"] = "SUBTLE"


class DecorationPlan(DecorationSettings):
    family: Family = "contours"
    seed: int = 0


def decoration_plan(settings):
    value = settings.get("auto_decoration")
    if value is None:
        return None
    seed = settings.get("design_seed", 0)
    return DecorationPlan(**value, family=FAMILIES[seed % len(FAMILIES)], seed=seed)
