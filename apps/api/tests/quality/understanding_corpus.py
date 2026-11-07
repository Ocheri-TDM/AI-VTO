"""Gold authored independently of production resolvers and SQL.

Frozen before the 2026-11-07 repair. Holdout grammar is separate from the
labelled corpus. These are controlled scenarios, not a claim about all Russian.
"""

VERSION = "understanding-20261107-v1"
# Canonical concepts already present in taxonomy, plus the two independently
# justified headwear concepts under investigation. Do not invent quota IDs.
CONCEPTS = [
    ("pen", "ручки"),
    ("notebook", "блокноты"),
    ("thermomug", "термокружки"),
    ("cap", "кепки"),
    ("baseballcap", "бейсболки"),
    ("backpack", "рюкзаки"),
    ("bottle", "бутылки"),
    ("powerbank", "повербанки"),
    ("flashdrive", "флешки"),
    ("thermocup", "термостаканы"),
    ("thermos", "термосы"),
    ("pencil", "карандаши"),
    ("marker", "маркеры"),
    ("highlighter", "текстовыделители"),
    ("mug", "кружки"),
    ("glass", "стаканы"),
    ("plate", "тарелки"),
    ("tshirt", "футболки"),
    ("raincoat", "дождевики"),
    ("socks", "носки"),
    ("hoodie", "худи"),
    ("blanket", "пледы"),
    ("clock", "часы"),
    ("speaker", "колонки"),
    ("headphones", "наушники"),
    ("flashlight", "фонарики"),
    ("suitcase", "чемоданы"),
    ("wallet", "кошельки"),
    ("cardholder", "визитницы"),
    ("organizer", "органайзеры"),
    ("lanyard", "ланьярды"),
    ("bag", "сумки"),
    ("shopperbag", "шопперы"),
    ("charger", "зарядные устройства"),
    ("cable", "кабели"),
    ("lunchbox", "ланчбоксы"),
    ("umbrella", "зонты"),
    ("accessory", "брелоки"),
]


def labelled():
    forms = [
        "{noun}",
        "красные {noun} от 200 шт",
        "{noun}. цвет красный. тираж 200",
        "{noun}, цвет, красный, тираж, 200",
        "{noun}\nцвет красный\nтираж 200",
        "найди {noun}; желательно металлические",
    ]
    rows = []
    for concept, noun in CONCEPTS:
        for n, form in enumerate(forms):
            rows.append(
                {
                    "id": f"L-{concept}-{n}",
                    "class": ["canonical", "global", "sentences", "punctuation", "newlines", "soft"][n],
                    "query": form.format(noun=noun),
                    "categories": [concept],
                    "models": [],
                    "colors": ["RED"] if n in (1, 2, 3, 4) else [],
                    "quantity": 200 if n in (1, 2, 3, 4) else None,
                }
            )
    controls = [
        (
            "красные ручки блокноты термокружки и кепки. тираж 200",
            ["pen", "notebook", "thermomug", "cap"],
            ["RED"],
            200,
        ),
        (
            "красные ручки блокноты термокружки и бейсболка. тираж 200",
            ["pen", "notebook", "thermomug", "baseballcap"],
            ["RED"],
            200,
        ),
        (
            "красные. ручки. блокноты. термокружки. и. кепки. тираж. 200.",
            ["pen", "notebook", "thermomug", "cap"],
            ["RED"],
            200,
        ),
        (
            "красные, ручки, блокноты, термокружки, и, бейсболки, тираж, 200",
            ["pen", "notebook", "thermomug", "baseballcap"],
            ["RED"],
            200,
        ),
        ("темно. синий. цвет. ручки. кепки. тираж. 200", ["pen", "cap"], ["DARK_BLUE"], 200),
        ("ручки и бейсболки", ["pen", "baseballcap"], [], None),
        ("ручки и кепки", ["pen", "cap"], [], None),
        ("ручки и бутылки", ["pen", "bottle"], [], None),
        ("флешки USB-C на 64 ГБ, 200 штук", ["flashdrive"], [], 200),
        ("термокружки 0,5 л до 5 000,50 тенге", ["thermomug"], [], None),
        ("ручки без пластика", ["pen"], [], None),
        ("ручки желательно металлические", ["pen"], [], None),
        ("не красные ручки", ["pen"], [], None),
        ("нужны ручки, только не дверные", ["pen"], [], None),
        ("кепки без козырька", ["cap"], [], None),
        ("ручки со вкусом радуги", ["pen"], [], None),
        ("power bank и USB flash drive", ["powerbank", "flashdrive"], [], None),
        ("сумки-шопперы и карандаши", ["shopperbag", "pencil"], [], None),
        ("красные кепки и зонты от 100 шт", ["cap", "umbrella"], ["RED"], 100),
        ("бутылки, кружки, кепки", ["bottle", "mug", "cap"], [], None),
        ("металлические ручки от 250 шт", ["pen"], [], 250),
        ("бейсболка от 200 шт", ["baseballcap"], [], 200),
    ]
    rows += [
        {
            "id": f"L-regression-{i}",
            "class": "regression",
            "query": q,
            "categories": c,
            "models": [],
            "colors": colors,
            "quantity": quantity,
        }
        for i, (q, c, colors, quantity) in enumerate(controls)
    ]
    assert len(rows) == 250
    return rows


def holdout():
    # New sentence/attribute order, parentheticals, agreement, purpose and
    # exclusion grammar; not a different seed of the labelled templates.
    grammar = [
        "Покажи {noun}; цвет: красный; тираж минимум 200.",
        "Для каталога: {noun} — красные, от 200 штук.",
        "{noun}\nЦвет — красный\nТираж — 200",
        "Нужны {noun}, только красные, минимум 200 штук.",
        "Цвет красный. Из товаров нужны {noun}. Тираж 200.",
        "{noun} (красный цвет), 200 шт.",
        "Найди {noun}; не белые, красные. Тираж 200.",
        "От 200 шт. — {noun}; обязательный цвет: красный.",
        "{noun}; тираж от 200; цвет красный.",
        "Требуются {noun} для сотрудников. Все в красном цвете, тираж 200.",
    ]
    return [
        {
            "id": f"H-{i}-{n}",
            "class": "holdout-grammar-" + str(n),
            "query": form.format(noun=noun),
            "categories": [concept],
            "models": [],
            "colors": ["RED"],
            "quantity": 200,
        }
        for i, (concept, noun) in enumerate(CONCEPTS[:30])
        for n, form in enumerate(grammar)
    ]
