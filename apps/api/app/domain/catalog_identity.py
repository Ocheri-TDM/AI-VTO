"""Structured catalog brands and literal family/model constraints, independent of categories."""

import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache

from app.domain.hard_request import _materials
from app.domain.product_purpose import ROLES
from app.domain.query_constraints import parse_constraints
from app.domain.services import ColorNormalizer
from app.domain.taxonomy import QueryVocabulary, UniversalCategoryResolver

IDENTITY_VERSION = "brand-model-v1"
UNKNOWN_BRANDS = {"", "без бренда", "безбренда", "no brand", "unbranded", "unknown"}


def brand_key(value):
    return " ".join(unicodedata.normalize("NFKC", value or "").split()).casefold()


def brand_identity(value):
    key = brand_key(value)
    return "" if key in UNKNOWN_BRANDS else key


def tokens(value):
    return tuple(re.findall(r"[^\W_]+", brand_key(value), re.UNICODE))


def phrase_in(phrase, value):
    needle, haystack = tokens(phrase), tokens(value)
    return bool(needle) and any(
        haystack[n : n + len(needle)] == needle for n in range(len(haystack) - len(needle) + 1)
    )


def primary_title(value):
    # Relation words inside a quoted model remain part of that model.
    quoted = [m.span() for m in re.finditer(r'[«"“][^»"”]*[»"”]', value or "")]
    for match in re.finditer(r"\s+(?:с|со|для|под|with|for|compatible|including)\s+", value or "", re.I):
        if not any(a <= match.start() + 1 < b for a, b in quoted):
            return value[: match.start()]
    return value or ""


@lru_cache(maxsize=8192)
def model_evidence(models, parent, title, structured=""):
    sources = (
        [("STRUCTURED_MODEL", structured)]
        if structured
        else [("CATALOG_PARENT_TITLE", primary_title(parent or title))]
    )
    for source, value in sources:
        if all(phrase_in(m, value) for m in models):
            return {"accepted": True, "source": source, "values": list(models), "family_title": value}
    return {"accepted": False, "reason": "MODEL_TITLE_MISMATCH", "values": list(models)}


class BrandRegistry:
    """Rebuilt from a small DISTINCT scalar projection; no stale enum/TTL cache."""

    def __init__(self, labels):
        self.raw = {}
        for label in labels:
            key = brand_key(label)
            self.raw.setdefault("" if key in UNKNOWN_BRANDS else key, []).append(label)
        self.labels = {
            k: sorted(set(v), key=lambda s: (len(s or ""), s or ""))[0] for k, v in self.raw.items() if k
        }

    def display(self, value):
        return self.labels.get(brand_key(value), value)

    def spans(self, text):
        result = []
        for key in sorted(self.labels, key=len, reverse=True):
            spellings = {key, *(unicodedata.normalize("NFKC", v) for v in self.raw[key])}
            for spelling in sorted(spellings, key=len, reverse=True):
                for match in re.finditer(
                    r"(?<!\w)" + re.escape(spelling).replace(r"\ ", r"\s+") + r"(?!\w)", text, re.I
                ):
                    if not any(match.start() < b and match.end() > a for a, b, _ in result):
                        result.append((match.start(), match.end(), self.labels[key]))
        return sorted(result)


@dataclass
class IdentityQuery:
    cleaned: str
    attribute_query: str = ""
    brands: list[str] = field(default_factory=list)
    excluded_brands: list[str] = field(default_factory=list)
    models: list[str] = field(default_factory=list)
    local: dict = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)
    explicit: bool = False
    unresolved: bool = False


_BRAND_CUE = r"\b(?:бренд\w*|марк[аиу]?|brand)\s*[:—-]?\s*"
_MODEL_CUE = r"\b(?:модел[ьиь]?|model)\b\s*[:—-]?\s*(?!\s*(?:с|со|из|без|для|в|на)\b)"
_LIMIT = r"\s+(?:до|от|тираж|цвет|quantity|бюджет|желательно|предпочтительно|покажи|без|кроме|для)\b|[.;]"
_GENERIC = re.compile(
    r"^(?:нуж\w*|най\w*|подбер\w*|покаж\w*|только|бренд\w*|марк\w*|модел\w*|brand|model|и|или|но|однако|всё|все|из|в|на|с|со|шт\w*|тенге|kzt|руб\w*|шариков\w*|перьев\w*|пишущ\w*|вечн\w*|металл\w*|пластик\w*|дерев\w*|обычн\w*|корпоративн\w*|сувенирн\w*|подарочн\w*|ballpoint|fountain|writing|the|only|find|show)$",
    re.I,
)


