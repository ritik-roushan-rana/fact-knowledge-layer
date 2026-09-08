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
from .lexicon import RATE_PREDICATE_TERMS, STATUS_OPPOSITES
from .models import Relation
from .normalize import (compare_periods, parse_value, period_precedes,
                        precision_of, unit_signature)
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
# Rule IDs
# --------------------------------------------------------------------------
#
# Stable, ordered identifiers for every branch in `compare_claims`. Two
# relations with the same rule_id were decided by the same code path -- so
# a rule change produces a clean diff in `scripts/replay.py`, and a rename of
# the user-facing verdict kind (`kind`) never invalidates the trail.
#
# Ordering matches the cascade order: the first rule that fires wins.
#
#   R00-R09   rejected before comparison (entity/predicate/unit gates)
#   R10-R19   same-family relationships (component-of-total, partial cover)
#   R20-R29   revisions (numeric and status)
#   R30-R39   status-claim outcomes
#   R40-R49   agreement outcomes (corroboration)
#   R50-R59   context-explained differences (reconciled)
#   R60-R69   flagged as underspecified
#   R70-R79   contradiction
RULE_DESCRIPTIONS: dict[str, str] = {
    "R00": "unrelated: subject mismatch (entity gate rejected)",
    "R01": "unrelated: different property (predicate gate returned 'different')",
    "R02": "underspecified: units are not commensurable",
    "R10": "component_of_total: narrower value is 5%-100% of the broader one",
    "R11": "partial_cover: narrower value below 5% of broader; too small to confirm",
    "R12": "partial_cover: related property, no numeric part-of check applied",
    "R20": "supersedes: modality revision for the same period",
    "R30": "corroboration: two status claims agreeing on the same state",
    "R31": "supersedes: status changed over time (periods orderable)",
    "R32": "contradiction: opposing statuses with no orderable periods",
    "R33": "reconciled: statuses differ but are not mutually exclusive",
    "R40": "corroboration: comparable context and agreeing values",
    "R50": "reconciled: values differ, context accounts for the gap",
    "R51": "reconciled: values agree but claims cover different contexts",
    "R60": "underspecified: values differ, context stated on only one side",
    "R61": "underspecified: percentages of unknown bases (no named rate, no denominator)",
    "R62": "underspecified: unitless table cells with a row label that does not name a rate",
    "R70": "contradiction: matched subject and property, comparable context, values outside tolerance",
    "R99": "underspecified: inconclusive (fell through the cascade)",
}


def rule_description(rule_id: str | None) -> str:
    return RULE_DESCRIPTIONS.get(rule_id or "", "unknown rule")


# --------------------------------------------------------------------------
# field comparisons
# --------------------------------------------------------------------------
# Scope words that mean "no sub-group; the aggregate over all groups". A
# claim carrying one of these is the same measurement as an unstated-scope
# claim from another document. The RBI's spanning-header fix now labels one
# credit-deposit column "Combined" -- that column's figures correspond
# exactly to the IMF's unqualified figures, and forcing that pair into
# underspecified because one side "states" a scope and the other does not
# would be over-conservative in a way that hides a real disagreement.
_AGGREGATE_SCOPES = {"combined", "total", "aggregate", "overall", "all", "all banks"}


