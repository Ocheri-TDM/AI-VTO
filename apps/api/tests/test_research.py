import asyncio
from datetime import timedelta

import pytest
from conftest import FakeProvider, make_product
from pydantic import ValidationError

from app.application.research import ResearchService
from app.application.research_analysis import (
    FacetService,
    ProductGroupingService,
    SimilarityService,
    current_products,
    stale,
)
from app.application.research_planner import QueryExpansionService, ResearchPlanner
from app.application.research_refinement import ResearchRefinementEngine
from app.database.conversations import ConversationRepository
from app.database.research import ResearchRepository
from app.domain.errors import SupplierError
from app.domain.models import ColorGroup, ProductColor, utcnow
from app.domain.research import ListingPage, ResearchBudget, ResearchCoverage, ResearchFilters


class ResearchProvider(FakeProvider):
    def __init__(self, error=None):
        super().__init__('gifts')
        self.error = error
        self.pages = []
        self.read = []

    async def research_listing(self, branch):
        self.pages.append(branch.cursor.page_number)
        if self.error:
            raise self.error
        page = branch.cursor.page_number
        return ListingPage(urls=[f'https://gifts.ru/id/{page*30+i}' for i in range(30)],
                           next_url='https://gifts.ru/page2' if page == 0 else None,
                           exhausted=page == 1)

    async def research_products(self, url):
        self.read.append(url)
        return [make_product(id='gifts:'+url.rsplit('/',1)[-1], supplier='gifts', source_url=url,
            name='Бутылка BlueWater, синяя', category='bottle', material='металл',
            colors=[ProductColor(original_color='синий', normalized_color=ColorGroup.BLUE)])]


async def service(repository, settings, provider=None):
    conversations = ConversationRepository(repository.sessions)
    chat = await conversations.create('Research test')
    provider = provider or ResearchProvider()
    value = ResearchService(ResearchRepository(repository.sessions), [provider], settings)
    async def immediate(call, supplier=None):
        return await call()
    value._request = immediate
    return value, chat, provider


async def test_intent_blue_discovery_and_query_boundaries():
    planner = ResearchPlanner()
    intent = await planner.parse('Синие бутылки, 300 шт.')
    assert intent.categories == ['bottle'] and intent.quantity == 300
    assert {ColorGroup.BLUE, ColorGroup.NAVY, ColorGroup.LIGHT_BLUE} <= set(intent.colors)
    assert intent.color_mode == 'family'
    strict = await planner.parse('Строго темно-синие бутылки, 300 шт.')
    assert strict.color_mode == 'strict' and ColorGroup.LIGHT_BLUE not in strict.colors
    ambiguous = await planner.parse('Синий мерч для IT конференции, 300 человек')
    assert ambiguous.quantity == 300 and len(ambiguous.categories) >= 8
    assert 'lanyard' in ambiguous.discovery['exploratory']
    assert 'термокружка' not in QueryExpansionService().expand('bottle', ResearchBudget())
    assert {b.supplier for b in planner.plan(intent, ResearchBudget())} == {
        'oasis','ucontay','gifts','portobello','happygifts','artegifts'}


def test_complete_never_without_exhaustion():
    with pytest.raises(ValidationError):
        ResearchCoverage(supplier='gifts', category='bottle', query='бутылка', status='COMPLETE')


async def test_no_top_n_atomic_cursor_and_cache_independent_persistence(repository, settings):
    svc, chat, provider = await service(repository, settings)
    research = await svc.start(chat, 'Синие бутылки 300 шт.', ResearchBudget(max_query_expansions=1))
    await svc.tasks[research.id]
    saved = await svc.get(research.id)
    assert len(saved.products) == 60  # More than legacy MAX_PRODUCTS_PER_QUERY.
    assert provider.pages == [0, 1]
    assert saved.coverage[2].status == 'COMPLETE'
    saved.products[0].fetched_at = utcnow()-timedelta(hours=2)
    await svc.repository.save(saved)
    await repository.purge_expired()
    assert stale((await svc.repository.get(saved.id)).products[0])
    assert len((await svc.repository.get(saved.id)).products) == 60
    before = len(provider.read)
    await svc.resume(saved.id)
    await svc.tasks[saved.id]
    assert len(provider.read) == before


