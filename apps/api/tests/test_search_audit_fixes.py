"""Independent truth for the November 25 search audit, including complete pages."""

from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.database.models import Base
from app.domain.models import Product, ProductColor
from app.main import create_app


def audit_products(*, many=False):
    def product(id, name, *, color="красный", stock=9000, price=200, attrs=None, sku=None, size=None):
        return Product(
            id=f"vto:audit-{id}", supplier="vto", name=name,
            source_url=f"https://vto.kz/product/audit-{id}/", original_currency="KZT",
            original_price=price, stock_quantity=stock,
            colors=[ProductColor(original_color=color)],
            metadata={"attributes": attrs or {}, "sku": sku, "size": size, "parent_name": name},
        )

    items = [
        product("pen-red", "Ручка Tippo", sku="P-100.10"),
        product("pen-green", "Ручка Tippo", color="зелёный", stock=8100),
        product("pen-blue", "Ручка Tippo", color="синий", stock=7000),
        product("pen-burgundy", "Ручка Tippo", color="бордовый"),
        product("pen-maroon", "Ручка Maroon", color="maroon"),
        product("pen-red2", "Ручка другая", stock=300),
        product("pen-model", "Ручка A5 Max"),
        product("diary-a5", "Ежедневник Study", attrs={"Формат": "А5"}),
        product("diary-a5-variant", "Ежедневник Study A5", attrs={"Формат": "A5"}, size="А5"),
        product("diary-a5plus", "Ежедневник Study", attrs={"Формат": "A5+"}),
        product("diary-a6", "Ежедневник Study A6", attrs={"Формат": "A6"}),
        product("diary-model-a5", "Ежедневник модель A5 Max", attrs={"Формат": "A6"}),
        product("bottle-a", "Бутылка River", price=800, attrs={"Объем": "750 мл"}),
        product("bottle-b", "Бутылка River", price=300, attrs={"Объем": "750 мл"}),
        product("bottle-c", "Бутылка River", price=300, attrs={"Объем": "750 мл"}),
        product("bottle-unknown", "Бутылка River", price=None, attrs={"Объем": "750 мл"}),
        product("bottle-500", "Бутылка River", attrs={"Объем": "500 мл"}),
        product("bottle-half", "Бутылка River", attrs={"Объем": "0,5 л"}),
        product("mug-red", "Термокружка North", attrs={"Объем": "500 мл"}),
        product("mug-blue", "Термокружка North", color="синий", attrs={"Объем": "0,5 л"}),
        product("mug-navy", "Термокружка North", color="navy", attrs={"Объем": "500 мл"}),
        product("bag-sku", "Рюкзак Melango", color="чёрный", stock=1527, sku="GF-12449.30"),
        product("bag-sibling", "Рюкзак Melango", color="синий", stock=5000, sku="GF-12449.40"),
        product("power", "Внешний аккумулятор", attrs={"Емкость": "10000 мАч"}),
        product("pencil-red", "Карандаш"),
        product("shopper-red", "Сумка-шоппер"),
    ]
    for noun, name in [("pen", "Ручка шариковая"), ("power", "Внешний аккумулятор"),
                        ("shopper", "Сумка-шоппер"), ("mug", "Термокружка"), ("pencil", "Карандаш")]:
        items.extend(product(f"{noun}-{shade}", name, color=color, stock=250)
                     for shade, color in [("dark", "тёмно-синий"), ("navy2", "navy blue"), ("light", "голубой")])
    if many:
        items.extend(product(f"bottle-page-{i:03}", "Бутылка River", price=500+i,
                             attrs={"Объем": "750 мл"}) for i in range(105))
        items.append(product("z-cheapest", "Бутылка River", price=1, attrs={"Объем": "750 мл"}))
    return items


