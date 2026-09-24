import asyncio
from datetime import timedelta

from conftest import make_product
from sqlalchemy import select

from app.application.background_index import BackgroundIndex
from app.database.catalog import CatalogSafety
from app.database.index import IndexRepository
from app.database.models import CatalogRun, ProductObservation, SupplierOffer
from app.domain.catalog import CatalogNavigation, CatalogRoute, completion
from app.domain.models import utcnow
from app.domain.research import ListingPage


class CatalogFixture:
    supplier = 'gifts'
    counts = {'bottles': 100, 'pencils': 80, 'umbrellas': 60, 'electronics': 120, 'unknown': 40}

    async def catalog_navigation(self, url=None):
        return CatalogNavigation(routes=[] if url else [
            CatalogRoute(url='https://gifts.ru/catalog/' + name, label=name) for name in self.counts])

    async def research_listing(self, branch):
        name = branch.route.rsplit('/', 1)[-1]
        return ListingPage(urls=[f'https://gifts.ru/id/{name}-{n}' for n in range(self.counts[name])], exhausted=True)

    async def research_products(self, url):
        return [make_product(id='gifts:' + url.rsplit('/', 1)[-1], supplier='gifts', source_url=url,
                             name='Unmapped supplier product ' + url.rsplit('/', 1)[-1])]


class SevenPageFixture:
    supplier = 'gifts'

    async def catalog_navigation(self, url=None):
        return CatalogNavigation(routes=[] if url else [
            CatalogRoute(url='https://gifts.ru/catalog/all', label='All supplier products')
        ])

    async def research_listing(self, branch):
        page = branch.cursor.page_number
        count = 13 if page == 6 else 30
        return ListingPage(
            urls=[f'https://gifts.ru/id/p{page}-{n}' for n in range(count)],
            next_url=None if page == 6 else f'https://gifts.ru/catalog/all?page={page + 2}',
            exhausted=page == 6,
            total=193,
            total_unit='families',
        )

    async def research_products(self, url):
        return [make_product(
            id='gifts:' + url.rsplit('/', 1)[-1], supplier='gifts', source_url=url,
            name='Unknown category ' + url.rsplit('/', 1)[-1],
        )]


def test_complete_requires_every_observed_root_in_branch_graph():
    checkpoint={'roots_observed': True, 'roots': [{'url': 'known-a'}, {'url': 'known-b'}],
                'branches': [{'route': 'known-a', 'status': 'COMPLETE', 'pagination_exhausted': True}]}
    assert not completion(checkpoint)
    checkpoint['branches'].append({'route': 'known-b', 'status': 'COMPLETE', 'pagination_exhausted': True})
    assert completion(checkpoint)


async def test_discovery_repairs_published_route_in_old_checkpoint(repository, settings):
    index = IndexRepository(repository.sessions, settings)
    host = BackgroundIndex(index, [CatalogFixture()], settings)
    await index.enqueue('gifts', 'CATALOG_DISCOVERY')
    job = await index.claim(host.owner)
    job['checkpoint'] = {'roots_observed': True, 'roots': [{'url': 'https://gifts.ru/catalog/pencils'}],
                         'branches': [{'supplier': 'gifts', 'category': '', 'query': '', 'route': None,
                                      'status': 'COMPLETE', 'pagination_exhausted': True,
                                      'cursor': {'listing_url': 'https://gifts.ru/catalog/pencils', 'exhausted': True}}]}
    await host.discover(job, CatalogFixture())
    assert job['checkpoint']['branches'][0]['route'] == 'https://gifts.ru/catalog/pencils'


async def test_supplier_first_400_including_unknown(repository, settings):
    index = IndexRepository(repository.sessions, settings)
    host = BackgroundIndex(index, [CatalogFixture()], settings)
    await index.enqueue('gifts', 'CATALOG_DISCOVERY')
    job = await index.claim(host.owner)

    async def immediate(_job, call, _metric):
        return await call()

    host.request = immediate
    await host.run(job)
    assert len(await index.offers('gifts')) == 400
    assert completion(job['checkpoint'])
    assert len(job['checkpoint']['roots']) == 5


async def test_seven_pages_are_exhausted_and_repeat_is_idempotent(repository, settings):
    index = IndexRepository(repository.sessions, settings)
    host = BackgroundIndex(index, [SevenPageFixture()], settings)

    async def immediate(_job, call, _metric):
        return await call()

    host.request = immediate
    for _ in range(2):
        await index.enqueue('gifts', 'CATALOG_DISCOVERY')
        job = await index.claim(host.owner)
        await host.run(job)
        assert completion(job['checkpoint']), job['checkpoint']
        branch = job['checkpoint']['branches'][0]
        assert branch['pages_scanned'] == 7
        assert branch['listings_seen'] == 193
        assert branch['pagination_exhausted'] is True
        assert len(await index.offers('gifts')) == 193


async def test_timed_out_branch_does_not_starve_next_slice(repository, settings):
    class SlowFirst(CatalogFixture):
        async def research_listing(self, branch):
            if branch.route.endswith('bottles'):
                await asyncio.sleep(1)
            return await super().research_listing(branch)

    index = IndexRepository(repository.sessions, settings)
    host = BackgroundIndex(index, [SlowFirst()], settings.model_copy(update={'research_job_timeout_seconds': .05}))
    await index.enqueue('gifts', 'CATALOG_DISCOVERY')
    job = await index.claim(host.owner)

    async def immediate(_job, call, _metric):
        return await call()

    host.request = immediate
    await host.run(job)
    assert job['checkpoint']['branches'][0]['route'].endswith('pencils')
    assert job['checkpoint']['branches'][-1]['route'].endswith('bottles')
    assert not completion(job['checkpoint'])


async def test_anomalous_refresh_preserves_good_observations(repository, settings):
    index = IndexRepository(repository.sessions, settings)
    original = [make_product(id=f'gifts:{n}', supplier='gifts', stock_quantity=500) for n in range(20)]
    await index.ingest(original)
    identifier = await index.enqueue('gifts', 'OBSERVATION_REFRESH')
    job = await index.claim('test')
    safety = CatalogSafety(index)
    await safety.begin(job)
    await safety.stage(job, [p.model_copy(update={'stock_quantity': 0, 'fetched_at': utcnow() + timedelta(seconds=1)}) for p in original])
    assert not await safety.finish(job, True)
    async with index.sessions() as db:
        assert all(p.stock == 500 for p in (await db.scalars(select(ProductObservation))).all())
        run = await db.get(CatalogRun, job['checkpoint']['run_id'])
        assert run.status == 'QUARANTINED' and run.anomalies == ['STOCK_COLLAPSE']
    assert identifier


async def test_only_complete_runs_reconcile_and_never_delete(repository, settings):
    index = IndexRepository(repository.sessions, settings)
    products = [make_product(id=f'gifts:{n}', supplier='gifts') for n in range(4)]
    await index.ingest(products)
    safety = CatalogSafety(index)
    for number in range(4):
        job = {'id': await index.enqueue('gifts', 'CATALOG_DISCOVERY'), 'supplier': 'gifts',
               'kind': 'CATALOG_DISCOVERY', 'checkpoint': {}, 'metrics': {}}
        await safety.begin(job)
        await safety.stage(job, products[:3])
        await safety.finish(job, number > 0)
    async with index.sessions() as db:
        missing = await db.get(SupplierOffer, 'gifts:3')
        assert missing.lifecycle == 'REMOVED' and missing.missing_runs == 3
        assert len((await db.scalars(select(SupplierOffer))).all()) == 4
