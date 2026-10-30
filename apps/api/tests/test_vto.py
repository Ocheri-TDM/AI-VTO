import copy
import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select

from app.application.vto_sync import sync_slice
from app.config import Settings
from app.database.index import IndexRepository
from app.database.models import (
    AvailabilityObservation,
    CatalogSyncJob,
    IndexedProduct,
    ProductConcept,
    ProductConceptOffer,
    ProductObservation,
    SupplierOffer,
    SupplierSyncState,
)
from app.domain.errors import SupplierError
from app.providers.vto import VTOProvider
from app.providers.vto.provider import money, normalize, page_data, stock_from_html


@pytest.fixture
def melango():
    return json.loads((Path(__file__).parent / "fixtures/vto/melango.json").read_text("utf-8"))


def test_live_fixture_variant_identity_and_fields(melango):
    products = normalize(**melango)
    assert len(products) == 4
    assert len({p.id for p in products}) == 4
    assert len({p.primary_image for p in products}) == 4
    assert all(p.supplier == "vto" and p.original_price == Decimal("2084") for p in products)
    assert all(p.brand == "Molti" and p.material == "полиэстер, 300D" for p in products)
    assert {p.stock_quantity for p in products} == {1739, 2074, 2349, 977}
    assert {c.normalized_color.value for p in products for c in p.colors} == {"BLACK", "GREY", "BLUE", "RED"}
    assert not any(p.id == "vto:1521733" for p in products)


def test_many_categories_are_separate_concepts_not_one_overlong_label(melango):
    melango["parent"]["categories"] = [{"name": "Category " + str(i), "slug": str(i)} for i in range(80)]
    product = normalize(**melango)[0]
    assert product.metadata["supplier_category"] == "Category 0"
    assert len(product.metadata["breadcrumbs"]) == 80
    assert len(product.metadata["vto_categories"]) == 80


def test_manufacturer_brand_collection_are_not_product_types(melango):
    melango["parent"]["categories"] = [
        {"name": "Oasis", "slug": "oasis"}, {"name": "Molti", "slug": "molti"},
        {"name": "Новинки", "slug": "novinki"}, {"name": "Рюкзаки", "slug": "backpacks"},
    ]
    product = normalize(**melango)[0]
    assert product.metadata["breadcrumbs"] == ["Рюкзаки"]
    assert [c["role"] for c in product.metadata["vto_category_roles"]] == ["MANUFACTURER", "BRAND", "COLLECTION", "PRODUCT_TYPE"]


@pytest.mark.parametrize("value,expected", [
    ('<p class="stock in-stock">1 234 в наличии</p>', 1234),
    ('<p class="stock out-of-stock">Нет в наличии</p>', 0),
    ('<p>0 в наличии</p>', 0), ('<p>В наличии</p>', None), (None, None),
])
def test_stock_zero_and_unknown(value, expected):
    assert stock_from_html(value) == expected


def test_currency_scale_and_exact_decimal():
    assert money({"price": "12345", "currency_minor_unit": 2}) == Decimal("123.45")
    assert money({"price": "2084", "currency_minor_unit": 0}) == Decimal("2084")
    assert money({"price": ""}) is None


def test_parent_price_image_stock_never_leak_to_variant(melango):
    variant = melango["detail"]["variants"][0]
    variant.update(availability_html="В наличии", max_qty=99999, image={}, display_price=None)
    melango["parent"]["stock_quantity"] = 999
    product = normalize(**melango)[0]
    assert product.stock_quantity is None and product.original_price is None
    assert product.primary_image is None and not product.available
    assert product.availability_records[0].free_quantity is None
    assert product.metadata["stock_scope"] == "PARENT_SHARED"
    assert product.metadata["parent_shared_stock"] == 999


def test_real_color_size_combinations_only(melango):
    detail = melango["detail"]
    detail["options"]["attribute_pa_size"] = {"s": "S", "m": "M"}
    detail["variants"][0]["attributes"]["attribute_pa_size"] = "s"
    detail["variants"][1]["attributes"]["attribute_pa_size"] = "m"
    products = normalize(**melango)
    assert len(products) == 4 and products[0].metadata["size"] == "S"
    assert products[1].metadata["size"] == "M"


def test_no_invented_combinations_or_ids(melango):
    melango["detail"]["variants"][0]["attributes"]["attribute_pa_color-new"] = ""
    with pytest.raises(ValueError, match="Wildcard"):
        normalize(**melango)


