from abc import ABC, abstractmethod
from decimal import Decimal
from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from app.domain.errors import SupplierError
from app.domain.models import Product, ProviderResult, SearchIntent
from app.domain.research import ListingPage, ResearchCoverage


class ProviderCapabilities(BaseModel):
    model_config = ConfigDict(frozen=True)
    discovery: str
    identity: str
    pagination: str
    variants: str
    price: str
    currency: str
    stock: str
    images: str
    category_path: str
    refresh: str


class SupplierProvider(ABC):
    capabilities: ClassVar[ProviderCapabilities]
    parser_version: ClassVar[str] = "availability-v1"
    async def catalog_navigation(self, url=None):
        from app.providers.catalog_navigation import navigation
        return await navigation(self, url)

    def observation_group_key(self, url: str) -> str:
        """URLs sharing an observation response may be refreshed in one provider call."""
        return url

    def targeted_category_route(self, product_url: str) -> str | None:
        """Return a category route observed in a targeted result URL, if encoded by the site."""
        return None
    supplier: str

    async def research_listing(self, branch: ResearchCoverage) -> ListingPage:
        """One verified listing page, without product/page count truncation."""
        raise SupplierError('SUPPLIER_RESEARCH_UNSUPPORTED', 'Research traversal is not implemented.')

    async def research_products(self, url: str) -> list[Product]:
        return [await self.get_product(url)]

    @abstractmethod
    async def search(self, query: str, filters: SearchIntent) -> ProviderResult:
        """Search one category/term through the supplier's normal browser UI."""

    @abstractmethod
    async def get_product(self, url: str) -> Product:
        """Fetch one exact variant from its verified, allowlisted source URL."""

    async def get_stock(self, product: Product) -> int | None:
        return (await self.get_product(product.source_url)).stock_quantity

    async def get_price(self, product: Product) -> tuple[Decimal | None, str | None]:
        fresh = await self.get_product(product.source_url)
        return fresh.original_price, fresh.original_currency

    async def get_images(self, product: Product) -> list[str]:
        return (await self.get_product(product.source_url)).images

    @abstractmethod
    async def health_check(self) -> bool:
        pass
