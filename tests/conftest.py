"""Shared helpers.

Every test here runs offline: no API key, no network. That is the point of the
architecture, so the test suite has to prove it.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

REPO = Path(__file__).resolve().parent.parent
STARTER = REPO / "starter-datasets"


def claim_row(**kw) -> dict:
    """A stored-claim dict of the shape compare_claims consumes."""
    row = dict(
        claim_id=kw.pop("cid", "x"),
        doc_id=kw.pop("doc", "docA.pdf"),
        subject="Acme Corp",
        predicate="revenue",
        value="100",
        value_num=None,
        unit=None,
        ctx_period=None, ctx_unit=None, ctx_scope=None, ctx_basis=None,
        ctx_geography=None, ctx_as_of=None, ctx_denominator=None, ctx_other=None,
        asserted_by=None, modality="reported", origin="sentence",
        source_document="docA.pdf", span_page=1, span_text="evidence",
        matched_text="evidence", grounding_page=1,
        final_confidence=0.9, extraction_confidence=0.9, grounding_confidence=1.0,
        quarantined=False, needs_review=False,
    )
    row.update(kw)
    if row["value_num"] is None:
        from fkl.normalize import parse_value, unit_signature
        parsed = parse_value(row["value"])
        sig = unit_signature(row["unit"] or row["ctx_unit"], parsed)
        if parsed.base_number is not None:
            scale = parsed.scale if parsed.scale != 1.0 else sig["scale"]
            row["value_num"] = parsed.base_number * scale
    return row


def verdict(a: dict, b: dict) -> str:
    from fkl.compare import compare_claims
    relation, _ = compare_claims(a, b, similarity=1.0)
    return relation.kind


def relation_of(a: dict, b: dict):
    from fkl.compare import compare_claims
    return compare_claims(a, b, similarity=1.0)[0]
