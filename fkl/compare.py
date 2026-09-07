"""Comparing matched claims.

The design rule here is that the *reasoning must stay visible*. Every verdict
carries an ordered trace of the checks that produced it, so a reviewer can see
why two claims were called a contradiction rather than being asked to trust a
model's say-so.

Deterministic checks run first and settle most pairs:

    values agree   + comparable context   -> corroboration
    values differ  + comparable context   -> contradiction
    values differ  + context differs      -> reconciled (the context explains it)

Only pairs the rules genuinely cannot settle -- typically where one claim omits
a qualifier the other states -- are escalated to an LLM, which receives both
evidence spans and must justify its verdict.
"""
from __future__ import annotations

import hashlib
import logging

from rapidfuzz import fuzz

from .config import CONFIG
from .llm import LLMClient
from .models import Relation
from .normalize import compare_periods, compare_values, parse_value, unit_signature

log = logging.getLogger("fkl.compare")

# Below this, two scope strings describe different slices of the world.
_SCOPE_SAME = 0.80
_SCOPE_DIFFERENT = 0.45


def relation_id_for(a_id: str, b_id: str) -> str:
    lo, hi = sorted([a_id, b_id])
    return hashlib.sha256(f"{lo}|{hi}".encode()).hexdigest()[:20]


def _text_verdict(a: str | None, b: str | None, label: str) -> tuple[str, str]:
    """same / different / unknown for a free-text context field.

    Note the asymmetry: if NEITHER document qualifies the claim, they are
    equally unqualified and therefore comparable -- treating that as ambiguous
    would escalate almost every pair, since most claims carry no scope. It is
    the one-sided case (one document qualifies, the other does not) that is
    genuinely ambiguous.
    """
    a_s, b_s = (a or "").strip(), (b or "").strip()
    if not a_s and not b_s:
        return ("same", f"neither claim states a {label}; equally unqualified")
    if not a_s or not b_s:
        return ("unknown", f"only one claim states a {label} ({(a_s or b_s)!r})")
    score = fuzz.token_set_ratio(a_s.lower(), b_s.lower()) / 100.0
    if score >= _SCOPE_SAME:
        return ("same", f"same {label} ({a_s!r} ~ {b_s!r})")
    if score <= _SCOPE_DIFFERENT:
        return ("different", f"different {label} ({a_s!r} vs {b_s!r})")
    return ("unknown", f"{label} partially overlaps ({a_s!r} vs {b_s!r})")


def _unit_verdict(a: dict, b: dict) -> tuple[str, str]:
    """Units matter in two different ways.

    A scale difference (crore vs billion) is NOT a context difference: the value
    comparison normalises it away. A currency or kind difference makes the two
    values genuinely incomparable.
    """
    if a["currency"] and b["currency"] and a["currency"] != b["currency"]:
        return ("incomparable", f"different currencies ({a['currency']} vs {b['currency']})")
    if a["percent"] != b["percent"]:
        return ("incomparable", "one value is a rate and the other is an absolute quantity")
    if a["scale"] != b["scale"]:
        return ("same", f"different scale ({a['raw']!r} vs {b['raw']!r}) -- normalised before comparing")
    return ("same", f"comparable units ({a['raw']!r} vs {b['raw']!r})")


