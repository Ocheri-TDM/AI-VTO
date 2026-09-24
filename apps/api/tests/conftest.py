from decimal import Decimal

import pytest

from app.config import Settings
from app.database.connection import create_database
from app.database.models import Base
from app.database.repository import SearchRepository
from app.domain.models import Product, ProductColor, ProviderResult
from app.providers.base import SupplierProvider


def make_product(**changes) -> Product:
    values = dict(
        id="oasis:1",
        supplier="oasis",
        source_url="https://www.oasiscatalog.com/item/1",
        name="Термокружка Sense, navy",
        category="thermomug",
        original_price=Decimal("100.50"),
        original_currency="RUB",
        colors=[ProductColor(original_color="navy")],
        stock_quantity=300,
    )
    values.update(changes)
    return Product(**values)


class FakeProvider(SupplierProvider):
    def __init__(self, supplier="oasis", products=None, error=None):
        self.supplier = supplier
        self.products = products if products is not None else [make_product()]
        self.error = error
        self.calls = 0

    async def search(self, query, filters):
        self.calls += 1
        if self.error:
            raise self.error
        return ProviderResult(products=self.products, pages_scanned=1)

    async def get_product(self, url):
        return self.products[0]

    async def health_check(self):
        return self.error is None


@pytest.fixture
def settings(tmp_path):
    return Settings(
        _env_file=None,
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        rub_kzt_rate=Decimal("5.50"),
        background_research_enabled=False,
        auth_required=False,
    )


@pytest.fixture
async def repository(settings):
    engine, sessions = create_database(settings.database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield SearchRepository(sessions)
    await engine.dispose()
