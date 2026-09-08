"""Context-aware comparison of two grounded claims.

Entirely deterministic. Every verdict is produced by a rule that actually fired,
and the trace records those rules in the order they ran -- there is no
after-the-fact narration of a decision made somewhere else.

Order matters. Identity is settled before values are looked at:

    entity gate ─▶ predicate gate ─▶ context ─▶ values ─▶ verdict

so two unrelated properties of the same company can never reach the value
comparison, and a difference in period or scope is considered before a
difference in number is called a disagreement.

Seven outcomes, because forcing everything into three produces false
contradictions: corroboration, contradiction, reconciled, supersedes,
partial_cover, underspecified, unrelated.
"""
from __future__ import annotations

import hashlib
import logging

from rapidfuzz import fuzz

from .config import CONFIG
from .entity import same_entity
from .lexicon import RATE_PREDICATE_TERMS
from .models import Relation
from .normalize import compare_periods, parse_value, unit_signature
from .predicate import content_tokens, same_predicate

log = logging.getLogger("fkl.compare")

_TEXT_SAME = 0.80
_TEXT_DIFFERENT = 0.45

# Qualifier fields that, when they differ, explain a difference in value.
CONTEXT_FIELDS = ("period", "scope", "basis", "geography", "denominator", "as_of")

# Modalities that describe the same measurement at different stages of revision.
_REVISION_ORDER = {"estimate": 0, "projection": 0, "provisional": 1,
                   "reported": 2, "revised": 3}


def relation_id_for(a_id: str, b_id: str) -> str:
    lo, hi = sorted([a_id, b_id])
    return hashlib.sha256(f"{lo}|{hi}".encode()).hexdigest()[:20]


# --------------------------------------------------------------------------
# field comparisons
# --------------------------------------------------------------------------
def _text_field(a: str | None, b: str | None, label: str) -> tuple[str, str]:
    """same / different / unknown for a free-text context field.

    Both-absent counts as *same*: two equally unqualified claims are comparable.
    Treating it as unknown would make almost every pair ambiguous, since most
    claims state no scope. Only the one-sided case is genuinely unknown.
    """
    a_s, b_s = (a or "").strip(), (b or "").strip()
    if not a_s and not b_s:
        return ("same", f"neither claim states a {label}")
    if not a_s or not b_s:
        return ("unknown", f"only one claim states a {label} ({(a_s or b_s)!r})")
    score = fuzz.token_set_ratio(a_s.lower(), b_s.lower()) / 100.0
    if score >= _TEXT_SAME:
        return ("same", f"same {label} ({a_s!r} ~ {b_s!r})")
    if score <= _TEXT_DIFFERENT:
        return ("different", f"different {label}: {a_s!r} vs {b_s!r}")
    return ("unknown", f"{label} only partly overlaps ({a_s!r} vs {b_s!r})")


def _unit_check(a: dict, b: dict) -> tuple[str, str, dict, dict]:
    """Units differ in two very different ways.

    A scale difference (crore vs million) is not a disagreement -- it is
    normalised away before the values are compared. A currency difference, or a
    rate compared against an absolute quantity, means the numbers are not
    commensurable at all.
    """
    ua = unit_signature(a.get("unit") or a.get("ctx_unit"), parse_value(a["value"]))
    ub = unit_signature(b.get("unit") or b.get("ctx_unit"), parse_value(b["value"]))
    if ua["currency"] and ub["currency"] and ua["currency"] != ub["currency"]:
        return ("incomparable",
                f"different currencies ({ua['currency']} vs {ub['currency']})", ua, ub)
    if ua["percent"] != ub["percent"]:
        return ("incomparable",
                "one value is a rate and the other an absolute quantity", ua, ub)
    # A unit stated on one side only. "US$3.9 trillion" against a bare "9.2"
    # from a table cell is not a crore-versus-million difference that
    # normalisation can absorb -- the second number's magnitude is unanchored,
    # so whether the two disagree is simply unknown. Silently adopting the
    # stated unit for both is how an eleven-order-of-magnitude gap gets
    # reported as a contradiction.
    declared_a = bool(ua["currency"]) or ua["scale"] != 1.0
    declared_b = bool(ub["currency"]) or ub["scale"] != 1.0
    if declared_a != declared_b:
        stated, bare = ((a, b) if declared_a else (b, a))
        return ("unknown",
                f"only one claim states a unit "
                f"({(stated.get('ctx_unit') or stated.get('unit'))!r}); "
                f"{bare['value']!r} is unanchored", ua, ub)
    if ua["scale"] != ub["scale"]:
        return ("same",
                f"different scale ({a.get('ctx_unit')!r} vs {b.get('ctx_unit')!r}); "
                f"normalised before comparison", ua, ub)
    return ("same", f"comparable units ({a.get('ctx_unit')!r} vs {b.get('ctx_unit')!r})", ua, ub)


