"""Human-defined truth. Oracles do not import production parsers or SQL builders."""

import random

VERSION = "search-safety-20261031-v1.1"
SEED = 20261031


def products():
    rows = []

    def add(
        key, title, concept, brand="Alpha", color="BLACK", price=1500, stock=300, material="металл", **attrs
    ):
        rows.append(
            dict(
                key=key,
                id=f"vto:{9900000 + len(rows)}",
                title=title,
                concepts=concept if isinstance(concept, list) else [concept],
                brand=brand,
                color=color,
                price=price,
                stock=stock,
                material=material,
                **attrs,
            )
        )

    for color, stock in [
        ("BLACK", 300),
        ("RED", 500),
        ("BLUE", 249),
        ("WHITE", 0),
        ("GREEN", None),
        ("ORANGE", 1000),
    ]:
        add(
            "tippo-" + color,
            "Ручка шариковая Tippo",
            ["pen", "writing"],
            None,
            color,
            500,
            stock,
            "пластик",
            model="Tippo",
        )
    add("vortex", "Ручка шариковая VORTEX", "pen", "B1", "BLACK", 1800, 400, model="VORTEX")
    add("case-pen", "Ручка шариковая Vector в футляре", "pen", model="Vector")
    add("pen-case", "Футляр для ручки Molti", "pen_case", "Other", price=100)
    add("pen-box", "Пустая коробка для ручки", "pen_box", price=100)
    add("pen-set", "Подарочный набор с ручкой и блокнотом", "giftset")
    add("hybrid", "Ручка и вечный карандаш 2 в 1", ["pen", "pencil"], material="металл", model="Eternal")
    for brand in ["Alpha", "Beta"]:
        add(
            "galaxy-" + brand,
            "Карандаш Галактика",
            "pencil",
            brand,
            "BLACK",
            700,
            500,
            "дерево",
            model="Галактика",
        )
        add(
            "deep-" + brand, "Ручка шариковая Deep Space", "pen", brand, "BLUE", 1700, 700, model="Deep Space"
        )
    for color in ["BLACK", "BLUE", "RED", "GREY"]:
        add(
            "melango-" + color,
            "Рюкзак-мешок Melango",
            "backpack",
            "Molti",
            color,
            2084,
            600,
            "полиэстер",
            model="Melango",
            sku="GF-12449." + {"BLACK": "30", "BLUE": "40", "RED": "50", "GREY": "10"}[color],
        )
    add(
        "bottle-500",
        "Бутылка с карабином River 500 мл",
        "bottle",
        "Molti",
        "BLACK",
        2500,
        80,
        "металл",
        capacity_ml=500,
        model="River",
    )
    add(
        "bottle-750",
        "Бутылка River 750 мл",
        "bottle",
        "Beta",
        "BLUE",
        3500,
        300,
        "пластик",
        capacity_ml=750,
        model="River",
    )
    add("bottle-holder", "Держатель для бутылки", "bottle_holder", price=300)
    add("mug", "Кружка керамическая с ручкой", "mug", material="керамика")
    add("thermal", "Термокружка North", "thermomug", "Alpha", "NAVY", 3000, 200, "сталь")
    add("tumbler", "Термостакан City", "thermocup", "Beta", "LIGHT_BLUE", 2800, 200, "сталь")
    add("thermos", "Термос Trek", "thermos", "Alpha", "BLUE", 4000, 500, "сталь")
    add("umbrella", "Зонт с ручкой", "umbrella")
    add("diary", "Ежедневник датированный Horizon", "notebook", material="кожа", subtype="diary")
    add("notepad", "Блокнот Plain", "notebook", material="бумага", subtype="notepad")
    add("shopper", "Сумка-шоппер Cotton", "shopperbag", material="хлопок")
    add("bag", "Спортивная сумка Carry", "bag", material="полиэстер")
    add("power", "Внешний аккумулятор Charge 10000 мАч", "powerbank", capacity_mah=10000)
    add("power-small", "Внешний аккумулятор Charge 5000 мАч", "powerbank", capacity_mah=5000)
    add("charger", "Сетевое зарядное устройство USB", "charger")
    add("flash", "USB-накопитель Storage 32 ГБ", "flashdrive", storage_gb=32)
    add("humidifier", "USB-увлажнитель Egg", "humidifier")
    add("lamp", "USB-лампа Read", "lamp")
    add("hub", "USB-хаб Connect", "hub")
    add("shirt-xl", "Футболка Cotton красная XL", "tshirt", "Alpha", "RED", 2000, 250, "хлопок", size="XL")
    add("shirt-s", "Футболка Cotton красная S", "tshirt", "Alpha", "RED", 2000, 150, "хлопок", size="S")
    add("no-price", "Бутылка NoPrice", "bottle", price=None)
    add("stale", "Бутылка OldPositive", "bottle", stock=400, stale=True)
    add("zero", "Бутылка LatestZero", "bottle", stock=0, previous_stock=500)
    add("incoming", "Бутылка Incoming", "bottle", stock=None, incoming=1000)
    add("shared", "Бутылка Shared", "bottle", stock=None, parent_shared=1000)
    add("multi-brand", "Ручка шариковая Plus", "pen", "Alpha Studio")
    add("unknown-brand", "Ручка шариковая Anonymous", "pen", None)
    add(
        "sku",
        "Ежедневник Trend A5",
        "notebook",
        "Beta",
        "NAVY",
        1200,
        12,
        "кожа",
        model="Trend",
        sku="3-516.08",
        size="A5",
        subtype="diary",
    )
    for n in range(130):
        add("page-" + str(n), "Ручка шариковая Page " + str(n), "pen", "Page Brand", "BLACK", 1000 + n, 500)
    return rows


