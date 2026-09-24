"""Supplier navigation, independent of the application's product taxonomy."""
from typing import Literal

from pydantic import BaseModel, Field


class CatalogRoute(BaseModel):
    url: str
    label: str = ''
    parent_url: str | None = None


class CatalogNavigation(BaseModel):
    routes: list[CatalogRoute] = Field(default_factory=list)
    status: Literal['OBSERVED', 'UNAVAILABLE'] = 'OBSERVED'


def completion(checkpoint: dict) -> bool:
    """A successful worker exit alone is never evidence of catalog coverage."""
    branches = checkpoint.get('branches', [])
    roots = {root['url'] for root in checkpoint.get('roots', [])}
    routes = {branch.get('route') for branch in branches}
    return bool(checkpoint.get('roots_observed') and roots and branches) and roots <= routes and all(
        b.get('status') == 'COMPLETE'
        and b.get('pagination_exhausted')
        and not b.get('cursor', {}).get('pending_urls')
        and not b.get('cursor', {}).get('failed_urls')
        and not b.get('error_code')
        for b in branches
    )