def _text_field(a: str | None, b: str | None, label: str) -> tuple[str, str]:
    """same / different / unknown for a free-text context field.

    Both-absent counts as *same*: two equally unqualified claims are comparable.
    Treating it as unknown would make almost every pair ambiguous, since most
    claims state no scope. Only the one-sided case is genuinely unknown --
    with one exception, for the aggregate-scope tokens.
    """
    a_s, b_s = (a or "").strip(), (b or "").strip()
    if not a_s and not b_s:
        return ("same", f"neither claim states a {label}")
    if not a_s or not b_s:
        stated, missing_from = (a_s, "b") if a_s else (b_s, "a")
        if label == "scope" and stated.lower() in _AGGREGATE_SCOPES:
            return ("same", f"one claim states {stated!r} (aggregate over all groups) "
                            f"and the other states no {label}; these describe the "
                            f"same measurement")
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

    # Precision-derived tolerance. Rather than a flat 0.5%, use half a unit of
    # the coarser side's least significant digit, so `8,142 Cr` agrees with
    # `81,415.38 Mn`: the first is written to the nearest whole crore
    # (±0.5 Cr = ±5,000,000), and the tiny gap between the two figures is
    # inside that. Falls back to the flat relative tolerance if either value
    # has no meaningful precision (bare integers, unknown format).
    prec_a = precision_of(a["value"], ua.get("scale", 1.0))
    prec_b = precision_of(b["value"], ub.get("scale", 1.0))
    coarser_precision = None
    if prec_a is not None and prec_b is not None:
        coarser_precision = max(prec_a, prec_b)
        delta["coarser_precision"] = coarser_precision

    if ua["percent"] and ub["percent"]:
        # Rates are compared in percentage points. A relative tolerance would
        # call 4.0% and 2.8% a 30% difference and hide that these are two
        # incompatible reported rates. Precision-aware: `3.5%` and `3.51%`
        # agree because 3.5% is written to the nearest 0.1 (precision ±0.05).
        pp = abs(x - y)
        delta["percentage_point_difference"] = round(pp, 4)
        pp_tolerance = coarser_precision if coarser_precision is not None \
            else CONFIG.percentage_point_tolerance
        pp_tolerance = max(pp_tolerance, CONFIG.percentage_point_tolerance)
        if pp <= pp_tolerance:
            return ("equivalent",
                    f"same rate within its stated precision "
                    f"(±{pp_tolerance:g} pp; {x:g}% vs {y:g}%)", delta)
        return ("differ",
                f"rates differ by {pp:.2f} percentage points ({x:g}% vs {y:g}%)", delta)

    if x == y:
        return ("equal", f"identical after normalisation ({x:g})", delta)
    denom = max(abs(x), abs(y))
    if denom == 0:
        return ("equal", "both values are zero", delta)
    rel = abs(x - y) / denom
    delta["relative_difference"] = round(rel, 6)

    # Precision-based accept: is the absolute gap smaller than the coarser
    # side's rounding half-unit? If so, the two figures agree to the limit of
    # what either document actually says. Kept below the flat relative
    # tolerance so a claim that is coarsely written (e.g. `8,000`) does not
    # get an unreasonably wide tolerance from trailing-zero ambiguity.
    if coarser_precision is not None and abs(x - y) <= coarser_precision:
        return ("equivalent",
                f"values agree within the coarser side's stated precision "
                f"(±{coarser_precision:,.4g}; {x:g} vs {y:g})", delta)
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

    def finish(kind: str, explanation: str, *, rule_id: str, rel_conf: float,
               match_conf: float, value_agreement=None, delta=None,
               escalate: bool = False) -> tuple[Relation, bool]:
        # Overall confidence is bounded by the weaker claim: a shaky extraction
        # must never yield a confident verdict just because two numbers differ.
        weakest = min(a["final_confidence"], b["final_confidence"])
        overall = weakest * match_conf * rel_conf
        # Stamp the rule id into the trace so anyone reading it can go straight
        # from a verdict to the code path that produced it.
        if trace and not trace[-1].startswith("[") and rule_id:
            trace[-1] = f"[{rule_id}] {trace[-1]}"
        return (Relation(
            relation_id=relation_id_for(a["claim_id"], b["claim_id"]),
            claim_a_id=a["claim_id"], claim_b_id=b["claim_id"],
            kind=kind, decided_by="rules", rule_id=rule_id,
            similarity=similarity,
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
                      rule_id="R00", rel_conf=0.9, match_conf=ent_score)

    # -- 2. predicate ------------------------------------------------------
    pred_verdict, pred_score, pred_why = same_predicate(a["predicate"], b["predicate"])
    trace.append(f"predicate gate: {pred_verdict} -- {pred_why}")
    if pred_verdict == "different":
        return finish("unrelated",
                      f"Same subject but different properties, so no comparison is meaningful. {pred_why}",
                      rule_id="R01", rel_conf=0.9, match_conf=pred_score)

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
                      rule_id="R02", rel_conf=0.55, match_conf=match_conf,
                      value_agreement=value_v, delta=delta)

    if pred_verdict == "related":
        # Same family, different measure. A more specific property whose value
        # is smaller than the broader one is a component of it, not a rival
        # claim about it -- reporting that as a contradiction is the classic
        # segments-versus-total error. When the pairwise ratio actually fits
        # a component-of-total pattern (narrower is 5%-100% of broader), the
        # verdict is upgraded from `partial_cover` to `component_of_total`.
        # The stronger label is what the post-pass in pipeline.py works with:
        # it groups these by broader-claim and confirms the siblings sum.
        direction = _more_specific(a["predicate"], b["predicate"])
        if direction != 0 and delta.get("a") is not None and delta.get("b") is not None:
            narrow, broad = ((delta["a"], delta["b"]) if direction == 1
                             else (delta["b"], delta["a"]))
            ratio = abs(narrow) / abs(broad) if broad else 0.0
            if ratio <= 1.02:
                share = ratio * 100
                if 0.05 <= ratio <= 1.02:
                    trace.append(
                        f"decision: the narrower property is {share:.0f}% of the "
                        f"broader one -- a plausible component -> component_of_total")
                    return finish("component_of_total",
                                  f"One claim measures a narrower quantity than the "
                                  f"other ({pred_why}). The narrower figure is "
                                  f"{share:.0f}% of the broader one, which is consistent "
                                  f"with it being a component. Whether the siblings sum "
                                  f"to the broader figure is checked corpus-wide in a "
                                  f"post-pass.",
                                  rule_id="R10", rel_conf=0.75, match_conf=match_conf,
                                  value_agreement=value_v, delta=delta)
                trace.append(
                    f"decision: the narrower property is {share:.0f}% of the broader "
                    f"one -- too small to confirm as a component -> partial_cover")
                return finish("partial_cover",
                              f"One claim measures a narrower quantity than the other "
                              f"({pred_why}). The narrower figure is {share:.0f}% of the "
                              f"broader one; the ratio is too small to confirm it as a "
                              f"component, but it is not a rival measurement either.",
                              rule_id="R11", rel_conf=0.65, match_conf=match_conf,
                              value_agreement=value_v, delta=delta)
        trace.append("decision: related but not the same property -> partial_cover")
        return finish("partial_cover",
                      f"These describe related but distinct properties, so their values "
                      f"are not expected to match. {pred_why}",
                      rule_id="R12", rel_conf=0.6, match_conf=match_conf,
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
                          rule_id="R20", rel_conf=0.8, match_conf=match_conf,
                          value_agreement=value_v, delta=delta)

    # -- 4b. status claims: state changes, not measurements -----------------
    # Two "status" claims about the same entity. The generic value_check has
    # already produced "equal"/"equivalent"/"differ" textually; here we decide
    # whether a difference is a supersession or a real contradiction, using the
    # period ordering rather than the modality ladder used for numerics.
    if (a["predicate"].strip().lower() == "status"
            and b["predicate"].strip().lower() == "status"):
        va, vb = (a["value"] or "").lower(), (b["value"] or "").lower()
        opposed = any(any(o in va for o in group) and any(o in vb for o in group)
                      and not any(o in va and o in vb for o in group)
                      for group in STATUS_OPPOSITES)
        if values_agree:
            trace.append("decision: two status claims agreeing on the same state "
                         "-> corroboration")
            return finish("corroboration",
                          f"Both documents record {a['subject']} as {a['value']!r}. "
                          f"The status agrees.",
                          rule_id="R30", rel_conf=0.85, match_conf=match_conf,
                          value_agreement=value_v, delta=delta)
        if values_differ:
            order = period_precedes(a["ctx_period"], b["ctx_period"])
            if order != 0:
                later, earlier = ((a, b) if order > 0 else (b, a))
                trace.append(f"decision: status changed over time; the later record "
                             f"({later['ctx_period']}) supersedes the earlier "
                             f"({earlier['ctx_period']}) -> supersedes")
                return finish("supersedes",
                              f"{a['subject']} was recorded as {earlier['value']!r} "
                              f"in the earlier document ({earlier['ctx_period']}) and "
                              f"as {later['value']!r} in the later one "
                              f"({later['ctx_period']}). Status changed; this is a "
                              f"revision, not a contradiction.",
                              rule_id="R31", rel_conf=0.85, match_conf=match_conf,
                              value_agreement=value_v, delta=delta)
            if opposed:
                trace.append("decision: opposing statuses with no orderable periods "
                             "-> contradiction")
                return finish("contradiction",
                              f"The documents give {a['subject']} opposing statuses "
                              f"({a['value']!r} vs {b['value']!r}) and no period is "
                              f"stated to say which is later. This looks like a real "
                              f"conflict.",
                              rule_id="R32", rel_conf=0.7, match_conf=match_conf,
                              value_agreement=value_v, delta=delta)
            trace.append("decision: differing but non-opposed statuses -> reconciled")
            return finish("reconciled",
                          f"{a['subject']} is described as {a['value']!r} in one "
                          f"document and {b['value']!r} in the other. The statuses "
                          f"differ but are not mutually exclusive; both may be true.",
                          rule_id="R33", rel_conf=0.6, match_conf=match_conf,
                          value_agreement=value_v, delta=delta)

    if values_agree and not context_differs:
        trace.append("decision: comparable context + agreeing values -> corroboration")
        return finish("corroboration",
                      f"Both documents report the same {a['predicate']} for {a['subject']}: "
                      f"{value_why}. Context is comparable.",
                      rule_id="R40", rel_conf=0.9, match_conf=match_conf,
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
                      rule_id="R50", rel_conf=0.85, match_conf=match_conf,
                      value_agreement=value_v, delta=delta)

    if values_agree and context_differs:
        fields = ", ".join(context_diff)
        trace.append(f"decision: agreeing values but differing {fields} -> reconciled")
        return finish("reconciled",
                      f"The values coincide ({value_why}) but the claims cover different "
                      f"{fields}, so they are compatible rather than corroborating the "
                      f"same measurement.",
                      rule_id="R51", rel_conf=0.6, match_conf=match_conf,
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
                      rule_id="R60", rel_conf=0.5, match_conf=match_conf,
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
                          rule_id="R61", rel_conf=0.45, match_conf=match_conf,
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
                          rule_id="R62", rel_conf=0.45, match_conf=match_conf,
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
                      rule_id="R70", rel_conf=0.85, match_conf=match_conf,
                      value_agreement=value_v, delta=delta)

    trace.append("decision: inconclusive")
    return finish("underspecified",
                  f"Related claims, but the comparison was inconclusive: {value_why}.",
                  rule_id="R99", rel_conf=0.4, match_conf=match_conf,
                  value_agreement=value_v, delta=delta, escalate=True)


