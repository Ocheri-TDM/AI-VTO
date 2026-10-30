"""Evidence for what is sold, separate from mentions of parts and contents.

Concept vocabulary remains catalog/resolver-owned. These rules describe
grammatical relationships and commercial roles, not a second product taxonomy.
"""

import json
import re
from dataclasses import asdict, dataclass
from functools import lru_cache

from app.domain.services import normalize_text
from app.domain.taxonomy import UniversalCategoryResolver, terms

PURPOSE_VERSION = "primary-product-v2"
ROLES = (
    (
        "SET",
        "set",
        r"\b(?:набор\w*|комплект\w*|gift\s+sets?|gift\s+boxes?|(?:stationery|writing|travel)\s+sets?)\b",
        ("набор", "комплект", "gift set", "gift box", "writing set"),
    ),
    ("PACKAGING", "case", r"\b(?:футляр\w*|пенал\w*|cases?|pencilcases?)\b", ("футляр", "пенал", "case")),
    (
        "PACKAGING",
        "box",
        r"\b(?:короб\w*|упаков\w*|ложемент\w*|boxes?|packaging)\b",
        ("короб", "упаков", "ложемент", "box", "packaging"),
    ),
    (
        "ACCESSORY",
        "cover",
        r"\b(?:чех\w*|обложк\w*|covers?|sleeves?)\b",
        ("чех", "обложк", "cover", "sleeve"),
    ),
    (
        "ACCESSORY",
        "lid",
        r"\b(?:крыш\w*|пробк\w*|lids?|caps?|stoppers?)\b",
        ("крыш", "пробк", "lid", "cap", "stopper"),
    ),
    (
        "ACCESSORY",
        "holder",
        r"\b(?:держател\w*|подставк\w*|holders?|stands?)\b",
        ("держател", "подставк", "holder", "stand"),
    ),
    (
        "ACCESSORY",
        "part",
        r"\b(?:запчаст\w*|запасн\w*|сменн\w*|refills?|replacements?|spare\s+parts?)\b",
        ("запчаст", "запасн", "сменн", "refill", "replacement", "spare"),
    ),
    (
        "COMPONENT",
        "handle",
        r"\b(?:дверн\w*\s+ручк\w*|ручк\w*[- ]карабин\w*|ручк\w*\s+(?:для|на)\s+(?:сумк|двер|бутыл|зонт)\w*)\b",
        ("ручк", "handle"),
    ),
)
RELATION = re.compile(r"\s+(?:с|со|для|под|на|в|во|из|без|with|without|for|in|including)\s+", re.I)
HYBRID = re.compile(r"\b(?:[2-9][\s/-]*(?:в|in)[\s/-]*1|многофункцион\w*|multifunction\w*)\b", re.I)


def concepts(text):
    return tuple(UniversalCategoryResolver().resolve(text))


@dataclass(frozen=True)
class ProductPurpose:
    role: str
    subject: str
    primary: tuple[str, ...]
    mentioned: tuple[str, ...]
    contents: tuple[str, ...]
    head: str
    category_evidence: str
    provenance: tuple[str, ...]

    def public(self):
        return asdict(self) | {"version": PURPOSE_VERSION}


def identity_inputs(
    title, supplier_category="", breadcrumbs=None, attributes=None, description="", brand=None
):
    attrs = {
        k: v
        for k, v in (attributes or {}).items()
        if isinstance(v, (str, int))
        and re.search(r"тип|вид|назнач|комплект|состав|издели|type|function|content", k, re.I)
    }
    return classify(
        title,
        supplier_category or "",
        json.dumps(breadcrumbs or [], ensure_ascii=False),
        json.dumps(attrs, sort_keys=True, ensure_ascii=False),
        (description or "")[:2000],
        brand or "",
    )


