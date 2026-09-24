import json
from pathlib import Path

import pytest
from conftest import make_product

from app.api.research import page_products, response
from app.application.index_research import IndexedResearchService
from app.application.research_analysis import current_products
from app.application.research_refinement import ResearchRefinementEngine
from app.database.conversations import ConversationRepository
from app.database.index import IndexRepository
from app.database.models import ResearchRecord
from app.database.research import ResearchRepository
from app.providers.catalog_navigation import catalog_links


async def test_reproducible_query_eval_dataset():
    from app.application.research_planner import ResearchPlanner
    from app.domain.research import ResearchSession
    dataset=json.loads((Path(__file__).parent/'fixtures/final_queries.json').read_text(encoding='utf-8'))
    planner=ResearchPlanner()
    for query,category,quantity in dataset['literal']:
        intent=await planner.parse(query)
        assert intent.categories==[category] and intent.quantity==quantity
    research=ResearchSession(chat_id='fixture',intent=await planner.parse('Мерч для IT конференции 300 шт'),
        products=[make_product(id=f'oasis:{n}',name='Бутылка металлическая',material='металл') for n in range(4)])
    engine=ResearchRefinementEngine(None)
    for query,action in dataset['refinements']:
        decision=engine.command(query,research)
        assert decision and decision.action==action,query
    for query in dataset['ambiguous']:
        intent=await planner.parse(query)
        assert intent.discovery and not any(c.startswith('dynamic:') for c in intent.categories)


def test_portobello_paginator_excludes_unrelated_apollo_offers():
    from app.providers.portobello import PortobelloProvider
    provider=PortobelloProvider(None,None)
    state={'family':{'nomenclatures':[{'__ref':'offer'}]}, 'offer':{'code':'001'},
           'CatalogNomenclature:recommendation':{'code':'999'}}
    assert provider.listing_offer_urls(state,{'items':[{'__ref':'family'}]})==['https://portobello.ru/catalog/offer/001']


@pytest.mark.parametrize('supplier', ['artegifts', 'happygifts'])
async def test_published_branch_identity_survives_listing_error(supplier):
    from contextlib import asynccontextmanager

    from app.domain.research import ResearchCoverage
    from app.providers.artegifts import ArteGiftsProvider
    from app.providers.happygifts import HappyGiftsProvider

    class UnavailableBrowser:
        @asynccontextmanager
        async def session(self, *_args):
            raise RuntimeError('offline')
            yield

    provider = (ArteGiftsProvider if supplier == 'artegifts' else HappyGiftsProvider)(UnavailableBrowser(), None)
    route = provider.base_url + '/catalog/elektronika/'
    branch = ResearchCoverage(supplier=supplier, category='', query='', route=route)
    branch.cursor.listing_url = route
    with pytest.raises(RuntimeError, match='offline'):
        await provider.research_listing(branch)
    assert branch.route == route


@pytest.mark.parametrize('supplier,url',[
    ('gifts','https://gifts.ru/catalog/brand-altavolo'),
    ('ucontay','https://ucontay.kz/collection/odezhda'),
    ('portobello','https://portobello.ru/catalog/elektronika'),
    ('happygifts','https://happygifts.ru/catalog/butylki_dlya_vody/'),
    ('artegifts','https://kz.artegifts.by/catalog/elektronika/'),
    ('oasis','https://www.oasiscatalog.com/categories/kuhnya-i-posuda'),
])
def test_provider_navigation_contract_observed_urls(supplier,url):
    links=[{'url':url,'label':'Unknown category'}, {'url':'https://evil.example/catalog/unknown','label':'bad'},
           {'url':url+'?filter=bad','label':'filtered'}]
    routes=catalog_links(supplier,links,'https://example.invalid/')
    assert [r.url for r in routes]==[url]
    assert routes[0].label=='Unknown category'


async def test_new_matches_storage_and_full_pagination(repository,settings):
    index=IndexRepository(repository.sessions,settings)
    products=[make_product(id=f'gifts:{i}',supplier='gifts',name=f'Карандаш деревянный {i}',material='дерево') for i in range(61)]
    await index.ingest(products)
    chat=await ConversationRepository(repository.sessions).create('Final')
    service=IndexedResearchService(ResearchRepository(repository.sessions),[],settings,None,index)
    research=await service.start(chat,'Карандаши 300шт')
    research=await service.change(research.id,selected_ids=[products[0].id])
    await index.ingest([make_product(id='gifts:new',supplier='gifts',name='Карандаш новый',material='дерево')])
    research=await service.sync(research.id)
    assert len(research.products)==61 and research.pending_offer_ids==['gifts:new']
    research=await service.accept_matches(research.id)
    assert len(research.products)==62 and research.view.selected_ids==[products[0].id]
    dto=response(research)
    tail=page_products(research,current_products(research),dto['next_cursor'])
    assert len(dto['products'])==50 and len(tail['products'])==12
    async with repository.sessions() as db:
        row=await db.get(ResearchRecord,research.id)
        assert 'products' not in row.payload
        assert 'source_url' not in json.dumps(row.payload)


async def test_hard_soft_semantic_undo_and_original_constraints(repository,settings):
    index=IndexRepository(repository.sessions,settings)
    await index.ingest([make_product(id='gifts:1',supplier='gifts',name='Бутылка металлическая',material='металл'),
                        make_product(id='gifts:2',supplier='gifts',name='Бутылка пластиковая',material='пластик')])
    chat=await ConversationRepository(repository.sessions).create('Semantic')
    service=IndexedResearchService(ResearchRepository(repository.sessions),[],settings,None,index)
    research=await service.start(chat,'Бутылки 300 шт')
    refine=ResearchRefinementEngine(service)
    soft,decision,_=await refine.apply(research.id,'Желательно металлические')
    assert decision.action=='LOCAL_RERANK' and len(current_products(soft))==2
    hard,decision,_=await refine.apply(research.id,'Только металлические')
    assert len(current_products(hard))==1
    restored,_,_=await refine.apply(research.id,'Верни предыдущий вариант')
    assert len(current_products(restored))==2
    restored,_,_=await refine.apply(research.id,'Покажи всё')
    assert len(current_products(restored))==2 and restored.intent.quantity==300
    assert not service.tasks