def test_single_published_size_option_resolves_wildcard_without_new_ids(melango):
    melango["detail"]["options"]["attribute_pa_size-new"] = {"a5": "A5"}
    melango["detail"]["variants"][0]["attributes"]["attribute_pa_size-new"] = ""
    products = normalize(**melango)
    assert len(products) == 4 and products[0].metadata["size"] == "A5"
    assert products[0].id == "vto:1823107"
    assert products[0].metadata["raw_variant_attributes"]["attribute_pa_size-new"] == ""
    assert products[0].metadata["inherited_variant_attributes"]["attribute_pa_size-new"] == "UNIQUE_PUBLISHED_FORM_OPTION"
    melango["detail"]["variants"] = None
    with pytest.raises(ValueError, match="no publicly confirmed"):
        normalize(**melango)


def test_html_entities_and_partial_html():
    data = page_data('<form class="variations_form" data-product_id="7" data-product_variations="[{&quot;variation_id&quot;:8}]"></form>')
    assert data["variants"] == [{"variation_id": 8}]
    assert page_data("<html><p>broken") == {"parent_id": None, "variants": None, "attributes": {}, "options": {}, "simple_stock": None}


def test_simple_product_has_no_fake_variation_id():
    parent = {"id": 7, "type": "simple", "name": "Ручка", "permalink": "https://vto.kz/product/pen/",
              "prices": {"price": "10025", "currency_code": "KZT", "currency_minor_unit": 2}, "images": []}
    product = normalize(parent, {"attributes": {}, "simple_stock": "0 в наличии"})[0]
    assert product.id == "vto:7" and product.metadata["vto_variation_id"] is None
    assert product.original_price == Decimal("100.25") and product.stock_quantity == 0
    assert not product.available


def test_vto_non_sequential_php_image_array_and_missing_price():
    parent = {"id": 7, "type": "simple", "name": "Ручка", "permalink": "https://vto.kz/product/pen/",
              "prices": None, "images": {"1": {"src": "/wp-content/uploads/image.jpg"}}}
    product = normalize(parent, {"attributes": {}})[0]
    assert product.original_price is None and product.price_kzt is None
    assert product.primary_image == "https://vto.kz/wp-content/uploads/image.jpg"


def test_backorder_is_not_on_hand(melango):
    melango["detail"]["variants"][0].update(backorders_allowed=True, is_in_stock=False)
    product = normalize(**melango)[0]
    assert product.stock_quantity is None and product.availability_records[0].state == "PREORDER"


def test_explicit_variant_quantity_has_own_provenance(melango):
    melango["detail"]["variants"][0]["stock_quantity"] = 0
    product = normalize(**melango)[0]
    assert product.stock_quantity == 0
    assert product.evidence["stock_quantity"]["source"] == "variation.stock_quantity"


async def test_variant_upsert_isolation_and_new_zero_wins(repository, settings, melango):
    index = IndexRepository(repository.sessions, settings)
    products = normalize(**melango)
    await index.ingest(products)
    assert (await index.ingest(products))["products_changed"] == 0
    updated = copy.deepcopy(melango)
    updated["detail"]["variants"][0]["availability_html"] = "0 в наличии"
    await index.ingest([normalize(**updated)[0]])
    async with repository.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(SupplierOffer)) == 4
        assert await db.scalar(select(func.count()).select_from(IndexedProduct)) == 1
        assert await db.scalar(select(func.count()).select_from(AvailabilityObservation)) == 4
        assert (await db.get(ProductObservation, products[0].id)).stock == 0
        assert (await db.get(ProductObservation, products[1].id)).stock == products[1].stock_quantity


async def test_exact_kzt_decimal_is_stored_and_used_by_sql_budget(repository, settings, melango):
    from app.domain.models import Budget, SearchIntent
    index = IndexRepository(repository.sessions, settings)
    melango["detail"]["variants"][0]["display_price"] = Decimal("2084.45")
    products = normalize(**melango)
    await index.ingest(products)
    async with repository.sessions() as db:
        observation = await db.get(ProductObservation, products[0].id)
        assert observation.price_kzt == Decimal("2084.45")
        assert observation.payload["price_kzt"] == 2084
    ids = await index.candidate_ids(SearchIntent(raw_query="рюкзак", categories=["backpack"], budget=Budget(max=2084)))
    assert products[0].id not in ids and len(ids) == 3