class Workspace:
    def __init__(self, client, app, project, chat):
        self.client, self.app, self.project, self.chat = client, app, project, chat
        self.url = f"/api/projects/{project}/requests"

    async def send(self, query, key=None, *, status=202):
        response = await self.client.post(self.url, json={"query": query, "request_id": key or str(uuid4())})
        assert response.status_code == status, (query, response.status_code, response.text[:1200])
        return response.json()

    async def research(self, query):
        return (await self.send(query))["research"]

    async def active(self):
        response = await self.client.get(f"/api/researches/chat/{self.chat}")
        response.raise_for_status()
        return response.json()

    async def change(self, r, **changes):
        response = await self.client.patch(f"/api/researches/{r['id']}/view",
                                           json={"version": r["view"]["version"], **changes})
        assert response.status_code == 200, response.text
        return response.json()

    async def all(self, r):
        products = list(r["products"])
        cursor = r["next_cursor"]
        while cursor:
            response = await self.client.get(f"/api/researches/{r['id']}/products",
                                             params={"cursor": cursor, "limit": 100})
            assert response.status_code == 200, response.text
            page = response.json()
            products.extend(page["products"])
            cursor = page["next_cursor"]
        assert len(products) == len({p["id"] for p in products}) == r["matching_count"]
        return products

    async def ids(self, r):
        return {p["id"].removeprefix("vto:audit-") for p in await self.all(r)}


@pytest.fixture
async def workspace(settings, request):
    app = create_app(settings, [])
    async with app.state.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    await app.state.index.ingest(audit_products(many=request.node.name.startswith("test_sort_all")))
    async with app.router.lifespan_context(app), AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        chat = (await client.post("/api/chats", json={"project_name": "Independent audit"})).json()["id"]
        project = (await client.get("/api/projects")).json()[0]["id"]
        yield Workspace(client, app, project, chat)


@pytest.mark.parametrize("old_query", ["Ежедневники", "Ручки Tippo от 8000 шт"])
@pytest.mark.parametrize("word,sorting", [("дешёвые", "price_asc"), ("дорогие", "price_desc")])
async def test_new_subject_with_sorting_replaces_context(workspace, old_query, word, sorting):
    w = workspace
    old = await w.research(old_query)
    if old_query == "Ежедневники":
        branch = old["intent"]["branches"][0]["id"]
        filters = old["view"]["filters"]
        filters["branch_attributes"] = {branch: {"paper_format": ["A5"]}}
        old = await w.change(old, filters=filters)
    result = await w.send(f"Бутылки 750 мл, сначала самые {word}")
    r = result["research"]
    assert result["route"]["action"] == "NEW_SEARCH" and r["id"] != old["id"]
    assert r["view"]["sorting"] == sorting
    assert [b["category"] for b in r["intent"]["branches"]] == ["bottle"]
    assert r["intent"]["branches"][0]["constraints"]["capacity_ml"] == 750
    assert r["intent"]["quantity"] is None and not r["view"]["filters"]["branch_attributes"]
    assert await w.ids(r) == {"bottle-a", "bottle-b", "bottle-c", "bottle-unknown"}
    assert (await w.active())["id"] == r["id"]


@pytest.mark.parametrize("command", ["Оставь только термокружки", "Покажи только термокружки из текущей выдачи"])
async def test_keep_category_preserves_source_color_and_manual_quantity(workspace, command):
    w = workspace
    r = await w.research("Ручки синие и термокружки красные")
    r = await w.change(r, effective_changes=[{"field": "minimum_quantity", "action": "set", "number": 200}])
    before = await w.ids(r)
    hidden = await w.change(r, branch_actions=[{"action": "hide", "branch_id": r["intent"]["branches"][0]["id"]}])
    expected = await w.ids(hidden)
    assert expected == {"mug-red"} and expected < before
    r = await w.change(hidden, restore="undo")
    narrowed = await w.research(command)
    assert narrowed["id"] == r["id"]
    assert await w.ids(narrowed) == expected
    assert any(b["colors"] == ["RED"] for b in narrowed["intent"]["branches"])
    unrestricted = await w.research("Найди все термокружки любого цвета")
    assert unrestricted["id"] != r["id"] and "mug-blue" in await w.ids(unrestricted)


async def test_sku_with_quantity_is_new_subject_at_bound(workspace):
    w = workspace
    old = await w.research("Бутылки")
    direct = await w.research("GF-12449.30 от 1527 шт")
    assert direct["id"] != old["id"]
    assert await w.ids(direct) == {"bag-sku"}
    assert direct["intent"]["branches"][0]["constraints"]["sku"] == "GF-12449.30"
    alone = await w.research("GF-12449.30")
    refined = await w.research("Теперь от 1527 шт")
    assert refined["id"] == alone["id"] and await w.ids(refined) == {"bag-sku"}
    above = await w.research("GF-12449.30 от 1528 шт")
    assert await w.ids(above) == set()