async def test_filters_undo_selection_hallucination_and_no_browser(repository, settings):
    svc, chat, provider = await service(repository, settings)
    research = await svc.start(chat, 'Синие бутылки 300 шт.', ResearchBudget(max_query_expansions=1))
    await svc.tasks[research.id]
    calls = len(provider.read)
    await svc.change(research.id, filters=ResearchFilters(max_price=1))
    saved = await svc.get(research.id)
    assert len(saved.products)==60 and not current_products(saved)
    await svc.change(research.id, restore='undo')
    assert len(current_products(await svc.get(research.id)))==60
    with pytest.raises(ValueError,match='UNKNOWN_PRODUCT'):
        await svc.change(research.id, selected_ids=['invented'])
    engine = ResearchRefinementEngine(svc)
    refined, decision, _ = await engine.apply(research.id, 'Хочу более премиальные')
    assert decision.action == 'LOCAL_RERANK'
    refined, decision, _ = await engine.apply(research.id, 'Убери пластиковые')
    assert decision.action == 'LOCAL_SEMANTIC_REFINE'
    assert len(provider.read) == calls
    assert FacetService().build(refined.products)['material'][0]['count']==60


@pytest.mark.parametrize('code,status',[('SUPPLIER_CAPTCHA_REQUIRED','CAPTCHA'),('SUPPLIER_TIMEOUT','TIMEOUT'),('SUPPLIER_PARSING_ERROR','FAILED')])
async def test_supplier_controlled_failures(repository,settings,code,status):
    svc, chat, provider = await service(repository,settings,ResearchProvider(SupplierError(code,'test')))
    research = await svc.start(chat,'Синие бутылки 300 шт.',ResearchBudget(max_query_expansions=1))
    await svc.tasks[research.id]
    result = await svc.get(research.id)
    assert next(b for b in result.coverage if b.supplier=='gifts').status==status
    assert result.job_status=='PARTIAL'


async def test_cancel_keeps_cursor_resumable(repository,settings):
    provider=ResearchProvider()
    gate=asyncio.Event()
    original=provider.research_products
    async def blocked(url):
        await gate.wait()
        return await original(url)
    provider.research_products=blocked
    svc,chat,_=await service(repository,settings,provider)
    research=await svc.start(chat,'Синие бутылки 300 шт.',ResearchBudget(max_query_expansions=1))
    while not provider.pages:
        await asyncio.sleep(.01)
    await svc.cancel(research.id)
    assert (await svc.get(research.id)).job_status=='CANCELLED'
    gate.set()
    await svc.resume(research.id)
    await svc.tasks[research.id]
    assert len((await svc.get(research.id)).products)==60


def test_similarity_and_cross_supplier_groups_preserve_offers():
    first=make_product(name='Бутылка BlueWater',category='bottle',price_kzt=5000,material='металл')
    second=first.model_copy(update={'id':'gifts:other','supplier':'gifts'})
    other=make_product(name='Рюкзак',category='backpack',price_kzt=20000)
    assert SimilarityService().score(second,[first])>SimilarityService().score(other,[first])
    groups=ProductGroupingService().groups([first,second,other])
    assert any(len(g['offer_ids'])==2 for g in groups)
    assert sum(len(g['offer_ids']) for g in groups)==3


def test_model_rerank_cannot_smuggle_filters_or_product_selection():
    from app.domain.research import ResearchDecision
    decision = ResearchDecision(action='LOCAL_RERANK', preference='premium',
        filters=ResearchFilters(max_price=1), product_ids=['invented'], query='unrelated', count=99)
    assert decision.filters is None and decision.product_ids == []
    assert decision.query is None and decision.count is None


def test_bottle_accessories_and_gift_sets_are_not_bottles():
    from app.domain.intent import CategoryResolver
    resolver = CategoryResolver()
    for title in ('Подарочный набор с бутылкой', 'Набор бутылка и блокнот', 'Коробка для бутылки'):
        assert 'bottle' not in resolver.classify(title)
    assert 'bottle' in resolver.classify('Бутылка с бамбуковой крышкой')


async def test_immediate_cancel_persists_terminal_state(repository, settings):
    svc, chat, _ = await service(repository, settings)
    research = await svc.start(chat, 'Синие бутылки 300 шт.', ResearchBudget(max_query_expansions=1))
    await svc.cancel(research.id)
    assert (await svc.repository.get(research.id)).job_status == 'CANCELLED'


async def test_targeted_research_only_runs_missing_branch(repository, settings):
    svc, chat, provider = await service(repository, settings)
    research = await svc.start(chat, 'Синие бутылки 300 шт.', ResearchBudget(max_query_expansions=1))
    await svc.tasks[research.id]
    engine = ResearchRefinementEngine(svc)
    research = await svc.get(research.id)
    assert engine.command('Добавь бутылки с бамбуком', research).action == 'TARGETED_RESEARCH'
    original_ids = {b.id for b in research.coverage}
    await engine.target(research, 'бамбук')
    await svc.tasks[research.id]
    result = await svc.get(research.id)
    new_branches = [b for b in result.coverage if b.id not in original_ids]
    assert len(new_branches) == 1 and new_branches[0].supplier == 'gifts'
    calls = len(provider.read)
    await engine.target(result, 'бамбук')
    assert len(provider.read) == calls
