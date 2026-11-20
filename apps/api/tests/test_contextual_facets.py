"""Independent labeled offers: expected values and IDs are not query-derived."""

import pytest
from test_product_grid import service

from app.database.conversations import ConversationRepository
from app.database.models import OfferAttributeProjection, OfferAttributeValue
from app.domain.models import Product, ProductColor
from app.domain.product_attributes import attribute_projection, normalize_value
from app.domain.research import ResearchFilters


def labeled_products():
    def product(
        identifier, name, attributes, *, size=None, parent=None, stock=300, color="красный", brand="Example"
    ):
        return Product(
            id="vto:facet-" + identifier,
            supplier="vto",
            name=name,
            source_url="https://vto.kz/product/facet-" + identifier + "/",
            original_price=1000,
            original_currency="KZT",
            stock_quantity=stock,
            brand=brand,
            material="Сталь",
            colors=[ProductColor(original_color=color)],
            metadata={"attributes": attributes, "size": size, "vto_product_id": parent or identifier},
        )

    bottles = [product(f"b{i:03}", "Бутылка Test", {"Объем": "500 мл"}) for i in range(101)]
    bottles += [
        product("b101", "Бутылка Test", {"Объем": "0,5 л"}),
        product("b102", "Бутылка Test", {"Объем": "0.5 l"}),
        product("b103", "Бутылка Test", {"Объем": "750 мл"}),
        product("b104", "Бутылка Test", {"Объем": "900 мл"}),
        product("z-last", "Бутылка Test", {"Объем": "1250 мл"}),
    ]
    return bottles + [
        product("m350", "Кружка Morning", {"Объем": "350 мл"}, size="JINGLE BELLS"),
        product("m500", "Кружка Morning", {"Объем": "500 мл"}),
        product("u100", "Зонт Spring", {"Диаметр купола": "100 см", "Механизм": "автомат"}),
        product("u110", "Зонт Spring", {"Диаметр купола": "110 см", "Механизм": "механический"}),
        *[
            product("t" + size, "Футболка Basic", {"Размер": size}, size=size)
            for size in ["S", "M", "L", "XL", "L V2", "M V2"]
        ],
        product(
            "child", "Футболка детская", {"Размер": "9–12 лет (134–152 см)"}, size="9–12 лет (134–152 см)"
        ),
        *[product("n" + size, "Блокнот Study", {"Формат": size}, size=size) for size in ["A4", "A5", "A6"]],
        product("shared1", "Бутылка Colors", {"Объем": "500 мл"}, size="500 мл", parent="siblings"),
        product(
            "shared2", "Бутылка Colors", {"Объем": "500 мл"}, size="750 мл", parent="siblings", color="синий"
        ),
        product("unknown", "Бутылка Unknown", {"Вес": "500 г", "Объем транспортной упаковки": "900 мл"}),
        product("box", "Коробка для бутылки", {"Объем": "999 мл"}),
        product("battery", "Внешний аккумулятор Power", {"Емкость": "10000 мАч"}),
        product("bag", "Рюкзак Travel", {"Объем": "20 л", "Диагональ ноутбука": '15.6"'}),
    ]


def groups(value):
    return {g["category"]: {a["id"]: a for a in g["attributes"]} for g in value["category_facets"]}


def counts(value, category, attribute):
    return {v["value"]: v["count"] for v in groups(value)[category][attribute]["values"]}


@pytest.fixture
async def facets(repository, settings):
    grid = service(repository, settings)
    await grid.index.ingest(labeled_products())
    chat = await ConversationRepository(repository.sessions).create("Independent attribute fixture")
    return grid, chat