@lru_cache(maxsize=8192)
def classify(title, category="", breadcrumbs="[]", attributes="{}", description="", brand="", nested=False):
    name = normalize_text(title)
    if brand and name.startswith(normalize_text(brand) + " "):
        name = name[len(normalize_text(brand)) + 1 :]
    # A quoted model called "Box" is not packaging; relation words outside
    # the model still delimit the actual main object.
    unquoted = re.sub(r'[«"“][^»"”]*[»"”]', " ", name)
    parts = RELATION.split(unquoted, maxsplit=1)
    head = parts[0].strip(" .,:;—-")
    primary = concepts(head)
    mentioned = list(concepts(name))
    writing = bool(re.search(r"шариков|пишущ|перьев|роллер|ballpoint|fountain\s+pen|writing\s+pen", name))
    nonwriting_handle = not writing and bool(
        re.search(r"(?:круж|бутыл|зонт|сумк|двер|стакан|рюкзак)\w*.{0,20}\sс\sручк|ручк\w*[- ]карабин", name)
    )
    if nonwriting_handle:
        mentioned = [c for c in mentioned if c != "pen"]
    attrs = json.loads(attributes)
    for key, value in attrs.items():
        if re.search(r"комплект|состав|content", key, re.I):
            mentioned.extend(concepts(str(value)))
    composition = re.search(
        r"(?:в\s+состав\w*|комплектаци\w*|в\s+комплект\w*\s+вход\w*|contains)\s*[:—-]?\s*(.+)",
        normalize_text(description),
    )
    if composition:
        mentioned.extend(concepts(composition[1]))
    role, subject = "PRODUCT", ""
    candidates = []
    for kind, subtype, pattern, _ in ROLES:
        if kind == "COMPONENT" and writing:
            continue
        for match in re.finditer(pattern, unquoted if kind == "COMPONENT" else head, re.I):
            prefix = head[: match.start()]
            english_compound = not re.search(r"[а-я]", head) and not head[match.end() :].strip(" s")
            if not concepts(prefix) or english_compound:
                candidates.append((match.start(), kind, subtype))
    provenance = ["title.main_object"]
    # A source type mentioned only after "for/without/..." describes a
    # related object, not evidence identifying a model-only main product.
    related_category = bool(
        len(parts) > 1
        and not primary
        and any(any(a.startswith(b) or b.startswith(a) for a in terms(parts[1])) for b in terms(category))
    )
    if related_category:
        provenance.append("supplier_category.related_object_conflict")
    if candidates:
        _, role, subject = min(candidates)
        primary = ()
    elif (
        HYBRID.search(name)
        or any(len(concepts(m[0])) > 1 for m in re.finditer(r"\b\w+-\w+\b", head))
        or {"pen", "pencil"} <= set(primary)
        and re.search(r"вечн\w*|everlasting|eternal", head)
    ):
        role = "HYBRID"
        primary = concepts(name)
        # Handles remain components even on a multi-function bottle/umbrella.
        if (
            "pen" in primary
            and "pen" not in concepts(head)
            and not re.search(r"шариков|пишущ|перьев|ballpoint|fountain\s+pen", name)
        ):
            primary = tuple(c for c in primary if c != "pen")
    elif len(primary) > 1 and re.search(r"\s(?:и|and)\s|\+", head):
        role, subject, primary = "SET", "implicit_set", ()
    if not primary and role == "PRODUCT":
        structured = " ".join(
            str(v) for k, v in attrs.items() if re.search(r"тип|вид|назнач|type|function", k, re.I)
        )
        primary = concepts(structured)
        if primary:
            provenance.append("attributes.product_type")
        elif (
            category
            and not related_category
            and not re.match(r"\s*(?:для|with|for)\b", normalize_text(category))
        ):
            # A precise source category can identify a model-only title. An
            # explicit accessory/set head above always overrides this fallback.
            primary = concepts(category)
            if primary:
                provenance.append("supplier_category.unopposed_product_type")
    if not primary and role == "PRODUCT":
        role = "UNKNOWN"
    if composition:
        provenance.append("description.explicit_contents")
    if attrs:
        provenance.append("attributes")
    contents = []
    if not nested and role in ("SET", "PRODUCT", "HYBRID"):
        body = re.sub(ROLES[0][2], " ", unquoted, count=1, flags=re.I) if role == "SET" else unquoted
        texts = [body] + [str(v) for k, v in attrs.items() if re.search(r"комплект|состав|content", k, re.I)]
        if composition:
            texts.append(composition[1])
        for text in texts:
            for piece in re.split(r"[,;:+]|\s+(?:и|and|с|со|из|with|contains)\s+", text):
                child = classify(piece, nested=True)
                if child.role in ("PRODUCT", "HYBRID"):
                    contents.extend(child.primary)
        if nonwriting_handle:
            contents = [c for c in contents if c != "pen"]
    return ProductPurpose(
        role,
        subject,
        tuple(dict.fromkeys(primary)),
        tuple(dict.fromkeys(mentioned)),
        tuple(dict.fromkeys(contents)),
        head,
        category,
        tuple(provenance),
    )