async def test_sort_all_pages_with_numeric_ties_unknown_and_late_cheapest(workspace):
    w = workspace
    initial = await w.research("Бутылки 750 мл")
    assert "vto:audit-z-cheapest" not in [p["id"] for p in initial["products"]]
    before = await w.ids(initial)
    for word, direction in [("дешёвые", "price_asc"), ("дорогие", "price_desc")]:
        result = await w.research("Сначала самые " + word)
        assert result["id"] == initial["id"] and result["view"]["sorting"] == direction
        products = await w.all(result)
        actual = [(p["price_kzt"], p["id"]) for p in products]
        expected = sorted(actual, key=lambda v: (v[0] is None, 0 if v[0] is None else v[0] * (-1 if direction == "price_desc" else 1), v[1]))
        assert actual == expected
        assert {i.removeprefix("vto:audit-") for _, i in actual} == before
        if direction == "price_asc":
            assert result["products"][0]["id"] == "vto:audit-z-cheapest"
    mixed = await w.research("Ручки и бутылки, сортируй по цене по возрастанию")
    prices = [p["price_kzt"] for p in await w.all(mixed) if p["price_kzt"] is not None]
    assert prices == sorted(prices)


@pytest.mark.parametrize("category,expected", [("Бутылки", {"bottle-500", "bottle-half"}), ("Термокружки", {"mug-red", "mug-blue", "mug-navy"})])
async def test_volume_preposition_and_decimal_equivalence(workspace, category, expected):
    for suffix in ["по 500 мл", "объёмом 500 мл", "0,5 л", "по 0.5 л"]:
        r = await workspace.research(f"{category} {suffix}")
        assert await workspace.ids(r) == expected
        assert r["intent"]["branches"][0]["constraints"]["capacity_ml"] == 500
        assert not r["intent"]["models"]


@pytest.mark.parametrize("number", ["8000", "8 000", "8\u00a0000", "8\u202f000"])
async def test_grouped_quantity_and_price_in_both_orders(workspace, number):
    for tail in [f"от {number} шт", f"от {number} шт до 300 тенге", f"до 300 тенге от {number} шт"]:
        r = await workspace.research("Ручки Tippo " + tail)
        assert r["intent"]["quantity"] == 8000
        assert await workspace.ids(r) == {"pen-red", "pen-green", "pen-burgundy"}


async def test_paper_format_is_typed_and_not_model(workspace):
    w = workspace
    r = await w.research("Ежедневники")
    branch = r["intent"]["branches"][0]["id"]
    filters = r["view"]["filters"]
    filters["branch_attributes"] = {branch: {"paper_format": ["A5"]}}
    manual = await w.change(r, filters=filters)
    expected = await w.ids(manual)
    assert expected == {"diary-a5", "diary-a5-variant"}
    for word in ["А5", "A5", "формат A5"]:
        r = await w.research("Ежедневники " + word)
        assert await w.ids(r) == expected
        assert not r["intent"]["models"] and not r["intent"]["branches"][0]["models"]
        facet = next(a for a in r["category_facets"][0]["attributes"] if a["id"] == "paper_format")
        assert facet["source_value"] == "A5"
    assert await w.ids(await w.research("Ежедневники A5+")) == {"diary-a5plus"}
    assert await w.ids(await w.research("Ручки A5 Max")) == {"pen-model"}
    assert await w.ids(await w.research("Ежедневники модель A5 Max")) == {"diary-model-a5"}


async def test_invalid_price_keeps_previous_and_corrects_the_range(workspace):
    w = workspace
    previous = await w.research("Бутылки 750 мл")
    response = await w.send("Ручки от 7000 до 500 тенге", status=422)
    assert "Минимальная цена выше максимальной" in str(response["detail"])
    assert (await w.active())["id"] == previous["id"]
    history = (await w.client.get(f"/api/chats/{w.chat}/history")).json()["messages"]
    assert "Минимальная цена выше максимальной" in history[-1]["content"]
    corrected = await w.research("От 100 до 500 тенге")
    assert [b["category"] for b in corrected["intent"]["branches"]] == ["pen"]
    assert corrected["matching_count"] > 0
    narrowed = await w.research("Убери всё дороже 150")
    assert narrowed["id"] == corrected["id"] and narrowed["matching_count"] == 0
    restored = await w.research("Верни предыдущий вариант")
    assert await w.ids(restored) == await w.ids(corrected)