# --------------------------------------------------------------------------
# corpus-wide post-pass
# --------------------------------------------------------------------------
def upgrade_components_of_total(relations: list[Relation],
                                claims_by_id: dict[str, dict],
                                *, tolerance: float = 0.05) -> int:
    """Confirm component_of_total pairs when the siblings actually sum.

    Each `component_of_total` relation says "the narrower value is a plausible
    share of the broader one". This post-pass groups relations by the broader
    claim and, when several narrower claims are found under the same broader
    one with matching context, checks whether their values sum to the broader
    value within a tolerance. Confirmed groups get their explanation extended;
    unconfirmed pairs keep the pairwise verdict.

    Deterministic and cheap: it never invokes a model, it does not change the
    verdict of any relation from another kind, and it never converts a
    confirmed pair into an unconfirmed one.

    Returns the number of relations whose explanation was strengthened.
    """
    from collections import defaultdict

    groups: dict[str, list[tuple[Relation, dict, dict, bool]]] = defaultdict(list)
    for r in relations:
        if r.kind != "component_of_total":
            continue
        a = claims_by_id.get(r.claim_a_id)
        b = claims_by_id.get(r.claim_b_id)
        if not a or not b:
            continue
        va, vb = a.get("value_num"), b.get("value_num")
        if va is None or vb is None:
            continue
        # Which side is broader? "broader" = larger absolute value AND fewer
        # content tokens in the predicate.
        a_narrower = (abs(va) < abs(vb)
                      and len(content_tokens(a["predicate"]))
                      > len(content_tokens(b["predicate"])))
        b_narrower = (abs(vb) < abs(va)
                      and len(content_tokens(b["predicate"]))
                      > len(content_tokens(a["predicate"])))
        if a_narrower:
            narrower, broader = a, b
            reversed_ = False
        elif b_narrower:
            narrower, broader = b, a
            reversed_ = True
        else:
            continue
        # Group by broader claim id + period so segments of one year are not
        # summed against another year's total.
        key = f"{broader['claim_id']}|{narrower.get('ctx_period') or ''}"
        groups[key].append((r, narrower, broader, reversed_))

    strengthened = 0
    for key, members in groups.items():
        if len(members) < 2:
            continue
        broader = members[0][2]
        broader_value = broader.get("value_num")
        if not broader_value:
            continue
        # De-duplicate on the narrower predicate: the same segment reported
        # via two different narrower claims should not be double-counted.
        seen: dict[str, dict] = {}
        for _, narrower, _, _ in members:
            pred = " ".join(sorted(content_tokens(narrower["predicate"])))
            existing = seen.get(pred)
            if existing is None \
                    or abs(narrower["value_num"]) > abs(existing["value_num"]):
                seen[pred] = narrower
        if len(seen) < 2:
            continue
        total = sum(abs(n["value_num"]) for n in seen.values())
        if not broader_value:
            continue
        ratio = total / abs(broader_value)
        if abs(1.0 - ratio) <= tolerance:
            names = ", ".join(sorted(pred for pred in seen))
            note = (f"[confirmed] siblings ({names}) sum to "
                    f"{total:,.4g}, within {tolerance:.0%} of the broader "
                    f"value {abs(broader_value):,.4g}. "
                    f"This is a component-of-total decomposition.")
            for r, _, _, _ in members:
                if "[confirmed]" not in r.explanation:
                    r.explanation = f"{r.explanation} {note}"
                    r.reasoning_trace.append(
                        f"post-pass: siblings sum to {total:,.4g} against a broader "
                        f"value of {abs(broader_value):,.4g} (ratio {ratio:.3f}); "
                        f"component-of-total decomposition confirmed")
                    r.relationship_confidence = round(
                        min(1.0, r.relationship_confidence + 0.10), 4)
                    r.confidence = round(min(1.0, r.confidence + 0.05), 4)
                    strengthened += 1
    return strengthened


