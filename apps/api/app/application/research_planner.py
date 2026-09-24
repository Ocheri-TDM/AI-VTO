"""Bounded taxonomy expansion; budgets constrain execution, never product counts."""
import re

from app.ai.basic import BasicIntentParser
from app.domain.models import ColorGroup
from app.domain.research import ResearchBudget, ResearchCoverage, ResearchIntent
from app.domain.taxonomy import SEEDS, UniversalCategoryResolver

SUPPLIERS = ('oasis', 'ucontay', 'gifts', 'portobello', 'happygifts', 'artegifts')
QUERIES = {
    'bottle': ['бутылка', 'бутылка для воды', 'спортивная бутылка', 'термобутылка'],
    'thermomug': ['термокружка', 'термостакан'],
    'backpack': ['рюкзак'], 'notebook': ['ежедневник', 'блокнот'],
    'pen': ['ручка'], 'powerbank': ['внешний аккумулятор', 'powerbank'],
    'charger': ['зарядное устройство'], 'cable': ['кабель'],
    'lanyard': ['ланъярд', 'шнурок для бейджа'],
    'accessory': ['технологичные аксессуары', 'держатель телефона'],
    'umbrella': ['зонт'], 'bag': ['сумка'], 'lunchbox': ['ланчбокс'],
}
BLUE_FAMILY = [ColorGroup.BLUE, ColorGroup.NAVY, ColorGroup.DARK_BLUE,
               ColorGroup.LIGHT_BLUE, ColorGroup.ROYAL_BLUE, ColorGroup.BLUE_GREY]


class QueryExpansionService:
    def expand(self, category: str, budget: ResearchBudget) -> list[str]:
        # Semantic suggestions must resolve into this category's approved taxonomy.
        return QUERIES.get(category, list(SEEDS[category][1:]) if category in SEEDS else [])[:budget.max_query_expansions]


class CategoryDiscoveryService:
    def discover(self, query: str) -> dict[str, list[str]]:
        if re.search(r'\bit\b|\bит\b|технолог|конференц', query.casefold()):
            return {'primary': ['powerbank', 'charger', 'cable'],
                    'secondary': ['bottle', 'thermomug', 'backpack', 'notebook', 'pen'],
                    'exploratory': ['lanyard', 'accessory']}
        return {'primary': ['bottle', 'notebook', 'pen'],
                'secondary': ['backpack', 'thermomug'], 'exploratory': ['accessory']}


class ResearchPlanner:
    async def parse(self, query: str) -> ResearchIntent:
        base = await BasicIntentParser().parse_intent(query)
        people = re.search(r'(\d+)\s*(?:человек|людей)', query.casefold())
        if people:
            base.quantity = int(people.group(1))
        resolver = UniversalCategoryResolver()
        explicit = resolver.resolve(query)
        # Broad event discovery should not be limited by Stage 2's default categories.
        broad = bool(re.search(r'мерч|подарк|конференц|сувенир|что-нибудь|что-то|руководител|сотрудник', query.casefold()))
        discovery = CategoryDiscoveryService().discover(query) if not explicit and broad else {}
        categories = explicit or [c for group in discovery.values() for c in group] or [resolver.dynamic_id(query)]
        strict = bool(re.search(r'строго|только\s+(?:темно|тёмно|син|голуб)', query.casefold()))
        primary = list(base.colors)
        soft_terms = []
        soft = bool(re.search(r'желательно|предпочтительно|лучше', query.casefold()))
        for pattern,material in [(r'дерев','дерев'),(r'алюмин','алюмин'),(r'сталь|стальн','сталь'),
                                 (r'бамбук','бамбук'),(r'пластик','пластик'),(r'металл','металл')]:
            if re.search(pattern,query.casefold()):
                if soft:
                    soft_terms.append(material)
                    base.material = None
                else:
                    base.material = material
                break
        colors = list(base.colors)
        if not strict and any(c in BLUE_FAMILY for c in colors):
            colors = list(dict.fromkeys(colors + BLUE_FAMILY))
        return ResearchIntent(**(base.model_dump() | {
            'categories': categories, 'colors': colors, 'primary_colors': primary,
            'color_mode': 'strict' if strict else 'family', 'discovery': discovery,
            'soft_terms': soft_terms,
            'availability_mode': ('AVAILABLE_NOW_ONLY' if re.search(
                r'\u0442\u043e\u043b\u044c\u043a\u043e\s+(?:\u0432\s+)?\u043d\u0430\u043b\u0438\u0447\u0438\u0438|'
                r'\u0442\u043e\u043b\u044c\u043a\u043e\s+\u0434\u043e\u0441\u0442\u0443\u043f\u043d', query.casefold()
            ) else 'WAIT_ALLOWED' if re.search(
                r'\u043c\u043e\u0436\u043d\u043e\s+\u043f\u043e\u0434\u043e\u0436\u0434\u0430\u0442\u044c|'
                r'\u0433\u043e\u0442\u043e\u0432(?:\u044b|\u0430)?\s+\u0436\u0434\u0430\u0442\u044c', query.casefold()
            ) else 'ALLOW_INCOMING')
        }))

    def plan(self, intent: ResearchIntent, budget: ResearchBudget) -> list[ResearchCoverage]:
        expansion = QueryExpansionService()
        # Keep ALL planned branches. The worker applies budgets and leaves the rest LIMITED.
        return [ResearchCoverage(supplier=s, category=c, query=q,
                query_origin='dynamic' if c.startswith('dynamic:') else 'taxonomy',
                query_reason='User product concept' if c.startswith('dynamic:') else 'Category alias')
                for c in intent.categories for s in SUPPLIERS
                for q in (expansion.expand(c, budget) or [UniversalCategoryResolver().concept(intent.raw_query)])]
