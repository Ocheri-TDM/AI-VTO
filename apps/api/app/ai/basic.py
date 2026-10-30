import re

from app.domain.models import SearchIntent
from app.domain.services import ColorNormalizer, normalize_text

CATEGORY_PATTERNS = {
    "thermomug": r"термокруж\w*|thermo\s?mug\w*|travel mug\w*|(?:thermal|insulated)\s+mug\w*",
    "thermocup": r"термостакан\w*|(?:thermal|insulated)\s+tumbler\w*",
    "flashdrive": r"флеш(?:к|[- ]?накопител|\s+памят)\w*|usb[- ]?накопител\w*|(?:usb\s+)?flash\s+drive|usb\s+memory\s+stick",
    "powerbank": r"power\s*bank\w*|п(?:о|а|ау)вербанк\w*|внешн\w* аккумулятор\w*|портативн\w* аккумулятор\w*",
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
        if not quantity_match:
            quantity_match = re.search(
                r"(?:от|минимум|не\s+менее|не\s+меньше|>=)\s*(\d[\d\s]*\d|\d)", text
            )
        quantity = int(re.sub(r"\s", "", quantity_match.group(1))) if quantity_match else None
        budget_match = re.search(r"(?:до|не дороже|бюджет)\s*(\d[\d\s]*\d|\d)\s*(?:₸|тенге|kzt)", text)
        budget = int(re.sub(r"\s", "", budget_match.group(1))) if budget_match else None
        categories = [key for key, pattern in CATEGORY_PATTERNS.items() if re.search(pattern, text)]
        colors = ColorNormalizer().extract(text)
        excluded_colors = []
        for excluded in re.findall(r"(?:не|кроме)\s+([а-яё-]+)", text):
            excluded_colors.extend(ColorNormalizer().extract(excluded))
        colors = [color for color in colors if color not in excluded_colors]
        return SearchIntent(
            raw_query=query.strip(),
            quantity=quantity,
            categories=categories,
            colors=colors,
            excluded_colors=list(dict.fromkeys(excluded_colors)),
            budget=budget,
        )