def matching(rows, spec):
    """Reference membership from labelled facts, never production evidence."""
    branches = spec.get("branches", [{}])

    def branch(p, b):
        return (
            (not b.get("concept") or b["concept"] in p["concepts"])
            and all(p.get(field) in allowed for field, allowed in b.get("in", {}).items())
            and all(p.get(field) not in denied for field, denied in b.get("not_in", {}).items())
            and all(
                p.get(field) is not None
                and (
                    p[field] >= value
                    if op == "ge"
                    else p[field] > value
                    if op == "gt"
                    else p[field] <= value
                    if op == "le"
                    else p[field] < value
                )
                for field, op, value in b.get("bounds", [])
            )
        )

    return {p["id"] for p in rows if any(branch(p, b) for b in branches)}


def cases():
    output = []

    def add(query, spec, concepts=None, **extra):
        output.append(dict(id=f"Q{len(output) + 1:03}", query=query, oracle=spec, concepts=concepts, **extra))

    aliases = {
        "pen": ["ручка", "ручки", "ручек"],
        "pencil": ["карандаш", "карандаши", "корондаш", "карондаш", "karandash"],
        "shopperbag": ["шопер", "шоппер", "шоперов", "шопперов"],
        "powerbank": ["powerbank", "power bank", "повербанк", "пауэрбанк", "внешний аккумулятор"],
        "thermomug": ["термокружка", "термо кружка", "термо-кружка"],
        "flashdrive": ["USB-накопитель", "флешка", "USB flash drive"],
        "bottle": ["бутылки", "БУТЫЛКИ", "  бутылки  "],
        "backpack": ["рюкзак", "рюкзаки"],
        "umbrella": ["зонт", "зонты"],
    }
    for concept, words in aliases.items():
        for word in words:
            add(word, {"branches": [{"concept": concept}]}, [concept], family="equivalence")
    for query in [
        "ручки бутылки",
        "ручки, бутылки",
        "ручки и бутылки",
        "ручки\nбутылки",
        "ручки\u00a0бутылки",
    ]:
        add(
            query,
            {"branches": [{"concept": "pen"}, {"concept": "bottle"}]},
            ["pen", "bottle"],
            family="union",
        )
    add("Molti", {"branches": [{"in": {"brand": ["Molti"]}}]}, [], family="brand")
    add(
        "ручки Molti", {"branches": [{"concept": "pen", "in": {"brand": ["Molti"]}}]}, ["pen"], family="brand"
    )
    add(
        "рюкзаки бренда Molti до 10000 тенге",
        {
            "branches": [
                {"concept": "backpack", "in": {"brand": ["Molti"]}, "bounds": [["price", "le", 10000]]}
            ]
        },
        ["backpack"],
    )
    for query in ["Ручка шариковая Tippo", "Tippo", "РУЧКИ TIPPO"]:
        add(query, {"branches": [{"in": {"model": ["Tippo"]}}]}, None, family="model")
    add(
        "Рюкзак-мешок Melango синий",
        {"branches": [{"concept": "backpack", "in": {"model": ["Melango"], "color": ["BLUE"]}}]},
        ["backpack"],
    )
    add("GF-12449.40", {"branches": [{"in": {"sku": ["GF-12449.40"]}}]}, None, family="sku")
    add("ручки бренда Unknown729", {"branches": [{"in": {"brand": ["Unknown729"]}}]}, ["pen"])
    add("бренд Alpha Studio", {"branches": [{"in": {"brand": ["Alpha Studio"]}}]}, [], family="brand")
    add("карандаши модели Галактика", {"branches": [{"in": {"model": ["Галактика"]}}]}, ["pencil"])
    add("ручки модель Deep Space", {"branches": [{"in": {"model": ["Deep Space"]}}]}, ["pen"])
    add(
        "ручка в футляре",
        {"branches": [{"concept": "pen", "in": {"key": ["case-pen"]}}]},
        ["pen"],
        family="purpose-role",
    )
    add("футляр для ручки", {"branches": [{"concept": "pen_case"}]}, None, family="purpose-role")
    add(
        "бутылка с карабином",
        {"branches": [{"concept": "bottle", "in": {"key": ["bottle-500"]}}]},
        ["bottle"],
        family="purpose-role",
    )
    add("держатель для бутылки", {"branches": [{"concept": "bottle_holder"}]}, None, family="purpose-role")
    add(
        "Чёрные ручки от 300 шт, до 2000 тенге за штуку",
        {
            "branches": [
                {
                    "concept": "pen",
                    "in": {"color": ["BLACK"]},
                    "bounds": [["stock", "ge", 300], ["price", "le", 2000]],
                }
            ]
        },
        ["pen"],
    )
    add(
        "Красные ручки от 500 шт и синие бутылки от 100 шт",
        {
            "branches": [
                {"concept": "pen", "in": {"color": ["RED"]}, "bounds": [["stock", "ge", 500]]},
                {"concept": "bottle", "in": {"color": ["BLUE"]}, "bounds": [["stock", "ge", 100]]},
            ]
        },
        ["pen", "bottle"],
    )
    add(
        "Бутылки 500 мл от 80 штук",
        {"branches": [{"concept": "bottle", "in": {"capacity_ml": [500]}, "bounds": [["stock", "ge", 80]]}]},
        ["bottle"],
        family="numeric-role",
    )
    add(
        "Повербанки 10000 мАч, 100 штук, до 12000 ₸",
        {
            "branches": [
                {
                    "concept": "powerbank",
                    "in": {"capacity_mah": [10000]},
                    "bounds": [["stock", "ge", 100], ["price", "le", 12000]],
                }
            ]
        },
        ["powerbank"],
        family="numeric-role",
    )
    add(
        "Футболки размера XL, от 200 шт",
        {"branches": [{"concept": "tshirt", "in": {"size": ["XL"]}, "bounds": [["stock", "ge", 200]]}]},
        ["tshirt"],
        family="numeric-role",
    )
    add("ручки желательно металлические", {"branches": [{"concept": "pen"}]}, ["pen"], family="soft")
    add("ручки сначала чёрные", {"branches": [{"concept": "pen"}]}, ["pen"], family="soft")
    add(
        "ручки только металлические",
        {"branches": [{"concept": "pen", "in": {"material": ["металл"]}}]},
        ["pen"],
        family="hard",
    )
    add("ручки, но не исключай пластик", {"branches": [{"concept": "pen"}]}, ["pen"], family="negation")
    add(
        "ручки без пластика",
        {"branches": [{"concept": "pen", "not_in": {"material": ["пластик"]}}]},
        ["pen"],
        family="negation",
    )
    add(
        "ручки не белые",
        {"branches": [{"concept": "pen", "not_in": {"color": ["WHITE"]}}]},
        ["pen"],
        family="negation",
    )
    for op, phrase in [("ge", "от"), ("ge", "не меньше"), ("gt", "больше")]:
        for n in [80, 250, 300, 500]:
            add(
                f"бутылки {phrase} {n} штук",
                {"branches": [{"concept": "bottle", "bounds": [["stock", op, n]]}]},
                ["bottle"],
                family="quantity-boundary",
            )
    for phrase in ["до", "не дороже", "дешевле"]:
        for n in [500, 1500, 2500, 5000]:
            add(
                f"ручки {phrase} {n} тенге",
                {
                    "branches": [
                        {"concept": "pen", "bounds": [["price", "lt" if phrase == "дешевле" else "le", n]]}
                    ]
                },
                ["pen"],
                family="price-boundary",
            )
    for color, word in [("BLACK", "чёрные"), ("RED", "красные"), ("DARK_BLUE", "тёмно-синие")]:
        for concept, label in [("pen", "ручки"), ("bottle", "бутылки"), ("backpack", "рюкзаки")]:
            add(
                f"{word} {label}",
                {"branches": [{"concept": concept, "in": {"color": [color]}}]},
                [concept],
                family="color",
            )
    for brand in ["Alpha", "Beta", "Page Brand", "B1", "Molti"]:
        for concept, label in [("pen", "ручки"), ("backpack", "рюкзаки"), ("bottle", "бутылки")]:
            add(
                f"{label} бренда {brand}",
                {"branches": [{"concept": concept, "in": {"brand": [brand]}}]},
                [concept],
                family="brand",
            )
    for word in ["шопбер", "квантосборник", "бренд NoSuchName729", "ручки модель Missing729"]:
        add(word, {"branches": [{"in": {"id": []}}]}, None, family="preserve-unknown")
    for query in ["Собери 10 наборов из ручек и кружек", "Собери 30 наборов из зонтов и термосов"]:
        cs = ["pen", "mug"] if "10" in query else ["umbrella", "thermos"]
        add(query, {"branches": [{"concept": c} for c in cs]}, cs, family="legacy-grid")
    for query in [
        "ручки от -3 штук",
        "ручки цена от 5000 до 1000 тенге",
        "только красные ручки, исключи красные",
        "ручки от 999999999999999999999999999 штук",
    ]:
        add(query, {}, None, family="contradiction", reject=True)
    while len(output) < 150:
        n = len(output)
        quantity = [79, 80, 249, 250, 299, 300, 499, 500][n % 8]
        add(
            f"Бутылки от {quantity} шт. до {1000 + n * 10} тенге",
            {
                "branches": [
                    {
                        "concept": "bottle",
                        "bounds": [["stock", "ge", quantity], ["price", "le", 1000 + n * 10]],
                    }
                ]
            },
            ["bottle"],
            family="combined",
        )
    add(
        "Для записи и напитков, всё чёрное, от 100 шт",
        {
            "branches": [
                {"concept": c, "in": {"color": ["BLACK"]}, "bounds": [["stock", "ge", 100]]}
                for c in ["pen", "pencil", "notebook", "bottle", "thermomug", "mug"]
            ]
        },
        None,
        family="purpose",
    )
    add(
        "Подарки сотрудникам в дороге, без кружек и футболок, до 7000 тенге, от 80 шт",
        {
            "branches": [
                {"concept": c, "bounds": [["stock", "ge", 80], ["price", "le", 7000]]}
                for c in ["backpack", "bottle", "thermomug", "powerbank", "umbrella", "organizer"]
            ]
        },
        None,
        family="purpose",
    )
    add(
        "Подарки руководителям до 12000 тенге, без пластика и одежды, желательно металл или дерево",
        {
            "branches": [
                {"concept": c, "not_in": {"material": ["пластик"]}, "bounds": [["price", "le", 12000]]}
                for c in ["notebook", "thermos", "organizer", "wallet", "cardholder"]
            ]
        },
        None,
        family="purpose",
    )
    add(
        "Что-то полезное для ноутбука, компактное, кроме зарядных устройств",
        {"branches": [{"concept": "cable"}]},
        None,
        family="purpose",
    )
    add(
        "Бутылки, цена до 2500 ₸",
        {"branches": [{"concept": "bottle", "bounds": [["price", "le", 2500]]}]},
        ["bottle"],
        family="price-role",
    )
    add(
        "Ежедневники",
        {"branches": [{"concept": "notebook", "in": {"subtype": ["diary"]}}]},
        ["notebook"],
        family="subtype",
    )
    add(
        "Блокноты",
        {"branches": [{"concept": "notebook", "in": {"subtype": ["notepad"]}}]},
        ["notebook"],
        family="subtype",
    )
    add(
        "Вечные карандаши 2 в 1, желательно металлические, сначала чёрные",
        {"branches": [{"in": {"key": ["hybrid"]}}]},
        ["pencil"],
        family="hybrid",
    )
    add(
        "Ручки и бутылки Molti. Только металл, без пластика",
        {
            "branches": [
                {"concept": c, "in": {"brand": ["Molti"], "material": ["металл"]}} for c in ["pen", "bottle"]
            ]
        },
        ["pen", "bottle"],
        family="global-material",
    )
    add(
        "Ручки цена от 1 000 до 2 500 тенге",
        {"branches": [{"concept": "pen", "bounds": [["price", "ge", 1000], ["price", "le", 2500]]}]},
        ["pen"],
        family="price-spaces",
    )
    add(
        "Ручки дешевле 5000",
        {"branches": [{"concept": "pen", "bounds": [["price", "lt", 5000]]}]},
        ["pen"],
        family="unqualified-price",
    )
    add(
        "Бутылки больше 250",
        {"branches": [{"concept": "bottle", "bounds": [["stock", "gt", 250]]}]},
        ["bottle"],
        family="unqualified-quantity",
    )
    add(
        "Собери 10 сетов из powerbank, шопперов, термо кружек и карондашей. Цвет navy blue, тираж минимум 150шт",
        {
            "branches": [
                {"concept": c, "in": {"color": ["NAVY"]}, "bounds": [["stock", "ge", 150]]}
                for c in ["powerbank", "shopperbag", "thermomug", "pencil"]
            ]
        },
        ["powerbank", "shopperbag", "thermomug", "pencil"],
        family="legacy-grid",
    )
    add(
        "Нужно 8 наборов: красные футболки от 200 шт, чёрные повербанки от 100 шт и синие ручки от 500 шт. Никаких белых товаров",
        {
            "branches": [
                {"concept": "tshirt", "in": {"color": ["RED"]}, "bounds": [["stock", "ge", 200]]},
                {"concept": "powerbank", "in": {"color": ["BLACK"]}, "bounds": [["stock", "ge", 100]]},
                {"concept": "pen", "in": {"color": ["BLUE"]}, "bounds": [["stock", "ge", 500]]},
            ]
        },
        ["tshirt", "powerbank", "pen"],
        family="local-constraints",
    )
    add(
        "Собери ровно 30 разных наборов из зонтов, дождевиков, носков и термосов. В каждом по 3 разных товара каждой категории, от 300 штук. Не повторяй товары между наборами",
        {
            "branches": [
                {"concept": c, "bounds": [["stock", "ge", 300]]}
                for c in ["umbrella", "raincoat", "socks", "thermos"]
            ]
        },
        ["umbrella", "raincoat", "socks", "thermos"],
        family="legacy-grid",
    )
    return output


def holdout(seed=927651, count=50):
    rng = random.Random(seed)
    result = []
    for n in range(count):
        concept, word = rng.choice([("pen", "ручки"), ("bottle", "бутылки"), ("backpack", "рюкзаки")])
        amount = rng.randint(1, 650)
        price = rng.randint(400, 5500)
        query = rng.choice(
            [
                f"Найди {word}, тираж минимум {amount}шт, не дороже {price} тенге",
                f"{word.capitalize()} от {amount} штук, цена до {price} ₸",
            ]
        )
        result.append(
            dict(
                id=f"H{seed}-{n}",
                query=query,
                concepts=[concept],
                oracle={
                    "branches": [
                        {"concept": concept, "bounds": [["stock", "ge", amount], ["price", "le", price]]}
                    ]
                },
                family="holdout",
            )
        )
    return result