async def test_normalization_and_evidence_are_not_generic_sizes():
    assert normalize_value("capacity_ml", "0,5 л") == "500"
    assert normalize_value("capacity_ml", "0.5 l") == "500"
    assert normalize_value("capacity_ml", "500 мл") == "500"
    assert normalize_value("capacity_ml", "500 г") is None
    assert normalize_value("apparel_size", "L V2") is None
    assert normalize_value("apparel_size", "JINGLE BELLS") is None
    assert normalize_value("paper_format", "A5") == "A5"
    _, rows, _ = attribute_projection(labeled_products()[-2].model_dump(mode="json"))
    assert rows[0]["attribute_id"] == "capacity_mah" and rows[0]["value_key"] == "10000"
    for payload in [
        {
            "name": "Бутылка Example",
            "metadata": {"attributes": {"Объем": "500 мл"}, "variant_attributes": {"capacity": "L V2"}},
        },
        {"name": "Бутылка в транспортной упаковке 500 мл", "metadata": {}},
    ]:
        assert not attribute_projection(payload)[1]
    assert (
        attribute_projection({"name": "Бутылка Example", "metadata": {"attributes": {"Объем, мл": 500}}})[1][
            0
        ]["value_key"]
        == "500"
    )
    for invalid in ["invalid-number", "1e-999999999", "1.0000001"]:
        with pytest.raises(ValueError):
            ResearchFilters(
                branch_attributes={"00000000-0000-4000-8000-000000000001": {"capacity_ml": [invalid]}}
            )


async def test_complete_pool_facets_and_last_page_value(facets):
    grid, chat = facets
    r = await grid.start(chat, "Бутылки, кружки, зонты")
    first = await grid.grid_response(r)
    assert first["matching_count"] == 113
    assert len(first["products"]) == 100
    assert "vto:facet-z-last" not in {p["id"] for p in first["products"]}
    assert counts(first, "bottle", "capacity_ml") == {"500": 104, "750": 2, "900": 1, "1250": 1}
    assert counts(first, "mug", "capacity_ml") == {"350": 1, "500": 1}
    assert counts(first, "umbrella", "canopy_diameter_cm") == {"100": 1, "110": 1}
    assert first["facets"]["size"] == []
    second = await grid.grid_response(r, first["next_cursor"])
    assert first["category_facets"] == second["category_facets"]
    assert first["facet_context"] == second["facet_context"]
    assert "vto:facet-z-last" in {p["id"] for p in first["products"] + second["products"]}
    assert all(p["purpose_match"]["accepted"] for p in first["products"] + second["products"])


async def test_false_catalog_category_and_multi_purpose_union(facets):
    grid, chat = facets
    await grid.index.ingest(
        [
            Product(
                id="vto:facet-foreign",
                supplier="vto",
                category="bottle",
                name="Жилет флисовый «New Bellagio» — M, бутылочный зелёный",
                source_url="https://vto.kz/product/vest/",
                metadata={"supplier_category": "Жилеты", "size": "M"},
            ),
            Product(
                id="vto:facet-hybrid",
                supplier="vto",
                name="Бутылка-кружка 2 в 1",
                source_url="https://vto.kz/product/hybrid/",
                metadata={"attributes": {"Объем": "500 мл"}},
            ),
        ]
    )
    r = await grid.start(chat, "Бутылки, кружки, зонты")
    value = await grid.grid_response(r)
    assert "vto:facet-foreign" not in r.pool_offer_ids
    assert value["matching_count"] == 114  # hybrid appears once in the union
    assert sum("vto:facet-hybrid" in ids for ids in r.branch_offer_ids) == 2
    assert counts(value, "bottle", "capacity_ml")["500"] == 105
    assert counts(value, "mug", "capacity_ml")["500"] == 2


async def test_branch_or_self_exclusion_zero_and_visibility(facets):
    grid, chat = facets
    r = await grid.start(chat, "Бутылки, кружки, зонты")
    bottle, mug, umbrella = r.intent.branches
    f = ResearchFilters(branch_attributes={bottle.id: {"capacity_ml": ["500"]}})
    r = await grid.change(r.id, version=r.view.version, filters=f)
    value = await grid.grid_response(r)
    assert value["matching_count"] == 108
    assert counts(value, "bottle", "capacity_ml")["750"] == 2
    assert {b["concept"]: b["count"] for b in value["query_categories"]} == {
        "bottle": 104,
        "mug": 2,
        "umbrella": 2,
    }
    f.branch_attributes[mug.id] = {"capacity_ml": ["350"]}
    r = await grid.change(r.id, version=r.view.version, filters=f)
    assert (await grid.grid_response(r))["matching_count"] == 107
    f.branch_attributes[bottle.id] = {"capacity_ml": ["9999"]}
    r = await grid.change(r.id, version=r.view.version, filters=f)
    value = await grid.grid_response(r)
    assert value["matching_count"] == 3
    assert counts(value, "bottle", "capacity_ml")["9999"] == 0
    option = next(v for v in groups(value)["bottle"]["capacity_ml"]["values"] if v["value"] == "9999")
    assert option["selected"] and not option["disabled"]
    r = await grid.change(
        r.id, version=r.view.version, branch_actions=[{"action": "hide", "branch_id": bottle.id}]
    )
    assert "bottle" not in groups(await grid.grid_response(r))
    assert r.view.filters.branch_attributes[bottle.id] == {"capacity_ml": ["9999"]}
    r = await grid.change(
        r.id, version=r.view.version, branch_actions=[{"action": "show", "branch_id": bottle.id}]
    )
    assert counts(await grid.grid_response(r), "bottle", "capacity_ml")["9999"] == 0
    restored = await grid.repository.get(r.id)
    assert restored.view.filters.branch_attributes == f.branch_attributes