def query_relation(query):
    include_sets = bool(
        re.search(r"(?:включая|в том числе|вместе с|including)\s+(?:набор|комплект|sets?)", query, re.I)
    )
    cleaned = re.split(r"\b(?:включая|в том числе|including)\b", query, maxsplit=1, flags=re.I)[0]
    identity = identity_inputs(cleaned)
    # Retain the earlier chat contract for imperative assembly wording. It
    # means category retrieval, never a revival of the removed allocator.
    assembly = bool(re.search(r"\b(?:собери|собрать|составь)\b", query, re.I))
    if (
        identity.role in ("SET", "PACKAGING", "ACCESSORY", "COMPONENT")
        and identity.subject != "implicit_set"
        and not include_sets
        and not assembly
    ):
        return {
            "scope": identity.role,
            "subject": identity.subject,
            "required_concepts": list(identity.mentioned),
            "include_sets": False,
        }
    if identity.primary and re.search(r"\s(?:с|со|with)\s", query, re.I):
        required = [c for c in identity.mentioned if c not in identity.primary]
        if required:
            return {
                "scope": "ASSOCIATED",
                "subject": "",
                "required_concepts": required,
                "include_sets": False,
            }
    return {"scope": "STANDALONE", "subject": "", "required_concepts": [], "include_sets": include_sets}


def purpose_match(identity, branch):
    present = set(identity.primary) | set(
        identity.contents if identity.role in ("SET", "PRODUCT", "HYBRID") else identity.mentioned
    )
    required = set(branch.required_concepts)
    accepted = False
    reason = "PRIMARY_CONCEPT_MISMATCH"
    match = "EXACT"
    if branch.scope in ("SET", "PACKAGING", "ACCESSORY", "COMPONENT"):
        accepted = (
            identity.role == branch.scope
            and (not branch.subject or identity.subject == branch.subject)
            and required <= present
        )
        reason = "REQUESTED_COMMERCIAL_ROLE" if accepted else "COMMERCIAL_ROLE_OR_CONTENTS_MISMATCH"
    elif branch.scope == "ASSOCIATED":
        accepted = (
            identity.role in ("PRODUCT", "HYBRID", "SET")
            and branch.category in present
            and required <= present
        )
        reason = "REQUESTED_ASSOCIATION" if accepted else "REQUIRED_ASSOCIATION_MISSING"
    elif identity.role == "SET" and branch.include_sets and branch.category in present:
        accepted = True
        match = "RELATED"
        reason = "EXPLICITLY_INCLUDED_SET"
    elif identity.role not in ("PRODUCT", "HYBRID", "UNKNOWN"):
        reason = "NOT_A_STANDALONE_PRODUCT"
    elif branch.category.startswith("dynamic:"):
        requested = terms(UniversalCategoryResolver().concept(branch.label))
        source = (
            ""
            if "supplier_category.related_object_conflict" in identity.provenance
            else identity.category_evidence
        )
        evidence = terms(identity.head + " " + source)
        accepted = bool(requested) and all(
            any(a.startswith(b) or b.startswith(a) for a in evidence) for b in requested
        )
        reason = "MAIN_OBJECT_OR_SOURCE_TYPE" if accepted else reason
    elif branch.category in identity.primary:
        accepted = True
        reason = "PRIMARY_FUNCTION_CONFIRMED"
    return {
        "accepted": accepted,
        "category_match": match if accepted else "REJECTED",
        "reason": reason,
        "identity": identity.public(),
    }


def scope_words(branch):
    return tuple(
        word
        for role, subject, _, words in ROLES
        if role == branch.scope and (not branch.subject or subject == branch.subject)
        for word in words
    )