def _dimensioned(sig: dict) -> bool:
    """Does this claim say what its number is measured in?"""
    return bool(sig["currency"]) or sig["percent"] or sig["scale"] != 1.0 \
        or bool(sig["raw"])


def _numeric(claim: dict, sig: dict) -> float | None:
    """Scale-normalised number for a claim, preferring the stored value_num."""
    if claim.get("value_num") is not None:
        return float(claim["value_num"])
    parsed = parse_value(claim["value"])
    if parsed.base_number is None:
        return None
    scale = parsed.scale if parsed.scale != 1.0 else sig.get("scale", 1.0)
    return parsed.base_number * scale


def _value_check(a: dict, b: dict, ua: dict, ub: dict
                 ) -> tuple[str, str, dict]:
    """equal / equivalent / differ / incomparable, plus the measured delta."""
    x, y = _numeric(a, ua), _numeric(b, ub)
    delta: dict = {}

    if x is None or y is None:
        # Non-numeric (semantic) claims compare textually.
        if x is None and y is None:
            score = fuzz.token_set_ratio(a["value"].lower(), b["value"].lower()) / 100.0
            delta["text_similarity"] = round(score, 4)
            if score >= 0.92:
                return ("equal", f"textual values match ({a['value']!r} ~ {b['value']!r})", delta)
            if score >= 0.72:
                return ("equivalent", f"textual values are close ({a['value']!r} ~ {b['value']!r})", delta)
            return ("differ", f"textual values differ ({a['value']!r} vs {b['value']!r})", delta)
        return ("incomparable", "one value is numeric and the other is not", delta)

    delta["a"], delta["b"] = x, y
    delta["absolute_difference"] = abs(x - y)

    if ua["percent"] and ub["percent"]:
        # Rates are compared in percentage points. A relative tolerance would
        # call 4.0% and 2.8% a 30% difference and hide that these are two
        # incompatible reported rates.
        pp = abs(x - y)
        delta["percentage_point_difference"] = round(pp, 4)
        if pp < CONFIG.percentage_point_tolerance:
            return ("equivalent",
                    f"same rate to the reported precision ({x:g}% vs {y:g}%)", delta)
        return ("differ",
                f"rates differ by {pp:.2f} percentage points ({x:g}% vs {y:g}%)", delta)

    if x == y:
        return ("equal", f"identical after normalisation ({x:g})", delta)
    denom = max(abs(x), abs(y))
    if denom == 0:
        return ("equal", "both values are zero", delta)
    rel = abs(x - y) / denom
    delta["relative_difference"] = round(rel, 6)
    if rel <= CONFIG.value_tolerance:
        return ("equivalent", f"values agree within {CONFIG.value_tolerance:.1%} "
                              f"({x:g} vs {y:g})", delta)
    if rel <= CONFIG.rounding_tolerance:
        return ("equivalent", f"values differ by {rel:.2%}, consistent with rounding "
                              f"({x:g} vs {y:g})", delta)
    return ("differ", f"values differ by {rel:.1%} ({x:g} vs {y:g})", delta)


def _more_specific(a_pred: str, b_pred: str) -> int:
    """+1 if a is a strict refinement of b, -1 if b refines a, else 0."""
    ta, tb = set(content_tokens(a_pred)), set(content_tokens(b_pred))
    if tb and tb < ta:
        return 1
    if ta and ta < tb:
        return -1
    return 0


