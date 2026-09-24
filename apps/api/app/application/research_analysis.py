"""Evidence-based local ranking, similarity, facets and reversible refinement."""
import hashlib
import re
from collections import Counter, defaultdict
from datetime import timedelta

from app.domain.models import Product, utcnow
from app.domain.research import ResearchSession, ResearchView
from app.domain.services import normalize_text


def facts(product: Product) -> str:
    value = normalize_text(' '.join(str(v or '') for v in (
        product.name, product.description, product.material, product.brand,
        *product.features, *product.metadata.get('attributes', {}).values())))
    if any(t in value for t in ('сталь', 'стальн', 'алюмин', 'латун', 'металл')):
        value += ' металл'
    if any(t in value for t in ('пластик', 'тритан', 'tritan')) or re.search(r'\bpet\b', value):
        value += ' пластик'
    return value


class SimilarityService:
    def score(self, product: Product, references: list[Product]) -> float:
        def compare(reference):
            left, right = set(re.findall(r'\w{3,}', facts(product))), set(re.findall(r'\w{3,}', facts(reference)))
            lexical = len(left & right) / max(1, len(left | right))
            color = bool({c.normalized_color for c in product.colors} & {c.normalized_color for c in reference.colors})
            price = min(product.price_kzt or 0, reference.price_kzt or 0) / max(1, product.price_kzt or 0, reference.price_kzt or 0)
            return .4 * (product.category == reference.category) + .2 * color + .2 * lexical + .2 * price
        return max((compare(r) for r in references), default=0)


class FacetService:
    def build(self, products):
        counters = defaultdict(Counter)
        for p in products:
            for field, value in [('category', p.category), ('material', p.material),
                                 ('brand', p.brand), ('capacity', p.capacity),
                                 *[(k, v) for k, v in p.metadata.get('attributes', {}).items()
                                   if isinstance(v, str) and len(v) <= 120]]:
                if value and not any(term in field.casefold() for term in ('sku', 'артикул', 'article', 'stock', 'supplier', 'остаток')):
                    counters[field][value] += 1
            for color in {c.normalized_color.value for c in p.colors}:
                counters['color'][color] += 1
            for tag in p.metadata.get('semantic_tags', {}).get('values', []):
                counters['style'][tag] += 1
        return {field: [{'value': value, 'count': count} for value, count in counter.most_common()]
                for field, counter in counters.items() if counter}


class ProductGroupingService:
    def groups(self, products):
        groups = defaultdict(list)
        for p in products:
            # Strong model clues only. Retain every offer, even when a duplicate is suspected.
            generic = {'bottle', 'water', 'blue', 'black', 'white', 'steel', 'metal', 'plastic', 'glass', 'sport', 'vacuum', 'thermo', 'navy', 'dark', 'light'}
            model = [word for word in re.findall(r'\b[a-z][a-z0-9]{3,}\b', p.name.casefold()) if word not in generic]
            # Model token alone is insufficient: require independent physical/brand evidence.
            evidence = [p.brand, p.material, p.dimensions, p.capacity]
            key = '|'.join([p.category or '', *model, *[str(v or '').casefold() for v in evidence]]) if model and p.brand and sum(bool(v) for v in evidence) >= 3 else p.id
            identifier = hashlib.sha256(key.encode()).hexdigest()[:20]
            groups[identifier].append(p.id)
        return [{'id': key, 'offer_ids': offers} for key, offers in groups.items()]


PREFERENCE_TERMS = {
    'premium': ('сталь', 'кожа', 'преми', 'вакуум', 'металл', 'дерево'),
    'minimal': ('минимал', 'лаконич', 'матов', 'однотон'),
    'business': ('делов', 'кожа', 'металл', 'классич'),
    'technology': ('usb', 'беспровод', 'заряд', 'технолог', 'магнит'),
    'eco': ('бамбук', 'переработ', 'стекло', 'дерево', 'эко'),
    'not_sport': ('спортив', 'карабин', 'трениров'),
    'creative': ('необыч', 'оригиналь', 'трансформ', 'конструктор', 'дизайнер'),
    'металл': ('металл', 'сталь', 'алюмин', 'латун'),
    'дерев': ('дерев', 'бамбук'),
    'темн': ('темн', 'navy', 'черн'),
}


def current_products(research: ResearchSession):
    filters = research.view.filters
    selected = set(research.view.selected_ids)
    refs = [p for p in research.products if p.id in research.view.similar_to]
    scored = []
    for p in research.products:
        if p.metadata.get('research_invalid'):
            continue
        text = facts(p)
        if p.id in research.view.hidden_ids:
            continue
        if filters.categories and p.category not in filters.categories:
            continue
        if filters.colors and not any(c.normalized_color in filters.colors for c in p.colors):
            continue
        if filters.min_price is not None and (p.price_kzt is None or p.price_kzt < filters.min_price):
            continue
        if filters.max_price is not None and (p.price_kzt is None or p.price_kzt > filters.max_price):
            continue
        if filters.selected_only and p.id not in selected:
            continue
        if any(normalize_text(t) in text for t in filters.excluded_terms):
            continue
        if any(normalize_text(t) not in text for t in filters.required_terms):
            continue
        if filters.attributes.get('style') and not set(filters.attributes['style']) & set(p.metadata.get('semantic_tags', {}).get('values', [])):
            continue
        if any(str(getattr(p, key, None) or p.metadata.get('attributes', {}).get(key, '')) not in values
               for key, values in filters.attributes.items() if values and key != 'style'):
            continue
        score = .5
        if research.intent.primary_colors and any(c.normalized_color in research.intent.primary_colors for c in p.colors):
            score += .1
        for preference in research.view.preferences:
            match = any(t in text for t in PREFERENCE_TERMS.get(preference, (preference,)))
            score += (-.2 if preference == 'not_sport' else .1) * match
            if preference == 'недорог' and p.price_kzt:
                score += .1 / (1 + p.price_kzt / 5000)
        if refs:
            score = SimilarityService().score(p, refs)
        scored.append((p, max(0, min(1, score))))
    if research.view.sorting == 'price_asc':
        scored.sort(key=lambda row: (row[0].price_kzt is None, row[0].price_kzt or 0))
    elif research.view.sorting == 'price_desc':
        scored.sort(key=lambda row: -(row[0].price_kzt or 0))
    else:
        scored.sort(key=lambda row: (-row[1], row[0].id))
    return [p for p, _ in scored]


def remember(research: ResearchSession, label: str):
    research.history.append({'label': label, 'view': research.view.model_dump(mode='json')})
    research.history = research.history[-30:]
    research.view.version += 1


def undo(research: ResearchSession):
    if research.history:
        version = research.view.version + 1
        research.view = ResearchView.model_validate(research.history.pop()['view'])
        research.view.version = version


def stale(product: Product, ttl=3600) -> bool:
    return utcnow() >= product.fetched_at + timedelta(seconds=ttl)