async def test_explicit_pagination_and_invalid_response():
    calls = []
    async def transport(request):
        calls.append(dict(request.url.params))
        return httpx.Response(200, json=[{"id": 21}], headers={"X-WP-Total": "21", "X-WP-TotalPages": "2"})
    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    provider = VTOProvider(Settings(_env_file=None), client)
    data, total, pages = await provider.collection(2)
    assert calls[0] == {"page": "2", "per_page": "20", "orderby": "date", "order": "desc"}
    assert (data, total, pages) == ([{"id": 21}], 21, 2)
    with pytest.raises(SupplierError, match="page length"):
        await provider.collection(1)
    await provider.close()


async def test_api_children_fallback_when_parent_and_html_have_no_variations(melango):
    parent = melango["parent"]
    parent["variations"] = []
    parent["is_purchasable"] = False
    async def transport(request):
        if request.url.path.startswith("/product/"):
            return httpx.Response(200, text='<form class="variations_form" data-product_id="1521733" data-product_variations="[]"></form>')
        assert dict(request.url.params)["parent"] == "1521733"
        child = {"id": 888, "parent": 1521733, "type": "variation", "variation": "Цвет: Серый",
                 "permalink": parent["permalink"], "sku": "own-sku", "prices": {"price": "12345", "currency_code": "KZT", "currency_minor_unit": 2},
                 "images": [{"src": "https://vto.kz/wp-content/uploads/grey.jpg"}], "is_in_stock": True, "low_stock_remaining": None}
        return httpx.Response(200, json=[child], headers={"X-WP-Total": "1", "X-WP-TotalPages": "1"})
    provider = VTOProvider(Settings(_env_file=None), httpx.AsyncClient(transport=httpx.MockTransport(transport)))
    products = await provider.products(parent)
    assert len(products) == 1 and products[0].id == "vto:888"
    assert products[0].original_price == Decimal("123.45") and products[0].metadata["sku"] == "own-sku"
    assert products[0].colors[0].normalized_color.value == "GREY"
    assert products[0].stock_quantity is None
    assert products[0].metadata["variant_source"] == "STORE_API_VARIATION"
    await provider.close()


async def test_variation_parent_filter_must_be_honored():
    async def transport(request):
        return httpx.Response(200, json=[{"id": 1, "parent": 999}], headers={"X-WP-Total": "1", "X-WP-TotalPages": "1"})
    provider = VTOProvider(Settings(_env_file=None), httpx.AsyncClient(transport=httpx.MockTransport(transport)))
    with pytest.raises(SupplierError, match="does not belong"):
        await provider.variation_collection(7)
    await provider.close()


@pytest.mark.parametrize("failure", [429, 503, "timeout"])
async def test_bounded_http_retries(failure, monkeypatch):
    calls = 0
    async def sleep(_seconds):
        return None
    monkeypatch.setattr("app.providers.vto.provider.asyncio.sleep", sleep)
    async def transport(request):
        nonlocal calls
        calls += 1
        if failure == "timeout":
            raise httpx.ReadTimeout("timeout", request=request)
        return httpx.Response(failure, headers={"Retry-After": "0"})
    provider = VTOProvider(Settings(_env_file=None), httpx.AsyncClient(transport=httpx.MockTransport(transport)))
    with pytest.raises(SupplierError):
        await provider.request("https://vto.kz/product/example/")
    assert calls == 3
    await provider.close()


async def test_failed_refresh_preserves_observation(repository, settings, melango):
    index = IndexRepository(repository.sessions, settings)
    products = normalize(**melango)
    await index.ingest(products)
    provider = VTOProvider(settings, httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(404))))
    with pytest.raises(SupplierError):
        await provider.products(melango["parent"])
    async with repository.sessions() as db:
        assert (await db.get(ProductObservation, products[0].id)).observed_at.replace(tzinfo=None) == products[0].fetched_at.replace(tzinfo=None)
    await provider.close()


async def test_confirmed_vto_zero_is_published_during_partial_refresh(repository, settings, melango):
    from app.database.catalog import CatalogSafety
    index = IndexRepository(repository.sessions, settings)
    products = normalize(**melango)
    await index.ingest(products)
    job_id = await index.enqueue("vto", "TARGETED_REFRESH")
    job = await index.claim("refresh-owner", job_ids=[job_id])
    safety = CatalogSafety(index)
    await safety.begin(job)
    melango["detail"]["variants"][0]["availability_html"] = "0 в наличии"
    await safety.stage(job, [normalize(**melango)[0]])
    assert await safety.finish(job, False)
    async with repository.sessions() as db:
        assert (await db.get(ProductObservation, products[0].id)).stock == 0
        assert (await db.get(ProductObservation, products[1].id)).stock == products[1].stock_quantity


