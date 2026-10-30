import pytest

from app.domain.product_purpose import identity_inputs, purpose_match, query_relation
from app.domain.research import ProductBranch


@pytest.mark.parametrize(
    "name,category,accepted",
    [
        ("Шариковая ручка", "pen", True),
        ("Перьевая ручка", "pen", True),
        ("Fountain pen", "pen", True),
        ("Ручка для сумки", "pen", False),
        ("Шариковая ручка-карабин", "pen", True),
        ("Ballpoint pen in individual case", "pen", True),
        ("Ручка в индивидуальном футляре", "pen", True),
        ("Ручка и вечный карандаш 2 в 1", "pen", True),
        ("Ручка и вечный карандаш 2 в 1", "pencil", True),
        ("Металлическая ручка и вечный карандаш Van Gogh", "pen", True),
        ("Металлическая ручка и вечный карандаш Van Gogh", "pencil", True),
        ("Ручка-карандаш", "pen", True),
        ("Сумка для покупок без ручек, серая — прозрачная", "pen", False),
        ("Сумка для покупок без ручек, серая — прозрачная", "bag", True),
        ("Сумка Clear Fest без ручек, серая — прозрачная", "pen", False),
        ("Футляр для ручки", "pen", False),
        ("Зонт с ручкой-карабином", "pen", False),
        ("Термокружка с ручкой", "pen", False),
        ("Блокнот с ручкой", "pen", False),
        ("Подарочный набор: ручка, блокнот, повербанк", "pen", False),
        ("Коробка для комплекта с ручкой", "pen", False),
        ("Pen case", "pen", False),
        ("Бутылка с ручкой и карабином", "bottle", True),
        ("Бутылка с крышкой", "bottle", True),
        ("Чехол для бутылки", "bottle", False),
        ("Запасная крышка бутылки", "bottle", False),
        ("Держатель для бутылки", "bottle", False),
        ("Крепление для бутылки", "bottle", False),
        ("Bottle sleeve", "bottle", False),
        ("Подарочный набор с бутылкой", "bottle", False),
        ("Футляр для зонта", "umbrella", False),
        ("Обложка для ежедневника", "notebook", False),
        ("Чехол для ноутбука", "backpack", False),
        ("Блокнот с фонариком", "flashlight", False),
        ("Упаковка для аккумулятора", "powerbank", False),
    ],
)
def test_main_object_not_mention(name, category, accepted):
    # Deliberately misleading catalog category must not override the object.
    identity = identity_inputs(
        name, supplier_category={"pen": "Ручки", "bottle": "Бутылки"}.get(category, "")
    )
    assert purpose_match(identity, ProductBranch(category=category, label=category))["accepted"] is accepted


def test_model_only_title_uses_unopposed_source_type_and_structured_evidence():
    identity = identity_inputs(
        "Jotter", supplier_category="Шариковые ручки", attributes={"Тип изделия": "Пишущая ручка"}
    )
    assert purpose_match(identity, ProductBranch(category="pen", label="ручки"))["accepted"]
    assert "attributes.product_type" in identity.provenance


def test_open_concept_category_is_not_proof_for_an_explicit_related_object():
    branch = ProductBranch(category="dynamic:flask", label="фляжки")
    assert purpose_match(identity_inputs("Steel model", supplier_category="фляжки"), branch)["accepted"]
    assert not purpose_match(identity_inputs("Крепление для фляжки", supplier_category="фляжки"), branch)[
        "accepted"
    ]


@pytest.mark.parametrize(
    "query,name,category",
    [
        ("футляры для ручек", "Футляр для ручки", "dynamic:role:case"),
        (
            "подарочный набор с ручкой и повербанком",
            "Подарочный набор с ручкой и повербанком",
            "dynamic:role:set",
        ),
        ("блокнот с ручкой", "Блокнот с ручкой", "notebook"),
    ],
)
def test_explicit_relationships(query, name, category):
    branch = ProductBranch(category=category, label=query, **query_relation(query))
    assert branch.scope == (
        "ASSOCIATED" if category == "notebook" else "SET" if category.endswith(":set") else "PACKAGING"
    )
    assert purpose_match(identity_inputs(name), branch)["accepted"]
    if branch.scope == "ASSOCIATED":
        assert branch.required_concepts == ["pen"]
        assert not purpose_match(identity_inputs("Блокнот без дополнений"), branch)["accepted"]


