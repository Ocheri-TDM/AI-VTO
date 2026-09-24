import asyncio
from decimal import Decimal

from conftest import FakeProvider, make_product

from app.ai.basic import BasicIntentParser
from app.application.search import SearchService
from app.domain.errors import SupplierError
from app.domain.models import ProductColor

QUERY = "Темно-синие термокружки, рюкзаки, ежедневники и ручки. Тираж 300 шт."


async def test_acceptance_pipeline_partial_results_and_cache(repository, settings):
    working = FakeProvider(
        products=[
            make_product(),
            make_product(id="oasis:2", stock_quantity=299),
            make_product(id="oasis:3", stock_quantity=None),
            make_product(id="oasis:4", colors=[ProductColor(original_color="blue")]),
            make_product(id="oasis:5", original_currency="KZT", original_price=Decimal("600")),
        ]
    )
    broken = FakeProvider("ucontay", error=SupplierError("SUPPLIER_CAPTCHA_REQUIRED", "CAPTCHA"))
    service = SearchService(repository, [working, broken], BasicIntentParser(), settings)
    first = await service.start(QUERY)
    await service.wait(first.id)
    result = await repository.get(first.id)
    assert result.status == "partial"
    assert [p.price_kzt for p in result.products] == [553, 600]
    assert result.suppliers[0].products_rejected == 3
    assert result.suppliers[1].error_code == "SUPPLIER_CAPTCHA_REQUIRED"
    assert broken.calls == 1  # challenges are never retried/bypassed
    again = await service.start(QUERY)
    assert again.id == first.id and again.cache_hit
    assert working.calls == 4
    refreshed = await service.start(QUERY, refresh=True)
    await service.wait(refreshed.id)
    assert refreshed.id != first.id and working.calls == 8


async def test_simultaneous_requests_share_one_job(repository, settings):
    provider = FakeProvider()
    service = SearchService(repository, [provider], BasicIntentParser(), settings)
    first, second = await asyncio.gather(service.start("термокружки"), service.start("термокружки"))
    assert first.id == second.id
    await service.wait(first.id)
    assert provider.calls == 1


async def test_retry_is_bounded_and_other_supplier_survives(repository, settings):
    broken = FakeProvider("ucontay", error=SupplierError("SUPPLIER_TIMEOUT", "timeout", retryable=True))
    working = FakeProvider()
    service = SearchService(repository, [broken, working], BasicIntentParser(), settings)
    initial = await service.start("термокружка")
    await service.wait(initial.id)
    result = await repository.get(initial.id)
    assert result.status == "partial" and len(result.products) == 1
    assert broken.calls == settings.provider_retries + 1


async def test_unconfigured_exchange_rate_never_leaks_rub_as_kzt(repository, settings):
    settings.rub_kzt_rate = None
    service = SearchService(repository, [FakeProvider()], BasicIntentParser(), settings)
    initial = await service.start("термокружка")
    await service.wait(initial.id)
    result = await repository.get(initial.id)
    assert result.products == []
    assert result.suppliers[0].error_code == "CURRENCY_CONVERSION_ERROR"