async def test_queue_resume_and_idempotence(repository, settings, melango):
    index = IndexRepository(repository.sessions, settings)
    job_id = await index.enqueue("vto", "FULL_CATALOG_RECONCILIATION")
    job = await index.claim("vto-test", job_ids=[job_id])
    class Provider:
        requests = errors = 0
        def __init__(self):
            self.settings = settings
        async def collection(self, page, per_page):
            return [melango["parent"]], 1, 1
        async def products(self, parent):
            return normalize(parent, melango["detail"])
    provider = Provider()
    # A zero-time slice does not falsely complete or move the cursor.
    assert await sync_slice(index, provider, job, seconds=0) == ("PENDING", "TIME_BUDGET")
    assert job["checkpoint"]["page"] == 1
    assert await sync_slice(index, provider, job) == ("COMPLETED", None)
    assert job["checkpoint"]["coverage_complete"]
    assert await sync_slice(index, provider, job) == ("COMPLETED", None)
    async with repository.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(SupplierOffer)) == 4


async def test_failed_scan_waits_for_schedule_and_manual_retry_keeps_cursor(repository, settings, melango):
    from app.domain.models import utcnow
    index = IndexRepository(repository.sessions, settings)
    job_id = await index.enqueue("vto", "FULL_CATALOG_RECONCILIATION")
    job = await index.claim("vto-owner", job_ids=[job_id])
    checkpoint = {"page": 3, "seen_parent_ids": [111], "pagination_exhausted": True,
                  "failed_products": {"1521733": {"product": melango["parent"], "attempts": 3, "error": "VTO_HTTP_ERROR"}}}
    await index.checkpoint(job_id, checkpoint, {})
    await index.finish(job, "vto-owner", "FAILED", "VTO_UNPROCESSED_PRODUCTS")
    async with repository.sessions() as db:
        state = await db.get(SupplierSyncState, "vto")
        assert state.next_reconciliation_at.replace(tzinfo=utcnow().tzinfo) > utcnow()
    await index.enqueue("vto", "FULL_CATALOG_RECONCILIATION", {"reason": "manual_vto_sync"})
    async with repository.sessions() as db:
        row = await db.get(CatalogSyncJob, job_id)
        assert row.checkpoint["page"] == 3 and row.checkpoint["seen_parent_ids"] == [111]
        assert row.checkpoint["failed_products"]["1521733"]["attempts"] == 0


async def test_equal_totals_with_changed_parent_ids_do_not_claim_full_coverage(repository, settings, melango):
    index = IndexRepository(repository.sessions, settings)
    job_id = await index.enqueue("vto", "FULL_CATALOG_RECONCILIATION")
    job = await index.claim("vto-test", job_ids=[job_id])
    class Provider:
        requests = errors = 0
        calls = 0
        def __init__(self):
            self.settings = settings
        async def collection(self, page, per_page):
            self.calls += 1
            parent = dict(melango["parent"])
            if self.calls > 1:
                parent["id"] = 99999
            return [parent], 1, 1
        async def products(self, parent):
            return normalize(parent, melango["detail"])
    status, error = await sync_slice(index, Provider(), job)
    assert (status, error) == ("FAILED", "VTO_CATALOG_CHANGED_DURING_SCAN")
    assert job["checkpoint"]["coverage_complete"] is False


def test_only_vto_is_configured(settings):
    from app.application.research_planner import SUPPLIERS
    from app.main import create_app
    app = create_app(settings)
    assert SUPPLIERS == ("vto",)
    assert set(app.state.research.providers) == {"vto"}
    assert not hasattr(app.state, "browser")


async def test_corrected_source_categories_replace_obsolete_concept_links(repository, settings, melango):
    index = IndexRepository(repository.sessions, settings)
    product = normalize(**melango)[0]
    product.metadata.update(supplier_category="Old category", breadcrumbs=["Old category"])
    await index.ingest([product])
    product.metadata.update(supplier_category="New category", breadcrumbs=["New category"])
    await index.ingest([product])
    async with repository.sessions() as db:
        linked = (await db.scalars(select(ProductConcept.canonical_name).join(
            ProductConceptOffer, ProductConceptOffer.concept_id == ProductConcept.id
        ).where(ProductConceptOffer.offer_id == product.id))).all()
        assert linked == ["new category"]
        obsolete = await db.scalar(select(ProductConcept).where(ProductConcept.canonical_name == "old category"))
        assert obsolete.products_count == 0
