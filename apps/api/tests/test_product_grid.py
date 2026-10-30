from app.application.index_research import IndexedResearchService
from app.database.index import IndexRepository
from app.database.research import ResearchRepository
from app.domain.models import Product


def service(repository, settings):
    return IndexedResearchService(
        ResearchRepository(repository.sessions),
        [],
        settings,
        None,
        IndexRepository(repository.sessions, settings),
    )


async def test_local_constraints_and_old_set_wording(repository, settings):
    grid = service(repository, settings)
    intent = await grid.grid_plan("Чёрные ручки, красные термокружки и синие рюкзаки. От 300 шт.")
    assert [b.category for b in intent.branches] == ["pen", "thermomug", "backpack"]
    assert [b.colors for b in intent.branches] == [["BLACK"], ["RED"], ["BLUE"]]
    assert [b.quantity for b in intent.branches] == [300, 300, 300]
    intent = await grid.grid_plan("Собери 10 наборов из ручек и кружек")
    assert len(intent.branches) == 2
    assert all(b.quantity is None for b in intent.branches)


async def test_open_catalog_category_evidence_is_not_limited_to_title(repository, settings):
    from app.database.conversations import ConversationRepository

    grid = service(repository, settings)
    chat = await ConversationRepository(repository.sessions).create("Open vocabulary")
    await grid.index.ingest(
        [
            Product(
                id="vto:flask",
                supplier="vto",
                name="Steel flask model",
                source_url="https://vto.kz/product/flask/",
                metadata={"supplier_category": "фляжки", "breadcrumbs": ["фляжки"]},
            )
        ]
    )
    r = await grid.start(chat, "фляжки")
    assert [p["id"] for p in (await grid.grid_response(r))["products"]] == ["vto:flask"]


async def test_purpose_is_not_replaced_by_catalog_token_correction(repository, settings):
    from types import SimpleNamespace

    from app.database.conversations import ConversationRepository

    grid = service(repository, settings)

    class Correction:
        async def understand(self, query):
            return SimpleNamespace(
                search_query="Подбери для для pack в дороге, без одежда, до 7000 тенге",
                model_dump=lambda **kw: {"confidence": 0.9},
            )

    grid.query_understanding = Correction()
    chat = await ConversationRepository(repository.sessions).create("Purpose")
    await grid.index.ingest(
        [
            Product(
                id="vto:purpose",
                supplier="vto",
                name="Ручка шариковая",
                original_price=500,
                original_currency="KZT",
                source_url="https://vto.kz/product/p/",
            )
        ]
    )
    query = "Подбери подарки для сотрудников в дороге, без одежды, до 7000 тенге"
    r = await grid.start(chat, query)
    assert r.intent.normalized_query == query and r.intent.purpose
    assert all(not b.category.startswith("dynamic:") for b in r.intent.branches)
    assert (await grid.grid_response(r))["filtered_total"] == 1


async def test_full_pool_brand_paging_selection_and_reset(repository, settings):
    grid = service(repository, settings)
    from app.database.conversations import ConversationRepository

    chat = await ConversationRepository(repository.sessions).create("Grid")
    items = [
        Product(
            id=f"vto:{i:04}",
            supplier="vto",
            name=f"Ручка шариковая {i}",
            source_url=f"https://vto.kz/product/pen-{i}/",
            brand="Molti" if i >= 105 else "Other",
            original_price=100 + i,
            original_currency="KZT",
            stock_quantity=400,
            metadata={"vto_product_id": i + 1000},
        )
        for i in range(120)
    ]
    await grid.index.ingest(items)
    r = await grid.start(chat, "ручки")
    result = await grid.grid_response(r)
    assert result["base_total"] == 120
    assert len(result["products"]) == 100
    assert result["timings"]["hydrated_objects"] == 100
    second = await grid.grid_response(r, result["next_cursor"])
    assert len(second["products"]) == 20
    assert not {p["id"] for p in result["products"]} & {p["id"] for p in second["products"]}
    from app.domain.research import ResearchFilters

    r = await grid.change(r.id, filters=ResearchFilters(brands=[" molti "]), version=0)
    filtered = await grid.grid_response(r)
    assert filtered["filtered_total"] == 15
    assert r.intent.raw_query == "ручки"
    assert {p["brand"] for p in filtered["products"]} == {"Molti"}
    r = await grid.change(r.id, selected_ids=["vto:0119"], version=r.view.version)
    r = await grid.change(r.id, filters=ResearchFilters(brands=["Other"]), version=r.view.version)
    assert (await grid.grid_response(r))["hidden_selected_count"] == 1
    r = await grid.change(r.id, restore="reset", version=r.view.version)
    assert (await grid.grid_response(r))["filtered_total"] == 120
    assert r.view.selected_ids == ["vto:0119"]


