"""Presentation of saved query branches and deterministic category commands."""

import re

from app.domain.intent import CategoryResolver
from app.domain.services import ColorNormalizer
from app.domain.taxonomy import DISPLAY_NAMES, QueryVocabulary, UniversalCategoryResolver


def branch_label(branch):
    if branch.category == "*":
        return "Все категории"
    if branch.resolved is False:
        return "Категория не распознана"
    return DISPLAY_NAMES.get(branch.category, branch.label)


def branch_commands(message, intent):
    text = message.casefold().replace("ё", "е")
    if re.search(r"покажи\s+все\s+категории", text):
        return [{"action": "show_all"}]
    action = (
        "hide"
        if re.search(r"\bскро[йю]|\bскрыть", text)
        else "show"
        if re.search(r"покажи.*(?:снова|обратно)|верни.*(?:видимость|категори)", text)
        else "remove"
        if re.search(r"\bубери|\bисключи", text)
        else None
    )
    if not action:
        return None
    categories = UniversalCategoryResolver().resolve(message)
    if not categories:
        return None
    branches = [b for b in intent.branches if b.category in categories]
    colors = {c.value for c in ColorNormalizer().extract(message)}
    if colors:
        branches = [b for b in branches if colors.intersection(b.colors)]
    models = [m for b in branches for m in b.models if m.casefold() in text]
    if models:
        branches = [b for b in branches if set(models).intersection(b.models)]
    if not branches:
        raise ValueError("UNKNOWN_BRANCH")
    return [{"action": action, "branch_id": b.id} for b in branches]


def source_segments(query, categories, registry):
    """Attach provenance/order to already resolved concepts, never discover new ones."""
    protected = [(a, b) for a, b, _ in registry.spans(query)]

    def blocked(a, b):
        return any(a < end and b > start for start, end in protected)

    mentions = []
    nodes = {n.id: n for n in UniversalCategoryResolver().nodes}
    for category in set(categories):
        if category in CategoryResolver.patterns:
            for match in re.finditer(CategoryResolver.patterns[category], query, re.I):
                if not blocked(match.start(), match.end()):
                    mentions.append((match.start(), match.end(), category))
        node = nodes.get(category)
        aliases = list(QueryVocabulary.equivalents(category)) + (node.aliases if node else []) + [category]
        for alias in aliases:
            pattern = r"\b" + re.escape(alias).replace(r"\ ", r"\s+").replace("е", "[её]") + r"\w*"
            for match in re.finditer(pattern, query, re.I):
                if not blocked(match.start(), match.end()):
                    mentions.append((match.start(), match.end(), category))
    accepted = []
    for item in sorted(mentions, key=lambda m: (m[0], -(m[1] - m[0]))):
        if not any(item[0] < b and item[1] > a for a, b, _ in accepted):
            accepted.append(item)
    separators = [
        m for m in re.finditer(r"[,;]|\s+(?:и|или)\s+", query, re.I) if not blocked(m.start(), m.end())
    ]
    regions = zip([0] + [m.end() for m in separators], [m.start() for m in separators] + [len(query)])
    parts = []
    for start, end in regions:
        items = [m for m in accepted if start <= m[0] < end]
        cuts = [start] + [m[0] for m in items[1:]] + [end]
        for n, (a, b) in enumerate(zip(cuts, cuts[1:])):
            if items:
                parts.append((items[n][2], query[a:b].strip(" ,;"), a))
    return parts