# --------------------------------------------------------------------------
# Counterfactuals: what if one qualifier were missing?
# --------------------------------------------------------------------------
#
# The strongest thing a reasoning system can show, short of the answer itself,
# is what the answer WOULD be under a hypothetical. Ablating one qualifier at
# a time and re-running the cascade produces exactly this: "if the basis were
# missing on either side, this would land in R70 (contradiction) instead of
# R50 (reconciled)". A reader can see which qualifier is doing the work.
#
# No new logic. Same cascade, run once per ablation. That is deliberate --
# a counterfactual explanation is only worth reading if it comes from the
# same code that decides live verdicts.

# Qualifier fields whose ablation is worth showing. Not the same as
# CONTEXT_FIELDS: unit ablation is done through ctx_unit and unit both, since
# the unit is stored in two places.
_ABLATABLE_FIELDS = ("ctx_period", "ctx_unit", "ctx_scope", "ctx_basis",
                     "ctx_denominator", "ctx_geography", "ctx_as_of")

_ABLATABLE_LABELS = {
    "ctx_period": "period", "ctx_unit": "unit", "ctx_scope": "scope",
    "ctx_basis": "basis", "ctx_denominator": "denominator",
    "ctx_geography": "geography", "ctx_as_of": "as-of date",
}


def _ablated(row: dict, field: str) -> dict:
    """A copy of the claim row with one qualifier removed. Also blanks the
    top-level `unit` when `ctx_unit` is ablated, because the unit lives in
    both places and the comparator reads either."""
    clone = dict(row)
    clone[field] = None
    if field == "ctx_unit":
        clone["unit"] = None
    return clone