def compare_claims(a: dict, b: dict, similarity: float) -> tuple[Relation, bool]:
    """Run the deterministic comparison.

    Returns (relation, needs_escalation). When needs_escalation is True the
    verdict is provisional and the caller may ask an LLM to adjudicate.
    """
    trace: list[str] = []
    context_diff: dict[str, list[str | None]] = {}

    subj_sim = fuzz.token_set_ratio(a["subject"].lower(), b["subject"].lower()) / 100.0
    pred_sim = fuzz.token_set_ratio(a["predicate"].lower(), b["predicate"].lower()) / 100.0
    trace.append(
        f"matched on embedding similarity of (subject, predicate) = {similarity:.3f}; "
        f"subject string similarity {subj_sim:.2f}, predicate string similarity {pred_sim:.2f}"
    )

    # --- context ---------------------------------------------------------
    period_v, period_why = compare_periods(a["ctx_period"], b["ctx_period"])
    trace.append(f"period: {period_v} -- {period_why}")
    if period_v == "different":
        context_diff["period"] = [a["ctx_period"], b["ctx_period"]]

    ua = unit_signature(a["ctx_unit"], parse_value(a["value"]))
    ub = unit_signature(b["ctx_unit"], parse_value(b["value"]))
    unit_v, unit_why = _unit_verdict(ua, ub)
    trace.append(f"unit: {unit_v} -- {unit_why}")
    if unit_v == "incomparable":
        context_diff["unit"] = [a["ctx_unit"], b["ctx_unit"]]

    scope_v, scope_why = _text_verdict(a["ctx_scope"], b["ctx_scope"], "scope")
    trace.append(f"scope: {scope_v} -- {scope_why}")
    if scope_v == "different":
        context_diff["scope"] = [a["ctx_scope"], b["ctx_scope"]]

    other_v, other_why = _text_verdict(a["ctx_other"], b["ctx_other"], "qualifier")
    trace.append(f"qualifiers: {other_v} -- {other_why}")
    if other_v == "different":
        context_diff["other_qualifiers"] = [a["ctx_other"], b["ctx_other"]]

    # --- value -----------------------------------------------------------
    value_v, value_why, _detail = compare_values(
        a["value"], b["value"], a_scale=ua["scale"], b_scale=ub["scale"]
    )
    trace.append(f"value: {value_v} -- {value_why}")

    # --- decide ----------------------------------------------------------
    context_differs = "different" in (period_v, scope_v, other_v)
    context_unknown = "unknown" in (period_v, scope_v, other_v)
    values_agree = value_v in ("equal", "equivalent")
    values_differ = value_v == "differ"

    escalate = False
    confidence = min(a["final_confidence"], b["final_confidence"]) * similarity

    if unit_v == "incomparable":
        kind = "reconciled"
        explanation = (
            f"Not directly comparable: {unit_why}. The two figures measure the same "
            f"property in different terms, so the difference is explained by units, "
            f"not by a disagreement."
        )
        trace.append("decision: units make the values incomparable -> reconciled by unit")

    elif values_agree and not context_differs:
        kind = "corroboration"
        explanation = (
            f"Both documents report the same {a['predicate']} for {a['subject']}: "
            f"{value_why}. Context is compatible ({period_why.split(' -- ')[0]})."
        )
        trace.append("decision: comparable context + agreeing values -> corroboration")

    elif values_differ and context_differs:
        fields = ", ".join(context_diff)
        kind = "reconciled"
        explanation = (
            f"The values differ ({value_why}), but so does the context: {fields}. "
            + "; ".join(w for v, w in [(period_v, period_why), (scope_v, scope_why),
                                       (other_v, other_why)] if v == "different")
            + ". The two claims measure different things, so this is not a conflict."
        )
        trace.append(f"decision: differing values explained by differing {fields} -> reconciled")

    elif values_agree and context_differs:
        fields = ", ".join(context_diff)
        kind = "reconciled"
        explanation = (
            f"The values coincide ({value_why}) but the claims describe different "
            f"slices ({fields}), so they are compatible rather than corroborating "
            f"the same measurement."
        )
        trace.append(f"decision: agreeing values but differing {fields} -> reconciled")

    elif values_differ and not context_differs and not context_unknown:
        kind = "contradiction"
        explanation = (
            f"Both documents state {a['predicate']} for {a['subject']} under the same "
            f"context, but disagree: {value_why}. No period, scope, unit or qualifier "
            f"difference accounts for the gap."
        )
        trace.append("decision: same context + differing values -> contradiction")

    elif values_differ and context_unknown:
        # The interesting hard case: the values disagree and the documents did
        # not state enough context to know whether they should.
        kind = "unresolved"
        explanation = (
            f"The values differ ({value_why}) but the context is incompletely stated, "
            f"so the rules cannot tell a contradiction from a context difference."
        )
        trace.append("decision: differing values + incomplete context -> ambiguous, escalating")
        escalate = True

    else:
        kind = "unresolved"
        explanation = f"Related claims, but the comparison was inconclusive: {value_why}."
        trace.append("decision: inconclusive")
        escalate = value_v == "incomparable"

    return (
        Relation(
            relation_id=relation_id_for(a["claim_id"], b["claim_id"]),
            claim_a_id=a["claim_id"], claim_b_id=b["claim_id"],
            kind=kind, decided_by="rules", similarity=similarity,
            confidence=round(max(0.0, min(1.0, confidence)), 4),
            explanation=explanation, reasoning_trace=trace,
            context_diff=context_diff, value_agreement=value_v,
        ),
        escalate,
    )


# --------------------------------------------------------------------------
# LLM adjudication -- used only where the rules genuinely could not decide
# --------------------------------------------------------------------------
JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string",
                 "enum": ["corroboration", "contradiction", "reconciled", "unresolved"]},
        "explanation": {"type": "string"},
        "reconciling_factor": {"type": ["string", "null"]},
        "confidence": {"type": "number"},
    },
    "required": ["kind", "explanation", "reconciling_factor", "confidence"],
    "additionalProperties": False,
}

