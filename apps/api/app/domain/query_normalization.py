"""Bounded syntax repair with source offsets; never erase punctuation wholesale."""

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache


@lru_cache(maxsize=1)
def _phrases():
    from app.domain.intent import CATEGORY_TERMS
    from app.domain.taxonomy import SEEDS, terms

    aliases = [label for node in SEEDS.values() for label in node[1:]] + list(CATEGORY_TERMS.values())
    patterns = []
    for alias in aliases:
        words = terms(alias)
        if 2 <= len(words) <= 5:
            patterns.append(
                re.compile(
                    r"\b"
                    + r"[\s.,;]+".join(
                        re.escape(w) + (r"\w*" if len(w) >= 3 and not w.isascii() else "") for w in words
                    )
                    + r"\b",
                    re.I,
                )
            )
    return tuple(patterns)


@dataclass(frozen=True)
class NormalizedQuery:
    original: str
    text: str
    offsets: tuple[tuple[int, int], ...]

    def trace(self):
        return {
            "version": "syntax-normalization-v1",
            "original": self.original,
            "normalized": self.text,
            "tokens": [
                {
                    "text": m[0],
                    "normalized_start": m.start(),
                    "normalized_end": m.end(),
                    "original_start": self.offsets[m.start()][0],
                    "original_end": self.offsets[m.end() - 1][1],
                }
                for m in re.finditer(r"\w+(?:[-./+]\w+)*", self.text)
            ],
        }


def normalize_query(original):
    chars, offsets = [], []
    for n, char in enumerate(original):
        value = unicodedata.normalize("NFKC", char)
        chars.extend(value)
        offsets.extend([(n, n + 1)] * len(value))
    text = "".join(chars)

    def replace(pattern, replacement):
        nonlocal text, offsets
        pieces, mapping, cursor = [], [], 0
        for match in (
            pattern.finditer(text) if isinstance(pattern, re.Pattern) else re.finditer(pattern, text, re.I)
        ):
            pieces.append(text[cursor : match.start()])
            mapping.extend(offsets[cursor : match.start()])
            new = replacement(match)
            pieces.append(new)
            mapping.extend(
                offsets[match.start() : match.end()]
                if len(new) == len(match[0])
                else [(offsets[match.start()][0], offsets[match.end() - 1][1])] * len(new)
            )
            cursor = match.end()
        pieces.append(text[cursor:])
        mapping.extend(offsets[cursor:])
        text, offsets = "".join(pieces), mapping

    # Decimal punctuation, SKU GF-12449.40, USB-C and model words remain opaque.
    replace(
        r"\b(тираж(?:ом)?|количество|цвет(?:а)?|размер|объ[её]м|модель|бренд)\s*[.,;:—–-]+\s*(?=\w)",
        lambda m: m[1] + " ",
    )
    replace(r"\b(от|до|минимум|максимум)\s*[.,;:]+\s*(?=\d)", lambda m: m[1] + " ")
    replace(r"\b(т[её]мно|светло)\s*[.,;]+\s*(син\w*|голуб\w*|сер\w*)", lambda m: m[1] + "-" + m[2])
    replace(r"\bтермо\s*[.,;]+\s*(круж\w*|стакан\w*)", lambda m: "термо" + m[1])
    for pattern in _phrases():
        replace(pattern, lambda m: re.sub(r"[.,;]", " ", m[0]))
    replace(r"(?<=[,.;])\s*(и|или)\s*[,.;]\s*", lambda m: " " + m[1] + " ")
    replace(r"[,;.!?]{2,}", lambda m: m[0][0])
    replace(r"\s+", lambda m: " ")
    left = len(text) - len(text.lstrip())
    end = len(text.rstrip())
    return NormalizedQuery(original, text[left:end], tuple(offsets[left:end]))
