import json
import re
from pathlib import Path

from app.ai.local import ModelUnavailable
from app.application.research_analysis import current_products, facts, remember
from app.application.research_planner import QUERIES
from app.domain.intent import ColorIntentResolver
from app.domain.research import RefinementAction, ResearchCoverage, ResearchDecision
from app.domain.taxonomy import UniversalCategoryResolver


class ResearchRefinementEngine:
    def __init__(self, service):
        self.service = service

    async def target(self, research, term, categories=None):
        if hasattr(self.service,'enqueue_target'):
            await self.service.enqueue_target(research,term,categories)
            return
        added = []
        async with self.service.lock(research.id):
            suppliers = sorted({p.supplier for p in research.products}) or list(self.service.providers)
            for category in categories or research.intent.categories:
                if category not in QUERIES:
                    continue
                query = QUERIES[category][0] + ' ' + term[:120]
                for supplier in suppliers:
                    existing = next((b for b in research.coverage if b.supplier == supplier and b.query == query), None)
                    if existing and existing.status != 'COMPLETE':
                        added.append(existing.id)
                    elif existing is None:
                        branch = ResearchCoverage(supplier=supplier, category=category, query=query)
                        research.coverage.append(branch)
                        added.append(branch.id)
            await self.service.repository.save(research)
        if added:
            await self.service.resume(research.id, added)

    def command(self, message, research):
        text = message.casefold().replace('ё', 'е')
        if any(t in text for t in ('первоначаль', 'сбросить', 'исходной выбор', 'покажи все')):
            return ResearchDecision(action='RESET_REFINEMENT')
        if any(t in text for t in ('назад', 'отмени', 'верни как', 'предыдущ')):
            return ResearchDecision(action='UNDO')
        if 'обнов' in text:
            return ResearchDecision(action='REFRESH_RESEARCH')
        if any(t in text for t in ('продолж', 'поищи еще', 'ищи еще')):
            return ResearchDecision(action='EXPAND_RESEARCH')
        if 'похож' in text or 'ближе к' in text:
            visible = current_products(research)
            ordinals = [int(x) for x in re.findall(r'\b\d+\b', text)]
            if 'шест' in text:
                ordinals.append(6)
            if 'трет' in text:
                ordinals.append(3)
            for stem, ordinal in [('перв',1),('втор',2),('четверт',4),('пят',5)]:
                if stem in text:
                    ordinals.append(ordinal)
            ids = [visible[n-1].id for n in ordinals if 0 < n <= len(visible)]
            ids = ids or research.view.selected_ids
            return ResearchDecision(action='SIMILARITY_SEARCH', product_ids=ids) if ids else ResearchDecision(action='SHOW')
        if 'выбери' in text or 'выбрать' in text:
            count = re.search(r'\d+', text)
            return ResearchDecision(action='SELECT', count=int(count[0]) if count else 5, per_category=bool(re.search(r'по\s*\d+|каждой категор', text)))
        if 'сначала' in text and any(t in text for t in ('дешев', 'дорог')):
            return ResearchDecision(action='LOCAL_RERANK', sorting='price_asc' if 'дешев' in text else 'price_desc')
        for word, preference in [('преми', 'premium'), ('минимал', 'minimal'), ('руководител', 'business'),
                                 ('технолог', 'technology'), ('слишком спортив', 'not_sport'),
                                 ('строг', 'business'), ('эко', 'eco'), ('не баналь', 'creative'),
                                 ('небаналь', 'creative'), ('современн', 'technology')]:
            if word in text:
                return ResearchDecision(action='LOCAL_RERANK', preference=preference)
        filters = research.view.filters.model_copy(deep=True)
        changed = False
        if any(w in text for w in ('желательно', 'предпочтительно', 'лучше')):
            for stem in ('металл', 'дерев', 'матов', 'бамбук', 'недорог', 'темн'):
                if stem in text:
                    return ResearchDecision(action='LOCAL_RERANK', preference=stem)
        categories = UniversalCategoryResolver().resolve(message)
        color = ColorIntentResolver().from_text(message)
        if color.primary and any(w in text for w in ('только', 'оставь', 'строго')):
            filters.colors = color.primary if 'строго' in text else color.acceptable
            changed = True
        if categories and any(w in text for w in ('без ', 'убери', 'исключ')):
            remaining = set(filters.categories or [p.category for p in research.products]) - set(categories)
            filters.categories = sorted(x for x in remaining if x) or ['__none__']
            changed = True
        elif categories and any(w in text for w in ('только', 'оставь')):
            filters.categories = categories
            changed = True
        if 'дерев' in text:
            filters.required_terms = list(dict.fromkeys(filters.required_terms+['дерев']))
            changed = True
        for attribute in ('ластик', 'клип', 'детск'):
            if attribute in text and any(w in text for w in ('без', 'не ', 'убери')):
                filters.excluded_terms = list(dict.fromkeys(filters.excluded_terms+[attribute]))
                changed = True
        if 'пластик' in text and any(w in text for w in ('убери', 'без', 'исключ')):
            filters.excluded_terms = list(dict.fromkeys(filters.excluded_terms + ['пластик', 'pet', 'тритан']))
            changed = True
        for stem in ('металл', 'матов', 'бамбук'):
            if stem in text:
                known = sum(bool(p.material or p.description) for p in research.products)
                if known < len(research.products) * .6 or not research.products or (
                    'добав' in text and not any(stem in facts(p) for p in research.products)
                ):
                    return ResearchDecision(action='TARGETED_RESEARCH', query=stem)
                filters.required_terms = list(dict.fromkeys(filters.required_terms + [stem]))
                changed = True
        if 'ручк' in text and 'товар' in text and 'убери' in text:
            filters.excluded_terms.append('ручк')
            changed = True
        price = re.search(r'(?:до|дороже)\s*(\d[\d ]*)\s*(тысяч|тыс)?', text)
        if price:
            filters.max_price = int(price[1].replace(' ', '')) * (1000 if price[2] else 1)
            changed = True
        minimum = re.search(r'от\s*(\d[\d ]*)\s*(тысяч|тыс)?', text)
        if minimum:
            filters.min_price = int(minimum[1].replace(' ', '')) * (1000 if minimum[2] else 1)
            changed = True
        price_range = re.search(r'(\d+)\s*[–—-]\s*(\d+)\s*(?:тысяч|тыс)', text)
        if price_range:
            filters.min_price, filters.max_price = int(price_range[1])*1000, int(price_range[2])*1000
            changed = True
        if changed:
            return ResearchDecision(action='LOCAL_SEMANTIC_REFINE', filters=filters)
        return None

    async def decide(self, message, research):
        guarded = self.command(message, research)
        if guarded:
            return guarded
        if self.service.model:
            prompt = (Path(__file__).parents[1] / 'ai/prompts/research.md').read_text(encoding='utf-8')
            context = {'intent': research.intent.model_dump(mode='json'),
                       'view': research.view.model_dump(mode='json'),
                       'count': len(research.products),
                       'recent': research.messages[-6:],
                       'products': [{'id': p.id, 'name': p.name, 'material': p.material,
                                     'price_kzt': p.price_kzt} for p in current_products(research)[:24]]}
            try:
                decision = await self.service.model.complete([
                    {'role': 'system', 'content': prompt},
                    {'role': 'user', 'content': json.dumps(context, ensure_ascii=False)},
                    {'role': 'user', 'content': message}], ResearchDecision)
                if not set(decision.product_ids).issubset({p.id for p in research.products}):
                    return ResearchDecision(action='SHOW')
                # Model cannot trigger a full refresh unless the user explicitly requested it.
                if decision.action in (RefinementAction.REFRESH_RESEARCH, RefinementAction.EXPAND_RESEARCH):
                    return ResearchDecision(action='SHOW')
                return decision
            except ModelUnavailable:
                pass
        return ResearchDecision(action='SHOW')

    async def apply(self, identifier, message):
        research = await self.service.get(identifier)
        if research is None:
            raise KeyError(identifier)
        decision = await self.decide(message, research)
        action = decision.action
        if action in (RefinementAction.UNDO, RefinementAction.RESET_REFINEMENT):
            research = await self.service.change(identifier, restore='undo' if action == RefinementAction.UNDO else 'reset')
        elif action in (RefinementAction.LOCAL_FILTER, RefinementAction.LOCAL_SEMANTIC_REFINE) and decision.filters:
            research = await self.service.change(identifier, filters=decision.filters)
        elif action == RefinementAction.SELECT:
            ids = decision.product_ids or [p.id for p in current_products(research)[:decision.count or 5]]
            if decision.per_category and not decision.product_ids:
                from collections import Counter
                counts = Counter()
                ids = []
                for product in current_products(research):
                    if counts[product.category] < (decision.count or 5):
                        ids.append(product.id)
                        counts[product.category] += 1
            research = await self.service.change(identifier, selected_ids=ids)
        elif action in (RefinementAction.LOCAL_RERANK, RefinementAction.SIMILARITY_SEARCH):
            async with self.service.lock(identifier):
                research = await self.service.get(identifier)
                if not set(decision.product_ids).issubset({p.id for p in research.products}):
                    raise ValueError('UNKNOWN_PRODUCT')
                remember(research, message)
                if decision.sorting:
                    research.view.sorting = decision.sorting
                if decision.preference:
                    research.view.preferences = list(dict.fromkeys(research.view.preferences + [decision.preference]))
                if decision.product_ids:
                    research.view.similar_to = decision.product_ids
                await self.service.repository.save(research)
            if action == RefinementAction.SIMILARITY_SEARCH and decision.product_ids:
                references = [p for p in research.products if p.id in decision.product_ids]
                if len([p for p in research.products if p.category in {r.category for r in references}
                        and p.id not in decision.product_ids]) < 3:
                    # Use only observed attributes; no model-invented similarity query.
                    reference = references[0]
                    term = reference.material or reference.brand
                    if term:
                        await self.target(research, term, [reference.category])
        elif action == RefinementAction.TARGETED_RESEARCH and decision.query:
            await self.target(research, decision.query)
        elif action == RefinementAction.EXPAND_RESEARCH:
            await self.service.resume(identifier)
        elif action == RefinementAction.REFRESH_RESEARCH:
            if hasattr(self.service,'refresh_index'):
                research = await self.service.refresh_index(identifier)
                research.messages.extend([{'role':'user','content':message},
                    {'role':'assistant','content':'Обновление данных запущено. Текущая подборка сохранена.'}])
                await self.service.repository.save(research)
                return research, decision, 'Обновление данных запущено. Текущая подборка сохранена.'
            await self.service.cancel(identifier)
            research = await self.service.get(identifier)
            # Retain the pool/history while refreshing observations in place.
            research.coverage = self.service.planner.plan(research.intent, research.budget)
            await self.service.repository.save(research)
            await self.service.resume(identifier)
        async with self.service.lock(identifier):
            research = await self.service.get(identifier)
            response = f'В подборке {len(current_products(research))} вариантов. Выбрано {len(research.view.selected_ids)}.'
            if action == RefinementAction.SHOW:
                response = 'Уточните изменение или выберите товар, на который нужно ориентироваться. ' + response
            research.messages.extend([{'role': 'user', 'content': message}, {'role': 'assistant', 'content': response}])
            await self.service.repository.save(research)
        return research, decision, response
