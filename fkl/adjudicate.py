"""Optional LLM adjudication.

Isolated in its own module so the rest of the pipeline never imports a
provider. It is called only for pairs the deterministic rules explicitly marked
ambiguous, and only when FKL_ENABLE_LLM_FALLBACK is on. If a provider is not
configured, the rule-based verdict stands unchanged.

The model is never allowed to overturn strong deterministic evidence: it is
consulted only where the rules already concluded "underspecified", and its
verdict is recorded as such in the trace.
"""
from __future__ import annotations

import logging

from .config import CONFIG
from .models import Relation

log = logging.getLogger("fkl.adjudicate")

VERDICTS = ("corroboration", "contradiction", "reconciled", "supersedes",
            "partial_cover", "underspecified", "unrelated")

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": list(VERDICTS)},
        "explanation": {"type": "string"},
        "reconciling_factor": {"type": ["string", "null"]},
        "confidence": {"type": "number"},
    },
    "required": ["kind", "explanation", "reconciling_factor", "confidence"],
    "additionalProperties": False,
}

JUDGE_SYSTEM = """\
You adjudicate whether two extracted claims agree, disagree, or are compatible \
once context is taken into account. Decide using ONLY the two claims and their \
quoted evidence. Do not use outside knowledge.

A deterministic comparison already ran and could not settle this pair -- \
typically because one document states a qualifier the other omits. Choose one:

- "corroboration": same thing asserted about the same entity under the same \
  conditions, even if worded or scaled differently.
- "contradiction": incompatible assertions under conditions that really are \
  the same. Both cannot be true.
- "reconciled": values differ but something in the context or evidence \
  explains it -- different period, scope, unit, basis, or an estimate versus \
  an actual. Name that factor in reconciling_factor.
- "supersedes": the same measurement at two stages, one revising the other.
- "partial_cover": one claim measures only part of what the other measures.
- "underspecified": the evidence is genuinely insufficient. Prefer this over \
  guessing.
- "unrelated": they are not about the same property after all.

Be conservative. A missing qualifier is not evidence of disagreement. Only \
call something a contradiction when the quoted evidence really does put the \
two claims in conflict."""

JUDGE_TEMPLATE = """CLAIM A (from {a_doc}, page {a_page})
  subject   : {a_subject}
  predicate : {a_predicate}
  value     : {a_value}
  period    : {a_period}      unit : {a_unit}
  scope     : {a_scope}       basis: {a_basis}
  modality  : {a_modality}
  evidence  : "{a_evidence}"

CLAIM B (from {b_doc}, page {b_page})
  subject   : {b_subject}
  predicate : {b_predicate}
  value     : {b_value}
  period    : {b_period}      unit : {b_unit}
  scope     : {b_scope}       basis: {b_basis}
  modality  : {b_modality}
  evidence  : "{b_evidence}"

What the deterministic comparison already established:
{trace}

Decide the relationship."""


def adjudicate(a: dict, b: dict, relation: Relation, client) -> Relation:
    """Ask the model to settle one ambiguous pair. Falls back silently to rules."""
    user = JUDGE_TEMPLATE.format(
        a_doc=a["source_document"], a_page=a["grounding_page"] or a["span_page"],
        a_subject=a["subject"], a_predicate=a["predicate"], a_value=a["value"],
        a_period=a["ctx_period"], a_unit=a["ctx_unit"], a_scope=a["ctx_scope"],
        a_basis=a.get("ctx_basis"), a_modality=a.get("modality"),
        a_evidence=(a["matched_text"] or a["span_text"])[:600],
        b_doc=b["source_document"], b_page=b["grounding_page"] or b["span_page"],
        b_subject=b["subject"], b_predicate=b["predicate"], b_value=b["value"],
        b_period=b["ctx_period"], b_unit=b["ctx_unit"], b_scope=b["ctx_scope"],
        b_basis=b.get("ctx_basis"), b_modality=b.get("modality"),
        b_evidence=(b["matched_text"] or b["span_text"])[:600],
        trace="\n".join(f"  - {t}" for t in relation.reasoning_trace),
    )
    try:
        res = client.complete_json(system=JUDGE_SYSTEM, user=user,
                                   schema=JUDGE_SCHEMA, schema_name="relation_verdict",
                                   max_tokens=2000)
        data = res.data
    except Exception as e:
        relation.reasoning_trace.append(
            f"LLM adjudication failed ({type(e).__name__}); deterministic verdict kept")
        return relation

    kind = data.get("kind")
    if kind not in VERDICTS:
        relation.reasoning_trace.append(
            "LLM adjudication returned an unusable verdict; deterministic verdict kept")
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
    relation.relationship_confidence = round(max(0.0, min(1.0, judged)), 4)
    relation.confidence = round(
        min(a["final_confidence"], b["final_confidence"])
        * relation.match_confidence * relation.relationship_confidence, 4)
    relation.reasoning_trace.append(
        f"escalated to {CONFIG.judge_model} (rules were inconclusive): verdict {kind!r}"
        + (f", reconciling factor: {factor}" if factor else ""))
    return relation
