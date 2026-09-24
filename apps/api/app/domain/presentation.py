from dataclasses import dataclass
from typing import Literal, Protocol

from app.domain.models import Product


@dataclass(frozen=True)
class PresentationBrief:
    project_name: str
    client_name: str
    selected_products: tuple[Product, ...]
    client_logo_url: str | None = None


class PresentationEngine(Protocol):
    """Stage-two boundary only. No generation/export implementation in stage one."""

    async def render(self, brief: PresentationBrief, format: Literal["pdf", "jpg"]) -> bytes: ...