async def test_empty_branch_union_and_handle_precision(repository, settings):
    grid = service(repository, settings)
    from app.database.conversations import ConversationRepository

    chat = await ConversationRepository(repository.sessions).create("Grid")
    await grid.index.ingest(
        [
            Product(
                id="vto:1", supplier="vto", name="Ручка шариковая", source_url="https://vto.kz/product/pen/"
            ),
            Product(
                id="vto:2",
                supplier="vto",
                name="Термокружка с ручкой",
                source_url="https://vto.kz/product/mug/",
            ),
        ]
    )
    r = await grid.start(chat, "ручки, рюкзаки")
    result = await grid.grid_response(r)
    assert [p["id"] for p in result["products"]] == ["vto:1"]
    assert result["empty_branches"]


async def test_brand_or_dimensions_and_latest_zero_do_not_keep_invalid_source_match(repository, settings):
    grid = service(repository, settings)
    from app.database.conversations import ConversationRepository
    from app.domain.models import ProductColor
    from app.domain.research import ResearchFilters

    chat = await ConversationRepository(repository.sessions).create("Original constraints")
    products = [
        Product(
            id=f"vto:{i}",
            supplier="vto",
            name="Ручка шариковая",
            brand=brand,
            original_price=100,
            original_currency="KZT",
            stock_quantity=400,
            colors=[ProductColor(original_color="чёрный")],
            source_url="https://vto.kz/product/p/",
        )
        for i, brand in [(1, "Molti"), (2, "Other")]
    ]
    await grid.index.ingest(products)
    r = await grid.start(chat, "Чёрные ручки от 300 шт.")
    r = await grid.change(
        r.id, filters=ResearchFilters(brands=["Molti", "Other"], colors=["BLACK"], max_price=500)
    )
    assert (await grid.grid_response(r))["filtered_total"] == 2
    r = await grid.change(r.id, restore="reset")
    assert r.intent.branches[0].quantity == 300 and r.intent.branches[0].colors == ["BLACK"]
    products[0].stock_quantity = products[0].total_quantity = 0
    await grid.index.ingest([products[0]])
    assert [p["id"] for p in (await grid.grid_response(r))["products"]] == ["vto:2"]


async def test_multi_concept_offer_occurs_once_in_first_matching_branch(repository, settings):
    grid = service(repository, settings)
    from app.database.conversations import ConversationRepository

    chat = await ConversationRepository(repository.sessions).create("Hybrid")
    await grid.index.ingest(
        [
            Product(
                id="vto:hybrid",
                supplier="vto",
                name="Ручка и вечный карандаш 2 в 1",
                source_url="https://vto.kz/product/p/",
            )
        ]
    )
    r = await grid.start(chat, "ручки, карандаши")
    result = await grid.grid_response(r)
    assert len(result["products"]) == 1
    assert result["products"][0]["matched_categories"] == ["pen", "pencil"]


async def test_archived_case_survives_and_explicit_replay_uses_grid(repository, settings):
    from app.application.project_queries import ProjectQueryService
    from app.database.conversations import ConversationRepository
    from app.database.models import HardRequestRecord, ProjectQuery
    from app.domain.conversation_router import ConversationQueryRouter

    conversations = ConversationRepository(repository.sessions)
    await conversations.create("Archive")
    project = (await conversations.projects())[0]["id"]
    async with repository.sessions.begin() as db:
        q = ProjectQuery(
            id="archived-query",
            project_id=project,
            type="HARD",
            query_text="Собери 10 наборов из ручек и кружек",
        )
        db.add(q)
        await db.flush()
        db.add(
            HardRequestRecord(
                id="archived-case",
                project_id=project,
                query_id=q.id,
                original_text=q.query_text,
                target_cases=10,
                status="RUNNING",
            )
        )
    grid = service(repository, settings)
    workspace = ProjectQueryService(repository.sessions, grid, ConversationQueryRouter())
    await workspace.recover()
    async with repository.sessions() as db:
        assert (await db.get(HardRequestRecord, "archived-case")).status == "ARCHIVED"
    result = await workspace.activate(project, "archived-query", None)
    assert result["research"].unified and len(result["research"].intent.branches) == 2
    async with repository.sessions() as db:
        assert await db.get(HardRequestRecord, "archived-case")


async def test_legacy_research_upgrade_preserves_effective_constraints_and_selection(repository, settings):
    from app.database.conversations import ConversationRepository
    from app.domain.research import ResearchSession

    grid = service(repository, settings)
    chat = await ConversationRepository(repository.sessions).create("Legacy selection")
    await grid.index.ingest(
        [
            Product(
                id="vto:upgrade",
                supplier="vto",
                name="Ручка шариковая",
                source_url="https://vto.kz/product/p/",
                brand="Molti",
            )
        ]
    )
    intent = await grid.planner.parse("ручки")
    r = ResearchSession(chat_id=chat, intent=intent, source_mode="index")
    r.view.selected_ids = ["vto:upgrade"]
    r.view.filters.attributes = {"brand": ["Molti"]}
    await grid.repository.save(r)
    r = await grid.grid_upgrade(r)
    assert r.unified and r.view.selected_ids == ["vto:upgrade"]
    assert r.view.filters.brands == ["Molti"]
    assert (await grid.grid_response(r))["filtered_total"] == 1
