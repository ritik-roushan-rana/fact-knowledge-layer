"""Schemas.

Two layers on purpose:

* ``Claim`` is exactly the shape the assignment specifies and exactly what the
  LLM is asked to produce. It is the *proposal*.
* ``GroundedClaim`` wraps a proposal with everything the pipeline computed about
  it (where the quote really is, how well it matched, final confidence). Keeping
  them separate makes it obvious which fields came from a model and which came
  from code -- the reasoning stays inspectable instead of hiding in one blob.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------
# Layer 1: what the model proposes
# --------------------------------------------------------------------------
class ClaimContext(BaseModel):
    """Qualifiers that decide whether two same-subject claims are comparable.

    Always present (fields may be null). This object is the whole reason a
    "different value" can be a reconciliation rather than a contradiction.
    """

    period: Optional[str] = Field(
        description="Time period the value refers to, verbatim from the document "
        "(e.g. a fiscal year, quarter, calendar year, as-of date). Null if none stated."
    )
    unit: Optional[str] = Field(
        description="Unit or denomination of the value (currency, scale such as "
        "million/crore, percent, count, index). Null if the value is not numeric "
        "or no unit is stated."
    )
    scope: Optional[str] = Field(
        description="What entity or slice the value covers (consolidated vs "
        "standalone, a segment, a region, a subset of a population). Null if not stated."
    )
    other_qualifiers: Optional[str] = Field(
        description="Any remaining qualifier that changes how the value should be "
        "read: basis of measurement, estimate vs actual, projection, restated, "
        "adjusted, provisional, source attribution. Null if none."
    )


class SourceSpan(BaseModel):
    page: int = Field(description="1-based page number of the document this text is on.")
    text: str = Field(
        description="Exact contiguous text copied verbatim from that page which "
        "states the claim. Must appear in the document character for character."
    )


class Claim(BaseModel):
    """A single atomic assertion proposed by the extractor."""

    subject: str = Field(description="The entity the claim is about.")
    predicate: str = Field(
        description="The property or relation being asserted about the subject, "
        "as a short normalized phrase."
    )
    value: str = Field(description="The asserted value, as written.")
    context: ClaimContext
    source_document: str = Field(description="Filename of the source document.")
    source_span: SourceSpan
    confidence: float = Field(
        ge=0.0, le=1.0,
        description="How confident the extractor is that this claim is stated by "
        "the quoted span, 0-1.",
    )


class ClaimBatch(BaseModel):
    """Structured-output envelope for one extraction call."""

    claims: list[Claim]


# --------------------------------------------------------------------------
# Layer 2: what the pipeline verified
# --------------------------------------------------------------------------
class Grounding(BaseModel):
    """Result of programmatically locating the proposed quote in the real text."""

    located: bool
    score: float = Field(ge=0.0, le=1.0, description="Fuzzy match quality, 0-1.")
    page: Optional[int] = Field(default=None, description="Page the quote was actually found on.")
    page_label: Optional[str] = Field(default=None, description="Printed page label, if the PDF has one.")
    matched_text: Optional[str] = Field(
        default=None, description="The verbatim document text the quote resolved to."
    )
    char_start: Optional[int] = None
    char_end: Optional[int] = None
    page_shift: Optional[int] = Field(
        default=None, description="Found page minus claimed page. Non-zero means the model misreported the page."
    )
    value_in_span: Optional[bool] = Field(
        default=None,
        description="Whether the numbers in the claim's value actually occur in the "
        "matched text. None when the value contains no numbers.",
    )
    note: Optional[str] = None


class GroundedClaim(BaseModel):
    claim_id: str
    doc_id: str
    claim: Claim
    grounding: Grounding
    final_confidence: float = Field(
        ge=0.0, le=1.0,
        description="extraction confidence x grounding score, penalised if the "
        "claim's numbers are absent from the located text.",
    )
    needs_review: bool
    review_reasons: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Cross-document relationships
# --------------------------------------------------------------------------
RelationKind = Literal[
    "corroboration",       # same thing said, same context, agreeing values
    "contradiction",       # same thing, same context, values that cannot both hold
    "reconciled",          # values differ, but a context difference explains it
    "unresolved",          # related, but neither values nor context settle it
]

Decider = Literal["rules", "llm"]


class Relation(BaseModel):
    relation_id: str
    claim_a_id: str
    claim_b_id: str
    kind: RelationKind
    # How the verdict was reached -- deterministic comparison or escalated judgement.
    decided_by: Decider
    similarity: float = Field(description="Embedding cosine similarity of (subject, predicate).")
    confidence: float = Field(ge=0.0, le=1.0)
    explanation: str = Field(description="Human-readable reason for this verdict.")
    reasoning_trace: list[str] = Field(
        default_factory=list,
        description="Ordered record of the checks that ran and what each concluded.",
    )
    context_diff: dict[str, list[Optional[str]]] = Field(
        default_factory=dict,
        description="Context fields that differ, as field -> [a_value, b_value].",
    )
    value_agreement: Optional[str] = Field(
        default=None, description="How the two values compared: equal / equivalent / differ / incomparable."
    )
