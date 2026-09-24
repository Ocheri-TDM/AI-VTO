import re

from app.domain.models import SearchIntent
from app.domain.services import ColorNormalizer, normalize_text

CATEGORY_PATTERNS = {
    "thermomug": r"термокруж\w*|термостакан\w*|thermo\s?mug\w*|travel mug\w*",
    "backpack": r"рюкзак\w*|backpack\w*",
    "notebook": r"ежедневник\w*|блокнот\w*|notebook\w*|diar(?:y|ies)",
    "pen": r"\bруч(?:ка|ки|ек|ку|ками)\b|\bpens?\b",
}


class BasicIntentParser:
    """Deliberately limited deterministic stage-one parser; no pretend LLM calls."""

    async def parse_intent(self, query: str) -> SearchIntent:
        text = normalize_text(query)
        quantity_match = re.search(r"(?:тираж|количество|quantity)\s*[:=]?\s*(\d[\d\s]*\d|\d)", text)
        if not quantity_match:
            quantity_match = re.search(r"(\d[\d\s]*\d|\d)\s*(?:шт\b|штук\b|pcs\b)", text)
        quantity = int(re.sub(r"\s", "", quantity_match.group(1))) if quantity_match else None
        budget_match = re.search(r"(?:до|не дороже|бюджет)\s*(\d[\d\s]*\d|\d)\s*(?:₸|тенге|kzt)", text)
        budget = int(re.sub(r"\s", "", budget_match.group(1))) if budget_match else None
        categories = [key for key, pattern in CATEGORY_PATTERNS.items() if re.search(pattern, text)]
        return SearchIntent(
            raw_query=query.strip(),
            quantity=quantity,
            categories=categories,
            colors=ColorNormalizer().extract(text),
            budget=budget,
        )