def test_explicitly_including_sets_is_related_and_packaging_is_not_contents():
    b = ProductBranch(category="pen", label="ручки", **query_relation("ручки, включая наборы"))
    assert purpose_match(identity_inputs("Набор из ручки и блокнота"), b)["category_match"] == "RELATED"
    assert not purpose_match(
        identity_inputs("Коробка для набора с ручкой", description="В составе: ручка, блокнот"), b
    )["accepted"]


async def test_resolved_association_survives_catalog_token_correction(repository, settings):
    from types import SimpleNamespace

    from app.application.index_research import IndexedResearchService
    from app.database.index import IndexRepository
    from app.database.research import ResearchRepository

    service = IndexedResearchService(
        ResearchRepository(repository.sessions),
        [],
        settings,
        None,
        IndexRepository(repository.sessions, settings),
    )

    class Correction:
        async def understand(self, query):
            return SimpleNamespace(search_query="пеналы с ручками", model_dump=lambda **kw: {})

    service.query_understanding = Correction()
    intent = await service.grid_plan("блокнот с ручкой")
    assert intent.categories == ["notebook"]
    assert intent.branches[0].scope == "ASSOCIATED"
    assert intent.branches[0].required_concepts == ["pen"]
    assert intent.normalized_query == "блокнот с ручкой"


def test_set_contents_are_not_accessory_compatibility_or_handles():
    branch = ProductBranch(category="pen", label="ручки", include_sets=True)
    assert not purpose_match(identity_inputs("Набор чехлов для ручек"), branch)["accepted"]
    assert not purpose_match(identity_inputs("Набор бутылок с ручкой"), branch)["accepted"]
    assert purpose_match(identity_inputs("Набор из ручки и блокнота"), branch)["accepted"]


async def test_whole_pool_purpose_validation_survives_filter_pagination_and_undo(repository, settings):
    from app.application.index_research import IndexedResearchService
    from app.database.conversations import ConversationRepository
    from app.database.index import IndexRepository
    from app.database.research import ResearchRepository
    from app.domain.models import Product, ProductColor
    from app.domain.research import ResearchFilters

    index = IndexRepository(repository.sessions, settings)
    service = IndexedResearchService(ResearchRepository(repository.sessions), [], settings, None, index)
    chat = await ConversationRepository(repository.sessions).create("Purpose pagination")
    products = [
        Product(
            id=f"vto:{i:04}",
            supplier="vto",
            name=f"Шариковая ручка {i}",
            source_url="https://vto.kz/product/pen/",
            colors=[ProductColor(original_color="красный")],
        )
        for i in range(125)
    ]
    products.extend(
        Product(
            id=f"vto:{i:04}",
            supplier="vto",
            name=name,
            category="pen",
            source_url="https://vto.kz/product/related/",
            colors=[ProductColor(original_color="красный")],
        )
        for i, name in enumerate(
            ["Блокнот с ручкой", "Коробка для ручки", "Набор с ручкой", "Футляр для ручки"], 150
        )
    )
    await index.ingest(products)
    r = await service.start(chat, "ручки бутылки")
    assert [b.category for b in r.intent.branches] == ["pen", "bottle"]
    assert len(r.pool_offer_ids) == 125
    r = await service.change(r.id, filters=ResearchFilters(colors=["RED"]))
    first = await service.grid_response(r)
    r = await service.change(r.id, selected_ids=[first["products"][0]["id"]])
    second = await service.grid_response(r, first["next_cursor"])
    assert [len(first["products"]), len(second["products"])] == [100, 25]
    assert all(p["purpose_match"]["accepted"] for p in first["products"] + second["products"])
    explicit = await service.start(chat, "футляры для ручек")
    assert explicit.pool_offer_ids == ["vto:0153"]  # A box is not silently called a case.