async def test_price_ranges_do_not_conflict_between_independent_categories(workspace):
    w = workspace
    research = await w.research("Ручки от 7000 тенге и бутылки до 500 тенге")
    branches = {b["category"]: b for b in research["intent"]["branches"]}
    assert (branches["pen"]["min_price"], branches["pen"]["max_price"]) == (7000, None)
    assert (branches["bottle"]["min_price"], branches["bottle"]["max_price"]) == (None, 500)
    assert await w.ids(research) == {"bottle-b", "bottle-c", "bottle-500", "bottle-half"}


async def test_negative_shade_is_not_exclusion_of_all_red(workspace):
    w = workspace
    for initial in ("Ручки от 999999 шт", "Ручки"):
        await w.research(initial)
        r = await w.research("Красные ручки без бордовых")
        assert await w.ids(r) == {"pen-red", "pen-red2", "pen-model"}
        assert r["intent"]["branches"][0]["colors"] == ["RED"]
        assert any("бордов" in message.casefold() for message in r["identity_notices"])


async def test_strict_navy_policy_and_explicit_near_shades(workspace):
    w = workspace
    prefix = "Внешние аккумуляторы, шопперы, термокружки и карандаши. Цвет "
    dark = await w.research(prefix + "тёмно-синий. Тираж 150 шт.")
    navy = await w.research(prefix + "navy blue. Тираж 150 шт.")
    assert await w.ids(dark) == {"power-dark", "shopper-dark", "mug-dark", "pencil-dark"}
    assert await w.ids(navy) == {"power-navy2", "shopper-navy2", "mug-navy2", "pencil-navy2", "mug-navy"}
    near = await w.research(prefix + "navy blue. Можно близкие оттенки. Тираж 150 шт.")
    assert await w.ids(navy) | await w.ids(dark) <= await w.ids(near)


@pytest.mark.parametrize("words", [("карандаш", "корондаш", "karandash"),
                                  ("повербанк", "powerbank", "power bank"),
                                  ("шоперы", "шопперы")])
async def test_category_aliases_keep_complete_identity_sets(workspace, words):
    expected = None
    for word in words:
        ids = await workspace.ids(await workspace.research("Найди " + word))
        assert ids
        if expected is None:
            expected = ids
        assert ids == expected


async def test_sentence_separators_and_soft_preferences(workspace):
    w = workspace
    a = await w.research("Ручки, ежедневники, шопперы и термокружки. Красный цвет. От 200 шт.")
    expected = await w.ids(a)
    b = await w.research("Красный цвет. От 200 шт. Ручки. Ежедневники. Шопперы. Термокружки.")
    assert await w.ids(b) == expected
    assert len(b["intent"]["branches"]) == 4
    assert all(p["availability"]["availableNow"] >= 200 for p in await w.all(b))
    soft = await w.research("Ручки Tippo, желательно красные")
    assert "pen-green" in await w.ids(soft) and soft["products"][0]["id"] in {"vto:audit-pen-red", "vto:audit-pen-burgundy"}
    hard = await w.research("Красные ручки Tippo без бордовых")
    assert await w.ids(hard) == {"pen-red"}


async def test_local_volume_and_power_capacity_are_not_quantity(workspace):
    w = workspace
    r = await w.research("Бутылки и ручки")
    before = await w.ids(r)
    bottle = next(b for b in r["intent"]["branches"] if b["category"] == "bottle")
    f = r["view"]["filters"]
    f["branch_attributes"] = {bottle["id"]: {"capacity_ml": ["500"]}}
    after = await w.ids(await w.change(r, filters=f))
    assert after == {p for p in before if p.startswith("pen-")} | {"bottle-500", "bottle-half"}
    power = await w.research("Power bank по 10000 мАч, от 200 шт")
    assert power["intent"]["quantity"] == 200
    assert await w.ids(power) == {"power"}
