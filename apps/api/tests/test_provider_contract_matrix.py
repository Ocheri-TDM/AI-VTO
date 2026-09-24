import pytest

from app.providers.research_support import conservative_stock


def test_all_six_providers_publish_complete_extraction_contract():
    from app.providers.artegifts import ArteGiftsProvider
    from app.providers.gifts import GiftsProvider
    from app.providers.happygifts import HappyGiftsProvider
    from app.providers.oasis import OasisProvider
    from app.providers.portobello import PortobelloProvider
    from app.providers.ucontay import UcontayProvider

    providers = [OasisProvider, UcontayProvider, GiftsProvider, PortobelloProvider,
                 HappyGiftsProvider, ArteGiftsProvider]
    assert {provider.supplier for provider in providers} == {
        'oasis', 'ucontay', 'gifts', 'portobello', 'happygifts', 'artegifts'}
    for provider in providers:
        facts = provider.capabilities.model_dump()
        assert set(facts) == {'discovery', 'identity', 'pagination', 'variants', 'price', 'currency',
                              'stock', 'images', 'category_path', 'refresh'}
        assert all(isinstance(value, str) and value.strip() for value in facts.values())
        assert provider.catalog_navigation is not None
        assert provider.research_listing is not None
        assert provider.research_products is not None


@pytest.mark.parametrize(('values','expected'), [
    ([1976, 1, 24], 1976),
    ([300.0, 250], 300),
    ([0, 0], 0),
    ([], None),
    ([300, None], None),
    ([10.5], None),
])
def test_supplier_stock_never_sums_warehouse_values(values, expected):
    assert conservative_stock(values) == expected


def test_portobello_listing_uses_only_active_paginator_refs():
    from app.providers.portobello import PortobelloProvider

    provider = PortobelloProvider(None, None)
    state = {'family': {'nomenclatures': [{'__ref': 'active'}]},
             'active': {'code': '101'}, 'CatalogNomenclature:recommendation': {'code': '999'}}
    urls = provider.listing_offer_urls(state, {'items': [{'__ref': 'family'}]})
    assert urls == ['https://portobello.ru/catalog/offer/101']


@pytest.mark.parametrize(('supplier','identifier','source'), [
    ('artegifts', '135834', 'max_single_store.available'),
    ('portobello', '101', 'max_single_StocksDataLoader.free:MAIN'),
])
def test_multiwarehouse_provider_contract_is_conservative(supplier, identifier, source):
    from app.providers.research_support import observed_product

    product = observed_product(supplier, 'https://supplier.example/product', identifier, 'Товар',
                               1505, 'KZT', conservative_stock([1976, 1, 24]), 'черный',
                               ['https://supplier.example/image.jpg'], stock_source=source)
    assert product.stock_quantity == 1976
    assert product.evidence['stock_quantity']['source'] == source
    assert product.primary_image


def test_artegifts_configurator_contract():
    from app.providers.artegifts import ArteGiftsProvider

    product = ArteGiftsProvider(None, None).normalize_configurator(
        'https://kz.artegifts.by/catalog/butylki/mystik/?active_sku_id=135834',
        {'product': {'id': 135834, 'name': 'Пластиковая бутылка Mystik', 'priceRrc': 1505,
                     'currency': 'KZT', 'stores': [{'available': 1976}, {'available': 24}]},
         'attrs': {'Цвет': 'Черный', 'Материал товара': 'PET', 'Объем': '730 мл'},
         'images': ['/image.jpg'], 'variants': ['135834', '135835']})
    assert product.id == 'artegifts:135834'
    assert product.stock_quantity == 1976 and product.original_currency == 'KZT'
    assert product.material == 'PET' and product.capacity == '730 мл' and product.primary_image
    assert len(product.metadata['variant_urls']) == 2


def test_happygifts_active_color_contract():
    from app.providers.happygifts import HappyGiftsProvider

    product, variants = HappyGiftsProvider(None, None).normalize_page(
        'https://happygifts.ru/catalog/bottle/color_blue/',
        {'id': 'blue-1', 'name': 'Бутылка синяя', 'price': '1 500',
         'stock': 'Центральный склад Свободно 421 шт', 'color': 'Синий',
         'attrs': {'Материал': 'сталь'}, 'variants': ['/color_blue/', '/color_red/']},
        '/catalog-images-webp/blue/photo/blue.webp')
    assert product.id == 'happygifts:blue-1' and product.stock_quantity == 421
    assert product.original_currency == 'RUB' and product.material == 'сталь'
    assert product.primary_image.endswith('blue.webp') and len(variants) == 2


def test_happygifts_preserves_free_reserved_total_and_unknown_incoming_quantity():
    from app.providers.happygifts import HappyGiftsProvider

    product, _ = HappyGiftsProvider(None, None).normalize_page(
        'https://happygifts.ru/catalog/bottle/color_blue/',
        {'id': 'blue-2', 'name': 'Бутылка', 'price': '1 500', 'color': 'Синий',
         'attrs': {}, 'variants': [], 'availability': [
             'Центральный\nСвободно 2 120 шт.\nВ резерве 50 шт.',
             'В пути\nПоступит на склад 20.11.2026',
         ]},
        None,
    )

    current, incoming = product.availability_records
    assert (current.free_quantity, current.reserved_quantity, current.total_quantity) == (2120, 50, 2170)
    assert incoming.state == 'INCOMING'
    assert incoming.free_quantity is None
    assert incoming.expected_at.isoformat().startswith('2026-11-20')


def test_portobello_offer_and_main_storage_contract():
    from app.providers.portobello import PortobelloProvider

    key = 'CatalogNomenclature:{"code":"101"}'
    state = {key: {'name': 'Термобутылка Portobello', 'price': 2400,
                   'color1': {'__ref': 'blue'}, 'material': {'name': 'сталь'},
                   'brandChain': [{'name': 'Portobello'}], 'capacity': 500,
                   'lengthWithoutPack': 20, 'widthWithoutPack': 7, 'heightWithoutPack': 7,
                   'description': 'Вакуумная бутылка', 'collection': {'code': 'line'}},
             'blue': {'name': 'Темно-синий'}}
    stocks = [{'offerCode': '101', 'free': 350, 'storage': {'type': 'MAIN'}},
              {'offerCode': '101', 'free': 90, 'storage': {'type': 'MAIN'}},
              {'offerCode': '101', 'free': 900, 'storage': {'type': 'REMOTE'}}]
    product = PortobelloProvider(None, None).normalize_offer(
        'https://portobello.ru/catalog/offer/101', '101', state, stocks, ['/101.jpg'])
    assert product.id == 'portobello:101' and product.stock_quantity == 350
    assert product.original_currency == 'RUB' and product.material == 'сталь'
    assert product.capacity == '500' and product.dimensions == '20 × 7 × 7'
    assert product.metadata['family'] == 'line' and product.primary_image
