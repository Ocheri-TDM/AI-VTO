import json
from pathlib import Path

from conftest import make_product

from app.application.index_research import IndexedResearchService
from app.application.research_refinement import ResearchRefinementEngine
from app.database.conversations import ConversationRepository
from app.database.index import IndexRepository
from app.database.models import ResearchRecord
from app.database.research import ResearchRepository


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


async def test_new_matches_storage_and_full_pagination(repository,settings):
    index=IndexRepository(repository.sessions,settings)
    products=[make_product(id=f'gifts:{i}',supplier='gifts',name=f'Карандаш деревянный {i}',material='дерево') for i in range(204)]
    await index.ingest(products)
    chat=await ConversationRepository(repository.sessions).create('Final')
    service=IndexedResearchService(ResearchRepository(repository.sessions),[],settings,None,index)
    research=await service.start(chat,'Карандаши 300шт')
    research=await service.change(research.id,selected_ids=[products[0].id])
    await index.ingest([make_product(id='gifts:new',supplier='gifts',name='Карандаш новый',material='дерево')])
    research=await service.sync(research.id)
    assert not research.products and len(research.pool_offer_ids)==204
    research=await service.accept_matches(research.id)
    assert len(research.pool_offer_ids)==205 and research.view.selected_ids==[products[0].id]
    dto=await service.grid_response(research)
    second=await service.grid_response(research,dto['next_cursor'])
    third=await service.grid_response(research,second['next_cursor'])
    pages=dto['products']+second['products']+third['products']
    assert [len(dto['products']),len(second['products']),len(third['products'])]==[100,100,5]
    assert third['next_cursor'] is None and len({item['id'] for item in pages})==205
    async with repository.sessions() as db:
        row=await db.get(ResearchRecord,research.id)
        assert 'products' not in row.payload
        assert 'source_url' not in json.dumps(row.payload)


async def test_hard_soft_semantic_undo_and_original_constraints(repository,settings):
    index=IndexRepository(repository.sessions,settings)
    await index.ingest([make_product(id='gifts:1',supplier='gifts',name='Бутылка металлическая',material='металл'),
                        make_product(id='gifts:2',supplier='gifts',name='Бутылка пластиковая',material='пластик')])
    await ConversationRepository(repository.sessions).create('Semantic')
    service=IndexedResearchService(ResearchRepository(repository.sessions),[],settings,None,index)
    from app.application.project_queries import ProjectQueryService
    from app.domain.conversation_router import ConversationQueryRouter
    workspace=ProjectQueryService(repository.sessions,service,ConversationQueryRouter())
    project=(await ConversationRepository(repository.sessions).projects())[0]['id']
    await workspace.create(project,'Бутылки 300 шт',None)
    soft=(await workspace.request(project,'Желательно металлические',None))['research']
    assert (await service.grid_response(soft))['filtered_total']==2
    hard=(await workspace.request(project,'Только металлические',None))['research']
    assert (await service.grid_response(hard))['filtered_total']==1
    restored=(await workspace.request(project,'Верни предыдущий вариант',None))['research']
    assert (await service.grid_response(restored))['filtered_total']==2
    restored=(await workspace.request(project,'Покажи всё',None))['research']
    assert (await service.grid_response(restored))['filtered_total']==2 and restored.intent.quantity==300
    assert not service.tasks