def resolve_identity(query, registry, family_rows=()):
    text = " ".join(unicodedata.normalize("NFKC", query).split())
    lower = text.casefold()
    identity_text = list(text)
    for preference in re.finditer(
        r"\b(?:желательно|предпочтительно|покажи\s+сначала|сначала)\b[^,.!?;]*", text, re.I
    ):
        identity_text[preference.start() : preference.end()] = " " * (preference.end() - preference.start())
    identity_text = "".join(identity_text)
    explicit_brand = bool(re.search(_BRAND_CUE, identity_text, re.I))
    explicit_model = bool(re.search(_MODEL_CUE, identity_text, re.I))
    out = IdentityQuery(cleaned=text, explicit=explicit_brand or explicit_model)
    requested_categories = set(UniversalCategoryResolver().resolve(text))
    spans = [] if explicit_model and not explicit_brand else registry.spans(identity_text)
    masked = identity_text
    entities = {}
    for n, (a, b, label) in reversed(list(enumerate(spans))):
        key = f"identitybrand{n}"
        excluded = bool(re.search(r"(?:без|кроме|исключ\w*|не\s+показывай)\s*$", text[:a], re.I))
        entities[key] = (label, excluded)
        masked = masked[:a] + key + masked[b:]
        if not explicit_brand and (
            any(
                brand_key(label) == brand_key(alias)
                for category in UniversalCategoryResolver().resolve(label)
                for alias in QueryVocabulary.equivalents(category)
            )
            or any(
                brand_key(brand) != brand_key(label)
                and phrase_in(label, primary_title(parent or name))
                and (
                    not requested_categories
                    or requested_categories.intersection(UniversalCategoryResolver().resolve(parent or name))
                )
                for parent, name, brand in family_rows
            )
        ):
            out.issues.append(
                f"«{label}» может означать бренд или модель/категорию. Уточните: «бренд {label}» или «модель {label}»."
            )
    # Explicit unknown values remain hard conditions, never silently disappear.
    for match in reversed(list(re.finditer(_BRAND_CUE, masked, re.I))):
        tail = re.split(_LIMIT + r"|\s+(?:и|или)\s+|,", masked[match.end() :], maxsplit=1, flags=re.I)[
            0
        ].strip(' «»"')
        if tail and not re.search(r"identitybrand\d+", tail):
            key = f"identitybrand{len(entities)}"
            entities[key] = (tail, False)
            start = masked.find(tail, match.end())
            masked = masked[:start] + key + masked[start + len(tail) :]
    resolver = UniversalCategoryResolver()
    clauses = re.split(r"\s+(?:и|или)\s+|[,;]", masked, flags=re.I)
    parsed = []
    for clause in clauses:
        labels = [entities[k] for k in re.findall(r"identitybrand\d+", clause)]
        for key, (_, excluded) in entities.items():
            if excluded:
                clause = re.sub(r"(?:без|кроме|исключ\w*|не\s+показывай)\s*" + key, " ", clause, flags=re.I)
        clean = re.sub(r"identitybrand\d+", " ", clause)
        clean = re.sub(_BRAND_CUE, " ", clean, flags=re.I)
        explicit = re.search(_MODEL_CUE, clean, re.I)
        if explicit:
            model = re.split(_LIMIT, clean[explicit.end() :], maxsplit=1, flags=re.I)[0].strip(' «»"')
            clean = clean[: explicit.start()]
            models = [model] if model else []
        else:
            _, model_text = parse_constraints(clean)
            model_text = re.sub(
                r"\b(?:собери|собрать|составь)\s+(?:ровно\s+)?\d*\s*(?:разных\s+)?(?:набор\w*|сет\w*|комплект\w*)\s*(?:из)?",
                " ",
                model_text,
                flags=re.I,
            )
            for _, _, role_pattern, _ in ROLES:
                model_text = re.sub(role_pattern, " ", model_text, flags=re.I)
            # Protect complete taxonomy phrases before looking for a model.
            for category in resolver.resolve(model_text):
                for alias in sorted(QueryVocabulary.equivalents(category), key=len, reverse=True):
                    if " " in alias or "-" in alias:
                        model_text = re.sub(
                            r"\b" + re.escape(alias).replace(r"\ ", r"\s+") + r"\w*",
                            " ",
                            model_text,
                            flags=re.I,
                        )
            model_text = re.sub(r"\b(?:термо)\s*[- ]\s*(круж\w*|стакан\w*)", " ", model_text, flags=re.I)
            model_text = re.sub(
                r"^(?:футляр|держател\w*|коробк\w*|упаковк\w*)\s+для\b", "для", model_text, flags=re.I
            )
            model_text = re.sub(r"\b(?:для|for|за\s+штуку)\b", " ", model_text, flags=re.I)
            head = re.split(
                _LIMIT + r"|\s+(?:сначала|только|не\s+бел\w*)\b", model_text, maxsplit=1, flags=re.I
            )[0]
            meaningful = []
            has_category = bool(resolver.resolve(head))
            for token in re.findall(r"[^\W_]+(?:[-+][^\W_]+)*", head):
                if (
                    token.isdecimal()
                    or re.fullmatch(r"собер\w*|состав\w*", token, re.I)
                    or re.fullmatch(r"\d+(?:шт\w*|pcs|kzt|тенге)", token, re.I)
                    or _GENERIC.fullmatch(token)
                    or _materials(token)
                    or token.casefold()
                    in {
                        "теперь",
                        "оставь",
                        "убери",
                        "исключи",
                        "сделай",
                        "добавь",
                        "поменяй",
                        "замени",
                        "другой",
                        "больше",
                    }
                    or resolver.resolve(token)
                    or ColorNormalizer().extract(token)
                ):
                    continue
                meaningful.append(token)
            model = " ".join(meaningful)
            known = bool(model) and any(
                phrase_in(model, primary_title(parent or name)) for parent, name, _ in family_rows
            )
            models = [model] if model and (has_category or known) else []
            if models:
                for word in meaningful:
                    clean = re.sub(r"(?<!\w)" + re.escape(word) + r"(?!\w)", " ", clean, flags=re.I)
        parsed.append((resolver.resolve(clean), labels, models, clean))
    scoped = [p for p in parsed if p[0] and (p[1] or p[2])]
    product_clauses = [i for i, p in enumerate(parsed) if p[0]]
    distinct_categories = {c for p in parsed for c in p[0]}
    local = len(distinct_categories) > 1 and (
        len(scoped) > 1
        or any(p[2] for p in parsed)
        or any((p[1] or p[2]) and i < product_clauses[-1] for i, p in enumerate(parsed))
        and not re.match(r"^(?:бренд|марка|brand)\b", text, re.I)
    )
    for categories, labels, models, _ in parsed:
        target = out.local if local and categories else None
        groups = (
            [target.setdefault(c, {"brands": [], "excluded_brands": [], "models": []}) for c in categories]
            if target is not None
            else [out.__dict__]
        )
        for group in groups:
            group["models"].extend(models)
            for label, excluded in labels:
                group["excluded_brands" if excluded else "brands"].append(label)
    out.cleaned = ", ".join(p[3].strip() for p in parsed if p[3].strip())
    for field_name in ("brands", "excluded_brands", "models"):
        setattr(out, field_name, list(dict.fromkeys(getattr(out, field_name))))
    if (
        not out.brands
        and not out.models
        and not out.local
        and not out.excluded_brands
        and not UniversalCategoryResolver().resolve(text)
        and re.fullmatch(r"[\w-]+", text)
        and not _GENERIC.fullmatch(text)
        and not _materials(text)
        and not ColorNormalizer().extract(text)
        and not re.match(r"назад|отмен|верни|сброс|дальше|ещё|продолж", lower)
    ):
        out.unresolved = True
    # Identity spans are opaque to attribute interpretation: Blue Lake may be
    # a catalog brand, and Plastic may be a literal model name.
    attributes = list(text)
    for start, end, _ in spans:
        attributes[start:end] = " " * (end - start)
    all_models = out.models + [m for local in out.local.values() for m in local["models"]]
    for cue in re.finditer(_MODEL_CUE, text, re.I):
        tail = text[cue.end() :]
        for model in sorted(all_models, key=len, reverse=True):
            match = re.match(r'\s*[«"“]?' + re.escape(model).replace(r"\ ", r"\s+") + r"(?!\w)", tail, re.I)
            if match:
                attributes[cue.end() : cue.end() + match.end()] = " " * match.end()
                break
    for label in (
        out.brands
        + out.excluded_brands
        + [v for local in out.local.values() for v in local["brands"] + local["excluded_brands"]]
    ):
        for match in re.finditer(
            r"(?<!\w)" + re.escape(label).replace(r"\ ", r"\s+") + r"(?!\w)", text, re.I
        ):
            attributes[match.start() : match.end()] = " " * (match.end() - match.start())
    out.attribute_query = "".join(attributes)
    return out
