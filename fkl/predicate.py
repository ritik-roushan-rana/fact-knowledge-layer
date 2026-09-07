"""Deterministic predicate matching.

Kept strictly separate from entity matching. The failure this exists to prevent
is two unrelated properties of the same entity being compared -- "Delhivery
employee count" against "Delhivery revenue" -- which produces confident
nonsense contradictions.

Three outcomes rather than two, because "revenue" and "revenue from operations"
are neither the same measurement nor unrelated:

    same       -> compare the values directly
    related    -> same family, different measure; never a contradiction
    different  -> do not compare at all
"""
from __future__ import annotations

import re
import unicodedata

from rapidfuzz import fuzz

from .lexicon import ANTONYM_GROUPS, PREDICATE_STOPWORDS

_PUNCT = re.compile(r"[^\w\s%]")
_WS = re.compile(r"\s+")


def _singular(word: str) -> str:
    # "gross", "class", "gas" are not plurals -- stripping the final s turns
    # "gross" into "gros" and silently breaks the net/gross antonym check.
    if word.endswith("ss") or word.endswith("us") or word.endswith("is"):
        return word
    for suffix, repl in (("ies", "y"), ("sses", "ss"), ("ses", "s"), ("s", "")):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)] + repl
    return word


def normalize_predicate(text: str) -> str:
    t = unicodedata.normalize("NFKC", text or "").lower()
    t = _PUNCT.sub(" ", t)
    return _WS.sub(" ", t).strip()


def content_tokens(text: str) -> list[str]:
    """Discriminating words only, singularised."""
    return [_singular(w) for w in normalize_predicate(text).split()
            if w not in PREDICATE_STOPWORDS and len(w) > 1]


def antonym_conflict(a_tokens: set[str], b_tokens: set[str]) -> tuple[str, str] | None:
    """Find a qualifier present on one side whose opposite is on the other."""
    for group in ANTONYM_GROUPS:
        ta, tb = a_tokens & group, b_tokens & group
        if ta and tb and not (ta & tb):
            return (sorted(ta)[0], sorted(tb)[0])
    return None


def _align_acronyms(ta: list[str], tb: list[str]) -> tuple[list[str], list[str]]:
    """Collapse a spelled-out run into the acronym the other side uses.

    Documents mix "CPI inflation" and "consumer price inflation" freely. A run
    of consecutive words whose initials spell a single token on the other side
    is that token, so the two predicates become directly comparable. Same rule
    as entity acronyms; no vocabulary of specific abbreviations.
    """
    def collapse(short: list[str], long: list[str]) -> list[str]:
        out = list(long)
        for tok in short:
            n = len(tok)
            if not (2 <= n <= 5) or not tok.isalpha():
                continue
            for i in range(len(out) - n + 1):
                run = out[i:i + n]
                if all(w.isalpha() for w in run) and "".join(w[0] for w in run) == tok:
                    out[i:i + n] = [tok]
                    break
        return out

    return (collapse(tb, ta), collapse(ta, tb))


def same_predicate(a: str, b: str, *, same_threshold: float = 0.70,
                   related_threshold: float = 0.40) -> tuple[str, float, str]:
    """Return (verdict, score, reason) with verdict in same / related / different."""
    ta, tb = content_tokens(a), content_tokens(b)
    if not ta or not tb:
        return ("different", 0.0, "predicate has no comparable content words")
    ta, tb = _align_acronyms(ta, tb)
    sa, sb = set(ta), set(tb)

    if sa == sb:
        return ("same", 1.0, f"identical property after normalisation ({' '.join(sorted(sa))!r})")

    conflict = antonym_conflict(sa, sb)
    if conflict:
        return ("related", 0.0,
                f"same family but opposed qualifiers ({conflict[0]!r} vs {conflict[1]!r}); "
                f"these measure different things")

    # Containment is the important case, and it must NOT be scored with
    # token_set_ratio: that returns 100 whenever one token set is a subset of
    # the other, so "cash" scored a perfect match against "cash generated from
    # operations" and produced confident false contradictions.
    if sa < sb or sb < sa:
        extra = (sb - sa) if sa < sb else (sa - sb)
        if len(extra) <= 1:
            # One extra qualifier: "cash" / "cash balance". Same measurement.
            return ("same", 0.85,
                    f"one predicate adds a single qualifier ({sorted(extra)[0]!r}) "
                    f"to the other; same measurement")
        return ("related", round(len(sa & sb) / len(sa | sb), 4),
                f"one predicate is a strict refinement of the other "
                f"(adds {sorted(extra)}); these are different measurements")

    jaccard = len(sa & sb) / len(sa | sb)
    fuzzy = fuzz.token_sort_ratio(" ".join(sorted(ta)), " ".join(sorted(tb))) / 100.0
    score = max(jaccard, fuzzy * 0.9)

    # A shared head word is required: without it, high fuzzy overlap between
    # unrelated properties can still clear a threshold.
    if not (sa & sb):
        return ("different", round(score, 4),
                f"no shared content word ({sorted(sa)} vs {sorted(sb)})")

    if score >= same_threshold:
        return ("same", round(score, 4),
                f"equivalent property ({' '.join(ta)!r} ~ {' '.join(tb)!r})")
    if score >= related_threshold:
        extra_a, extra_b = sorted(sa - sb), sorted(sb - sa)
        return ("related", round(score, 4),
                f"related but not identical property "
                f"(one adds {extra_a or 'nothing'}, the other {extra_b or 'nothing'})")
    return ("different", round(score, 4),
            f"different property ({' '.join(ta)!r} vs {' '.join(tb)!r})")
