"""Deterministic entity matching.

Whether two claims are about the same thing is decided before anything about
their values is considered. The rule is conservative on purpose: surface forms
of one name should unify ("Delhivery", "Delhivery Ltd.", "Delhivery Limited"),
but two genuinely different organisations must never be merged just because
their names are short and share letters ("Delhivery" and "DHL").
"""
from __future__ import annotations

import re
import unicodedata

from rapidfuzz import fuzz

from .lexicon import ORG_SUFFIXES

_PUNCT = re.compile(r"[^\w\s]")
_WS = re.compile(r"\s+")


def normalize_entity(name: str) -> str:
    """Canonical key: unicode-folded, de-punctuated, legal suffixes removed."""
    text = unicodedata.normalize("NFKC", name or "").lower()
    text = text.replace("&", " and ")
    text = _PUNCT.sub(" ", text)
    words = [w for w in _WS.sub(" ", text).split() if w]
    while words and words[-1] in ORG_SUFFIXES:
        words.pop()
    return " ".join(words).strip()


# Connective words are not voiced in an acronym: RBI, not RBOI.
_ACRONYM_SKIP = {"of", "the", "and", "for", "in", "de", "a", "an"}


def _acronym(words: list[str]) -> str:
    return "".join(w[0] for w in words if w and w not in _ACRONYM_SKIP)


def same_entity(a: str, b: str, *, threshold: float = 0.86) -> tuple[bool, float, str]:
    """Return (same, score, reason)."""
    na, nb = normalize_entity(a), normalize_entity(b)
    if not na or not nb:
        return (False, 0.0, "one subject has no comparable name")
    if na == nb:
        return (True, 1.0, f"identical after normalisation ({na!r})")

    wa, wb = na.split(), nb.split()
    sa, sb = set(wa), set(wb)

    # One name contains the other in full: "delhivery" vs "delhivery limited"
    # (suffixes already stripped, so this is a real containment).
    if sa <= sb or sb <= sa:
        score = min(len(sa), len(sb)) / max(len(sa), len(sb))
        return (True, round(max(0.9, score), 4),
                f"one name contains the other ({na!r} / {nb!r})")

    # An acronym only matches when it is built from the other name's words, and
    # only when it is actually written as an acronym. This is what keeps
    # "DHL" from matching "Delhivery" -- D-H-L is not Delhivery's initials.
    # Try the acronym against both the suffix-stripped name and the full one:
    # "IMF" comes from International Monetary *Fund*, and "fund" is a suffix
    # that normalisation removes.
    full_a = _WS.sub(" ", _PUNCT.sub(" ", (a or "").lower())).split()
    full_b = _WS.sub(" ", _PUNCT.sub(" ", (b or "").lower())).split()
    for short, variants, s_raw in ((na, (wb, full_b), a), (nb, (wa, full_a), b)):
        if len(short) >= 2 and " " not in short:
            for words in variants:
                if len(words) >= 2 and short == _acronym(words):
                    return (True, 0.9, f"{s_raw!r} is the acronym of the other name")

    # Fuzzy agreement, but require shared content words so that two short
    # unrelated names cannot pass on letter overlap alone.
    ratio = fuzz.token_sort_ratio(na, nb) / 100.0
    overlap = len(sa & sb) / max(1, min(len(sa), len(sb)))
    if ratio >= threshold and overlap > 0:
        return (True, round(ratio, 4), f"names agree closely ({na!r} ~ {nb!r})")
    return (False, round(ratio, 4),
            f"different entities ({na!r} vs {nb!r}); similarity {ratio:.2f}")
