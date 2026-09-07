"""Claim and relationship schemas.

Three layers, kept separate on purpose so it is always obvious which field came
from where:

* ``Claim``          -- what an extractor asserted (deterministic rules or, optionally, an LLM)
* ``GroundedClaim``  -- that claim plus what the grounding verifier proved about it
* ``Relation``       -- a verdict about two grounded claims, with the rules that fired

Confidence is deliberately *not* a single number. Extraction, grounding,
matching and relationship confidence answer different questions, and collapsing
them hides the case this system most needs to avoid: a weakly-extracted claim
becoming a high-confidence contradiction just because two numbers differ.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

# --------------------------------------------------------------------------
# Context
# --------------------------------------------------------------------------
class ClaimContext(BaseModel):
    """Qualifiers that decide whether two claims are comparable at all.

    A null field means "the document did not state this", which is NOT the same
    as the two claims disagreeing. Missing context can never, on its own, create
    a contradiction.
    """

    period: Optional[str] = Field(default=None, description="Time period as written.")
    unit: Optional[str] = Field(default=None, description="Unit/denomination as written.")
    scope: Optional[str] = Field(default=None, description="Consolidated/standalone, segment, subset.")
    basis: Optional[str] = Field(default=None, description="Reporting or accounting basis.")
    geography: Optional[str] = Field(default=None, description="Country/region the value covers.")
    as_of: Optional[str] = Field(default=None, description="Point-in-time date the value is stated as of.")
    denominator: Optional[str] = Field(default=None, description="What a ratio/share is measured against.")
    other_qualifiers: Optional[str] = Field(default=None, description="Anything else that changes reading.")

    def stated(self) -> dict[str, str]:
        return {k: v for k, v in self.model_dump().items() if v}


# --------------------------------------------------------------------------
# Evidence
# --------------------------------------------------------------------------
class SourceSpan(BaseModel):
    """Where a claim came from. bbox/char_span are filled by grounding."""

    page: int = Field(description="1-based PDF page index.")
    text: str = Field(description="Verbatim text asserting the claim.")
    char_span: Optional[tuple[int, int]] = Field(
        default=None, description="Character range within the page's extracted text.")
    bbox: Optional[tuple[float, float, float, float]] = Field(
        default=None, description="Bounding box on the page, when recoverable.")


Modality = Literal["reported", "estimate", "projection", "revised", "target", "provisional"]
Origin = Literal["sentence", "table", "llm"]


# --------------------------------------------------------------------------
# Claim
# --------------------------------------------------------------------------
class Claim(BaseModel):
    subject: str = Field(description="Entity the claim is about.")
    predicate: str = Field(description="Property asserted about the subject.")
    value: str = Field(description="Value exactly as written.")

    # Normalised numeric view, filled during normalisation. Non-numeric
    # (semantic) claims keep value_num=None and are compared textually.
    value_num: Optional[float] = Field(default=None, description="Scale-normalised numeric value.")
    unit: Optional[str] = Field(default=None, description="Canonical unit, e.g. 'INR million', 'percent'.")

    context: ClaimContext = Field(default_factory=ClaimContext)

    asserted_by: Optional[str] = Field(
        default=None, description="Who the document attributes the claim to, if stated.")
    modality: Modality = Field(default="reported")

    source_document: str
    source_span: SourceSpan
    origin: Origin = Field(default="sentence", description="Which extraction path produced this.")
    extraction_rule: Optional[str] = Field(
        default=None, description="Name of the rule that fired -- keeps extraction auditable.")
    confidence: float = Field(ge=0.0, le=1.0, default=0.5,
                              description="Extraction confidence only.")


class ClaimBatch(BaseModel):
    """Envelope used by the optional LLM extractor."""
    claims: list[Claim]


# --------------------------------------------------------------------------
# Grounding
# --------------------------------------------------------------------------
class Grounding(BaseModel):
    located: bool
    score: float = Field(ge=0.0, le=1.0)
    page: Optional[int] = None
    page_label: Optional[str] = None
    matched_text: Optional[str] = None
    char_start: Optional[int] = None
    char_end: Optional[int] = None
    bbox: Optional[tuple[float, float, float, float]] = None
    page_shift: Optional[int] = None
    value_in_span: Optional[bool] = None
    note: Optional[str] = None


class Confidence(BaseModel):
    """Kept separate rather than multiplied into one opaque score."""
    extraction: float = Field(ge=0.0, le=1.0)
    grounding: float = Field(ge=0.0, le=1.0)

    @property
    def combined(self) -> float:
        return round(self.extraction * self.grounding, 4)


class GroundedClaim(BaseModel):
    claim_id: str
    doc_id: str
    claim: Claim
    grounding: Grounding
    confidence: Confidence
    # Quarantined claims are stored and visible but never enter comparison.
    quarantined: bool = False
    needs_review: bool = False
    review_reasons: list[str] = Field(default_factory=list)

    @property
    def final_confidence(self) -> float:
        return self.confidence.combined


# --------------------------------------------------------------------------
# Relationships
# --------------------------------------------------------------------------
RelationKind = Literal[
    "corroboration",   # same claim, comparable context, equivalent values
    "contradiction",   # same claim, comparable context, materially different values
    "reconciled",      # values differ, but a context difference explains it
    "supersedes",      # same measurement restated later; one revises the other
    "partial_cover",   # one claim covers only part of what the other measures
    "underspecified",  # related, but the documents omit context needed to judge
    "unrelated",       # matched by similarity but not actually the same property
]

Decider = Literal["rules", "llm"]


class Relation(BaseModel):
    relation_id: str
    claim_a_id: str
    claim_b_id: str
    kind: RelationKind
    decided_by: Decider = "rules"

    similarity: float = Field(description="Combined (subject, predicate) embedding cosine.")
    subject_similarity: Optional[float] = None
    predicate_similarity: Optional[float] = None

    # Distinct from claim confidence: how sure we are these describe the same
    # measurement, versus how sure we are of the verdict about them.
    match_confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    relationship_confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    confidence: float = Field(ge=0.0, le=1.0, default=0.0,
                              description="Overall, bounded by the weaker claim's confidence.")

    explanation: str
    reasoning_trace: list[str] = Field(
        default_factory=list, description="The rules that actually fired, in order.")
    context_diff: dict[str, list[Optional[str]]] = Field(default_factory=dict)
    value_agreement: Optional[str] = None
    value_delta: Optional[dict] = Field(
        default=None,
        description="Absolute and relative difference, plus percentage points for rates.")
