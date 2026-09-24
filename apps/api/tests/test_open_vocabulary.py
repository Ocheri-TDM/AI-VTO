from conftest import make_product
from sqlalchemy import func, select

from app.application.index_query import IndexQueryService
from app.application.research_planner import ResearchPlanner
from app.database.index import IndexRepository
from app.database.models import ProductConcept, ProductConceptOffer


async def test_unknown_supplier_category_becomes_searchable_without_code_change(repository, settings):
    index = IndexRepository(repository.sessions, settings)
    product = make_product(
        id="gifts:smart-tag", supplier="gifts", source_url="https://gifts.ru/id/smart-tag",
        name="Умная багажная метка Voyager", category=None,
        metadata={
            "supplier_category": "Умные багажные метки",
            "breadcrumbs": ["Путешествия", "Умные багажные метки"],
        },
    )
    await index.ingest([product])
    found, metrics = await IndexQueryService(index).search(
        await ResearchPlanner().parse("умные багажные метки")
    )
    assert [item.id for item in found] == ["gifts:smart-tag"]
    assert metrics["browser_invoked"] is False
    async with repository.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(ProductConcept)) == 2
        assert await db.scalar(select(func.count()).select_from(ProductConceptOffer)) == 2


async def test_compound_concepts_and_typo_do_not_expand_to_neighbor_category(repository, settings):
    index = IndexRepository(repository.sessions, settings)
    await index.ingest([
        make_product(
            id="gifts:key-opener", supplier="gifts", source_url="https://gifts.ru/id/key-opener",
            name="Брелок-открывалка", metadata={"supplier_category": "Брелоки-открывалки"},
        ),
        make_product(
            id="gifts:flask", supplier="gifts", source_url="https://gifts.ru/id/flask",
            name="Карманная фляжка", metadata={"supplier_category": "Фляжки"},
        ),
        make_product(
            id="gifts:bottle", supplier="gifts", source_url="https://gifts.ru/id/bottle",
            name="Бутылка для воды", metadata={"supplier_category": "Бутылки"},
        ),
    ])
    opener, _ = await IndexQueryService(index).search(await ResearchPlanner().parse("открывалки"))
    typo, _ = await IndexQueryService(index).search(await ResearchPlanner().parse("фляшка"))
    assert [item.id for item in opener] == ["gifts:key-opener"]
    assert [item.id for item in typo] == ["gifts:flask"]
    assert all(item.id != "gifts:bottle" for item in typo)