JUDGE_SYSTEM = """\
You adjudicate whether two extracted claims from different documents agree, \
disagree, or are compatible once their context is taken into account. You know \
nothing about the subject matter beyond what the two claims and their quoted \
evidence say, and you must not use outside knowledge to decide.

A deterministic comparison already ran and could not settle this pair, usually \
because one claim omits a qualifier the other states. Decide using ONLY the \
claims and their evidence.

Choose exactly one:
- "corroboration": the claims assert the same thing about the same entity under \
  the same conditions, even if worded or scaled differently.
- "contradiction": they assert incompatible things about the same entity under \
  conditions that appear to be the same. Both cannot be true.
- "reconciled": the values differ, but something in the context or evidence \
  explains why -- a different time period, a different scope or population, a \
  different unit or basis of measurement, an estimate versus an actual, or a \
  revision. Name that factor in reconciling_factor.
- "unresolved": the evidence is genuinely insufficient to choose. Use this \
  rather than guessing.

Be conservative: only call something a contradiction when the evidence really \
does put the two claims in conflict. If the quoted evidence suggests the two \
figures are measuring different things, that is "reconciled", not \
"contradiction". Quote or point to the specific wording that drove your \
decision in the explanation."""

JUDGE_TEMPLATE = """CLAIM A (from {a_doc}, page {a_page})
  subject   : {a_subject}
  predicate : {a_predicate}
  value     : {a_value}
  period    : {a_period}
  unit      : {a_unit}
  scope     : {a_scope}
  qualifiers: {a_other}
  evidence  : "{a_evidence}"

CLAIM B (from {b_doc}, page {b_page})
  subject   : {b_subject}
  predicate : {b_predicate}
  value     : {b_value}
  period    : {b_period}
  unit      : {b_unit}
  scope     : {b_scope}
  qualifiers: {b_other}
  evidence  : "{b_evidence}"

What the deterministic comparison already established:
{trace}

Decide the relationship."""


def adjudicate(a: dict, b: dict, relation: Relation, client: LLMClient) -> Relation:
    """Ask the model to settle a pair the rules could not, given both evidence spans."""
    user = JUDGE_TEMPLATE.format(
        a_doc=a["source_document"], a_page=a["grounding_page"] or a["span_page"],
        a_subject=a["subject"], a_predicate=a["predicate"], a_value=a["value"],
        a_period=a["ctx_period"], a_unit=a["ctx_unit"], a_scope=a["ctx_scope"],
        a_other=a["ctx_other"], a_evidence=(a["matched_text"] or a["span_text"])[:600],
        b_doc=b["source_document"], b_page=b["grounding_page"] or b["span_page"],
        b_subject=b["subject"], b_predicate=b["predicate"], b_value=b["value"],
        b_period=b["ctx_period"], b_unit=b["ctx_unit"], b_scope=b["ctx_scope"],
        b_other=b["ctx_other"], b_evidence=(b["matched_text"] or b["span_text"])[:600],
        trace="\n".join(f"  - {t}" for t in relation.reasoning_trace),
    )
    try:
        res = client.complete_json(system=JUDGE_SYSTEM, user=user,
                                   schema=JUDGE_SCHEMA, schema_name="relation_verdict",
                                   max_tokens=2000)
        data = res.data
    except Exception as e:
        relation.reasoning_trace.append(f"escalation failed ({type(e).__name__}: {e}); "
                                        f"keeping the rule-based verdict")
        return relation

    kind = data.get("kind")
    if kind not in ("corroboration", "contradiction", "reconciled", "unresolved"):
        relation.reasoning_trace.append("escalation returned an unusable verdict; keeping rules")
        return relation

    factor = data.get("reconciling_factor")
    relation.kind = kind
    relation.decided_by = "llm"
    relation.explanation = data.get("explanation") or relation.explanation
    if factor:
        relation.explanation += f" (reconciling factor: {factor})"
    try:
        judged = float(data.get("confidence", 0.5))
    except (TypeError, ValueError):
        judged = 0.5
    # The adjudicated confidence is still bounded by how much we trust the claims.
    relation.confidence = round(
        max(0.0, min(1.0, judged * min(a["final_confidence"], b["final_confidence"]))), 4
    )
    relation.reasoning_trace.append(
        f"escalated to {CONFIG.judge_model}: verdict '{kind}'"
        + (f", reconciling factor: {factor}" if factor else "")
    )
    return relation