# --------------------------------------------------------------------------
# the decision
# --------------------------------------------------------------------------
def compare_claims(a: dict, b: dict, similarity: float = 0.0,
                   subject_similarity: float | None = None,
                   predicate_similarity: float | None = None) -> tuple[Relation, bool]:
    """Return (relation, needs_escalation)."""
    trace: list[str] = []
    context_diff: dict[str, list[str | None]] = {}

    def finish(kind: str, explanation: str, *, rel_conf: float,
               match_conf: float, value_agreement=None, delta=None,
               escalate: bool = False) -> tuple[Relation, bool]:
        # Overall confidence is bounded by the weaker claim: a shaky extraction
        # must never yield a confident verdict just because two numbers differ.
        weakest = min(a["final_confidence"], b["final_confidence"])
        overall = weakest * match_conf * rel_conf
        return (Relation(
            relation_id=relation_id_for(a["claim_id"], b["claim_id"]),
            claim_a_id=a["claim_id"], claim_b_id=b["claim_id"],
            kind=kind, decided_by="rules", similarity=similarity,
            subject_similarity=subject_similarity,
            predicate_similarity=predicate_similarity,
            match_confidence=round(match_conf, 4),
            relationship_confidence=round(rel_conf, 4),
            confidence=round(max(0.0, min(1.0, overall)), 4),
            explanation=explanation, reasoning_trace=trace,
            context_diff=context_diff, value_agreement=value_agreement,
            value_delta=delta,
        ), escalate)

    # -- 1. entity ---------------------------------------------------------
    ent_ok, ent_score, ent_why = same_entity(a["subject"], b["subject"])
    trace.append(f"entity gate: {'matched' if ent_ok else 'rejected'} -- {ent_why}")
    if not ent_ok:
        return finish("unrelated", f"Different subjects, so the claims are not comparable. {ent_why}",
                      rel_conf=0.9, match_conf=ent_score)

    # -- 2. predicate ------------------------------------------------------
    pred_verdict, pred_score, pred_why = same_predicate(a["predicate"], b["predicate"])
    trace.append(f"predicate gate: {pred_verdict} -- {pred_why}")
    if pred_verdict == "different":
        return finish("unrelated",
                      f"Same subject but different properties, so no comparison is meaningful. {pred_why}",
                      rel_conf=0.9, match_conf=pred_score)

    match_conf = min(1.0, (ent_score * 0.5) + (pred_score * 0.5)) if pred_score else ent_score * 0.5

    # -- 3. context --------------------------------------------------------
    period_v, period_why = compare_periods(a["ctx_period"], b["ctx_period"])
    trace.append(f"period: {period_v} -- {period_why}")
    if period_v == "different":
        context_diff["period"] = [a["ctx_period"], b["ctx_period"]]

    unit_v, unit_why, ua, ub = _unit_check(a, b)
    trace.append(f"unit: {unit_v} -- {unit_why}")

    # The unit sits alongside the other context fields when deciding whether
    # the pair is pinned down well enough to call a disagreement.
    field_verdicts = {"period": period_v, "unit": unit_v}
    for field in ("scope", "basis", "geography", "denominator", "as_of"):
        va, vb = a.get(f"ctx_{field}"), b.get(f"ctx_{field}")
        verdict, why = _text_field(va, vb, field)
        field_verdicts[field] = verdict
        trace.append(f"{field}: {verdict} -- {why}")
        if verdict == "different":
            context_diff[field] = [va, vb]

    mod_a, mod_b = a.get("modality") or "reported", b.get("modality") or "reported"
    modality_differs = mod_a != mod_b
    trace.append(f"modality: {mod_a} vs {mod_b}"
                 + (" -- these describe different stages of the same measurement"
                    if modality_differs else " -- same"))

    # -- 4. values ---------------------------------------------------------
    value_v, value_why, delta = _value_check(a, b, ua, ub)
    trace.append(f"value: {value_v} -- {value_why}")

    context_differs = bool(context_diff)
    context_unknown = any(v == "unknown" for v in field_verdicts.values())
    values_agree = value_v in ("equal", "equivalent")
    values_differ = value_v == "differ"

    # -- 5. verdict --------------------------------------------------------
    if unit_v == "incomparable":
        trace.append("decision: units are not commensurable -> underspecified")
        return finish("underspecified",
                      f"These figures are not directly comparable: {unit_why}. "
                      f"No conclusion about agreement can be drawn.",
                      rel_conf=0.55, match_conf=match_conf,
                      value_agreement=value_v, delta=delta)

    if pred_verdict == "related":
        # Same family, different measure. A more specific property whose value
        # is smaller than the broader one is a component of it, not a rival
        # claim about it -- reporting that as a contradiction is the classic
        # segments-versus-total error.
        direction = _more_specific(a["predicate"], b["predicate"])
        if direction != 0 and delta.get("a") is not None and delta.get("b") is not None:
            narrow, broad = ((delta["a"], delta["b"]) if direction == 1
                             else (delta["b"], delta["a"]))
            if abs(narrow) <= abs(broad) * 1.02:
                share = (abs(narrow) / abs(broad) * 100) if broad else 0.0
                trace.append(
                    f"decision: the narrower property is {share:.0f}% of the broader one, "
                    f"consistent with it being a component -> partial_cover")
                return finish("partial_cover",
                              f"One claim measures a narrower quantity than the other "
                              f"({pred_why}). The narrower figure is {share:.0f}% of the "
                              f"broader one, which is consistent with partial coverage "
                              f"rather than disagreement.",
                              rel_conf=0.7, match_conf=match_conf,
                              value_agreement=value_v, delta=delta)
        trace.append("decision: related but not the same property -> partial_cover")
        return finish("partial_cover",
                      f"These describe related but distinct properties, so their values "
                      f"are not expected to match. {pred_why}",
                      rel_conf=0.6, match_conf=match_conf,
                      value_agreement=value_v, delta=delta)

    if modality_differs and values_differ and period_v in ("same", "unknown"):
        order_a = _REVISION_ORDER.get(mod_a, 2)
        order_b = _REVISION_ORDER.get(mod_b, 2)
        if order_a != order_b:
            later, earlier = ((a, b) if order_a > order_b else (b, a))
            later_mod = mod_a if order_a > order_b else mod_b
            earlier_mod = mod_b if order_a > order_b else mod_a
            trace.append(f"decision: '{earlier_mod}' superseded by '{later_mod}' "
                         f"for the same period -> supersedes")
            return finish("supersedes",
                          f"The same measurement at two stages: the {earlier_mod} figure "
                          f"({earlier['value']}) is superseded by the {later_mod} figure "
                          f"({later['value']}). A revision is not a contradiction.",
                          rel_conf=0.8, match_conf=match_conf,
                          value_agreement=value_v, delta=delta)

    if values_agree and not context_differs:
        trace.append("decision: comparable context + agreeing values -> corroboration")
        return finish("corroboration",
                      f"Both documents report the same {a['predicate']} for {a['subject']}: "
                      f"{value_why}. Context is comparable.",
                      rel_conf=0.9, match_conf=match_conf,
                      value_agreement=value_v, delta=delta)

    if values_differ and context_differs:
        fields = ", ".join(context_diff)
        detail = "; ".join(
            f"{k}: {v[0]!r} vs {v[1]!r}" for k, v in context_diff.items())
        trace.append(f"decision: differing values explained by differing {fields} -> reconciled")
        return finish("reconciled",
                      f"The values differ ({value_why}), but the context differs too "
                      f"({detail}). The claims measure different things, so this is not "
                      f"a conflict.",
                      rel_conf=0.85, match_conf=match_conf,
                      value_agreement=value_v, delta=delta)

    if values_agree and context_differs:
        fields = ", ".join(context_diff)
        trace.append(f"decision: agreeing values but differing {fields} -> reconciled")
        return finish("reconciled",
                      f"The values coincide ({value_why}) but the claims cover different "
                      f"{fields}, so they are compatible rather than corroborating the "
                      f"same measurement.",
                      rel_conf=0.6, match_conf=match_conf,
                      value_agreement=value_v, delta=delta)

    if values_differ and context_unknown:
        missing = [f for f, v in field_verdicts.items() if v == "unknown"]
        trace.append(f"decision: values differ but {', '.join(missing)} stated on only one "
                     f"side -> underspecified (a missing qualifier is not a disagreement)")
        return finish("underspecified",
                      f"The values differ ({value_why}), but {', '.join(missing)} "
                      f"{'is' if len(missing) == 1 else 'are'} stated by only one document, "
                      f"so the rules cannot tell a real disagreement from a difference in "
                      f"what is being measured.",
                      rel_conf=0.5, match_conf=match_conf,
                      value_agreement=value_v, delta=delta, escalate=True)

    if values_differ and ua["percent"] and ub["percent"]:
        # A percentage is only comparable when it is clear what it is a
        # percentage OF. If neither predicate names a rate and neither claim
        # states a denominator, these are two shares of unknown bases -- a
        # margin and a segment weighting, say -- and calling them contradictory
        # would be wrong.
        named_rate = (set(content_tokens(a["predicate"])) & RATE_PREDICATE_TERMS
                      or set(content_tokens(b["predicate"])) & RATE_PREDICATE_TERMS)
        if not named_rate and not (a.get("ctx_denominator") or b.get("ctx_denominator")):
            trace.append("decision: both values are percentages, but neither predicate "
                         "names a rate and no denominator is stated -> underspecified "
                         "(proportions of unknown bases are not comparable)")
            return finish("underspecified",
                          f"Both figures are percentages ({a['value']} vs {b['value']}) "
                          f"but neither document states what they are percentages of, "
                          f"and the property name does not identify a rate. They cannot "
                          f"be compared.",
                          rel_conf=0.45, match_conf=match_conf,
                          value_agreement=value_v, delta=delta, escalate=True)

    if values_differ and a.get("origin") == "table" and b.get("origin") == "table" \
            and not _dimensioned(ua) and not _dimensioned(ub):
        # Two bare table cells, neither carrying a unit. A row label is only
        # meaningful relative to its table: "Deposits" in the RBI's money and
        # credit table is year-on-year growth in per cent, "Deposits" in the
        # IMF's fiscal table is a balance as a share of GDP. The labels read
        # alike, the periods line up, and the numbers disagree -- but they were
        # never the same measurement, and the claim carries no unit that would
        # have revealed it. A sentence claim is exempt because its predicate
        # comes with the sentence that qualified it; a naked cell has no such
        # context. Same reasoning as the percentage guard above: with no
        # dimension there is no accusation to make, only a pair worth a
        # human's attention.
        named_rate = (set(content_tokens(a["predicate"])) & RATE_PREDICATE_TERMS
                      or set(content_tokens(b["predicate"])) & RATE_PREDICATE_TERMS)
        if not named_rate:
            trace.append("decision: both claims are unitless table cells and the row "
                         "label does not identify a rate -> underspecified (a row label "
                         "alone does not fix what is being measured)")
            return finish("underspecified",
                          f"The values differ ({a['value']} vs {b['value']}), but both "
                          f"come from table cells that state no unit, and the row label "
                          f"{a['predicate']!r} does not by itself say what is being "
                          f"measured. Two tables can use the same row label for different "
                          f"quantities, so this is not evidence of a disagreement.",
                          rel_conf=0.45, match_conf=match_conf,
                          value_agreement=value_v, delta=delta, escalate=True)

    if values_differ:
        pp = delta.get("percentage_point_difference")
        extra = (f" Difference: {pp:.2f} percentage points." if pp is not None
                 else f" Absolute difference: {delta.get('absolute_difference', 0):,.4g}.")
        trace.append("decision: matched subject and property, comparable context, "
                     "values outside tolerance -> contradiction")
        return finish("contradiction",
                      f"Both documents state {a['predicate']} for {a['subject']} under "
                      f"equivalent context, but disagree: {value_why}.{extra} No period, "
                      f"scope, unit, basis or qualifier difference accounts for the gap.",
                      rel_conf=0.85, match_conf=match_conf,
                      value_agreement=value_v, delta=delta)

    trace.append("decision: inconclusive")
    return finish("underspecified",
                  f"Related claims, but the comparison was inconclusive: {value_why}.",
                  rel_conf=0.4, match_conf=match_conf,
                  value_agreement=value_v, delta=delta, escalate=True)