async def test_typed_characteristics_and_variant_precedence(facets):
    grid, chat = facets
    shirts = await grid.start(chat, "Футболки")
    sizes = counts(await grid.grid_response(shirts), "tshirt", "apparel_size")
    assert set(sizes) == {"S", "M", "L", "XL", "9–12 лет (134–152 см)"}
    notebooks = await grid.start(chat, "Блокноты")
    assert counts(await grid.grid_response(notebooks), "notebook", "paper_format") == {
        "A4": 1,
        "A5": 1,
        "A6": 1,
    }
    bottles = await grid.start(chat, "Бутылки Colors")
    f = ResearchFilters(branch_attributes={bottles.intent.branches[0].id: {"capacity_ml": ["750"]}})
    bottles = await grid.change(bottles.id, version=bottles.view.version, filters=f)
    rows = (await grid.grid_response(bottles))["products"]
    assert {p["id"] for p in rows} == {"vto:facet-shared2"}


async def test_required_source_values_and_manual_common_choices(facets):
    grid, chat = facets
    r = await grid.start(chat, "Бутылки 500 мл")
    assert set(counts(await grid.grid_response(r), "bottle", "capacity_ml")) == {"500"}
    r = await grid.start(chat, "Бутылки")
    r = await grid.change(
        r.id,
        version=r.view.version,
        effective_changes=[{"field": "colors", "action": "set", "values": ["BLUE"]}],
    )
    value = await grid.grid_response(r)
    assert value["matching_count"] == 1
    assert {v["value"] for v in value["facets"]["color"]} == {"BLUE", "RED"}
    r = await grid.change(r.id, version=r.view.version, restore="reset")
    assert (await grid.grid_response(r))["matching_count"] == 109


async def test_or_within_attribute_and_and_between_attributes(facets):
    grid, chat = facets
    r = await grid.start(chat, "Зонты")
    branch = r.intent.branches[0]
    r = await grid.change(
        r.id,
        version=r.view.version,
        filters=ResearchFilters(
            branch_attributes={
                branch.id: {"canopy_diameter_cm": ["100", "110"], "umbrella_mechanism": ["AUTOMATIC"]}
            }
        ),
    )
    value = await grid.grid_response(r)
    assert {p["id"] for p in value["products"]} == {"vto:facet-u100"}
    assert counts(value, "umbrella", "umbrella_mechanism") == {"AUTOMATIC": 1, "MANUAL": 1}
    assert counts(value, "umbrella", "canopy_diameter_cm") == {"100": 1, "110": 0}


async def test_scope_validation_and_idempotent_projection(facets):
    grid, chat = facets
    r = await grid.start(chat, "Бутылки")
    with pytest.raises(ValueError, match="UNSUPPORTED_ATTRIBUTE"):
        await grid.change(
            r.id,
            version=r.view.version,
            filters=ResearchFilters(branch_attributes={r.intent.branches[0].id: {"paper_format": ["A5"]}}),
        )
    with pytest.raises(ValueError):
        ResearchFilters(branch_attributes={r.intent.branches[0].id: {"DROP TABLE": ["1"]}})
    from sqlalchemy import func, select

    async with grid.index.sessions() as db:
        before = await db.scalar(select(func.count()).select_from(OfferAttributeValue))
        header = await db.get(OfferAttributeProjection, "vto:facet-shared2")
        old_time = header.derived_at
    await grid.index.ingest(labeled_products())
    async with grid.index.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(OfferAttributeValue)) == before
        assert (await db.get(OfferAttributeProjection, "vto:facet-shared2")).derived_at == old_time