def counterfactuals(a: dict, b: dict, *, similarity: float = 0.0,
                    subject_similarity: float | None = None,
                    predicate_similarity: float | None = None) -> list[dict]:
    """Re-run the cascade with one qualifier ablated at a time.

    For each qualifier stated on either side, drop it and re-decide. The
    result lists only the ablations whose verdict differs from the live one:
    those are the qualifiers actually doing work on this pair.
    """
    live, _ = compare_claims(a, b, similarity=similarity,
                             subject_similarity=subject_similarity,
                             predicate_similarity=predicate_similarity)
    live_key = (live.kind, live.rule_id)

    results: list[dict] = []
    for field in _ABLATABLE_FIELDS:
        # Only worth ablating a field one side (or both) stated.
        if not (a.get(field) or b.get(field)):
            continue
        # Ablate on the side(s) that stated it.
        alt_a = _ablated(a, field) if a.get(field) else a
        alt_b = _ablated(b, field) if b.get(field) else b
        alt, _ = compare_claims(alt_a, alt_b, similarity=similarity,
                                subject_similarity=subject_similarity,
                                predicate_similarity=predicate_similarity)
        if (alt.kind, alt.rule_id) == live_key:
            continue
        results.append({
            "ablated_field": _ABLATABLE_LABELS.get(field, field),
            "removed_from": [
                side for side, row in (("A", a), ("B", b)) if row.get(field)
            ],
            "would_be_kind": alt.kind,
            "would_be_rule_id": alt.rule_id,
            "explanation": alt.explanation,
            "confidence": alt.confidence,
        })
    return results
