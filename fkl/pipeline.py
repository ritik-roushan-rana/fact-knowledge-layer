"""Ingest orchestration: PDF -> chunks -> proposed claims -> grounded claims -> store."""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .compare import adjudicate, compare_claims
from .config import CONFIG
from .embed import Embedder
from .extract import ChunkResult, Extractor
from .ground import ground_claim, score_claim
from .llm import LLMClient
from .match import find_candidates
from .models import Claim, GroundedClaim, Relation
from .pdf import Document, chunk_document, load_pdf
from .store import Store

log = logging.getLogger("fkl.pipeline")

Progress = Callable[[str, dict], None]


def claim_id_for(doc_id: str, claim: Claim, page: int) -> str:
    """Deterministic id: re-ingesting the same PDF updates rows instead of duplicating."""
    key = "|".join([doc_id, claim.subject.strip().lower(), claim.predicate.strip().lower(),
                    claim.value.strip().lower(), str(page),
                    claim.source_span.text.strip()[:80].lower()])
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:20]


@dataclass
class IngestReport:
    doc_id: str
    filename: str
    n_pages: int
    pages_read: int
    n_chunks: int
    proposed: int = 0
    deduped: int = 0
    stored: int = 0
    grounded: int = 0
    needs_review: int = 0
    chunk_errors: list[str] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0

    def as_dict(self) -> dict:
        return {
            "doc_id": self.doc_id, "filename": self.filename, "n_pages": self.n_pages,
            "pages_read": self.pages_read, "n_chunks": self.n_chunks,
            "claims_proposed": self.proposed, "claims_after_dedup": self.deduped,
            "claims_stored": self.stored, "claims_grounded": self.grounded,
            "claims_needing_review": self.needs_review,
            "chunk_errors": self.chunk_errors,
            "input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
        }


def _dedupe(claims: list[Claim]) -> list[Claim]:
    """Adjacent chunks and repeated tables restate the same claim. Keep the most
    confident copy of each (subject, predicate, value, context) tuple."""
    best: dict[tuple, Claim] = {}
    for c in claims:
        key = (
            c.subject.strip().lower(), c.predicate.strip().lower(), c.value.strip().lower(),
            (c.context.period or "").strip().lower(), (c.context.unit or "").strip().lower(),
            (c.context.scope or "").strip().lower(),
        )
        cur = best.get(key)
        if cur is None or c.confidence > cur.confidence:
            best[key] = c
    return list(best.values())


def extract_and_ground(document: Document, *, extractor: Extractor | None = None,
                       progress: Progress | None = None) -> tuple[list[GroundedClaim], IngestReport]:
    chunks = chunk_document(document, CONFIG.chunk_chars)
    report = IngestReport(
        doc_id=document.doc_id, filename=document.filename, n_pages=document.n_pages,
        pages_read=len(document.pages), n_chunks=len(chunks),
    )
    if progress:
        progress("chunked", {"n_chunks": len(chunks), "pages_read": len(document.pages)})

    extractor = extractor or Extractor()

    def on_chunk(done: int, total: int, res: ChunkResult) -> None:
        if progress:
            progress("extracting", {"done": done, "total": total,
                                    "claims": len(res.claims), "error": res.error})

    results = extractor.extract_document(
        document.filename, document.n_pages, chunks, progress=on_chunk
    )

    proposed: list[Claim] = []
    for res in results:
        proposed.extend(res.claims)
        report.input_tokens += res.input_tokens
        report.output_tokens += res.output_tokens
        if res.error:
            report.chunk_errors.append(
                f"pages {res.chunk.first_page}-{res.chunk.last_page}: {res.error}"
            )
    report.proposed = len(proposed)

    unique = _dedupe(proposed)
    report.deduped = len(unique)
    if progress:
        progress("grounding", {"total": len(unique)})

    grounded: list[GroundedClaim] = []
    seen_ids: set[str] = set()
    for claim in unique:
        g = ground_claim(document, claim)
        conf, needs_review, reasons = score_claim(
            claim, g,
            grounding_threshold=CONFIG.grounding_threshold,
            review_threshold=CONFIG.review_threshold,
        )
        page = g.page or claim.source_span.page
        cid = claim_id_for(document.doc_id, claim, page)
        if cid in seen_ids:
            continue
        seen_ids.add(cid)
        grounded.append(GroundedClaim(
            claim_id=cid, doc_id=document.doc_id, claim=claim, grounding=g,
            final_confidence=conf, needs_review=needs_review, review_reasons=reasons,
        ))

    report.stored = len(grounded)
    report.grounded = sum(1 for g in grounded if g.grounding.score >= CONFIG.grounding_threshold)
    report.needs_review = sum(1 for g in grounded if g.needs_review)
    return grounded, report


def ingest_pdf(path: str | Path, store: Store, *, max_pages: int | None = None,
               extractor: Extractor | None = None, embedder: Embedder | None = None,
               progress: Progress | None = None) -> IngestReport:
    path = Path(path)
    if progress:
        progress("loading", {"filename": path.name})
    document = load_pdf(path, max_pages=max_pages)

    store.upsert_document(
        doc_id=document.doc_id, filename=document.filename, path=str(path),
        n_pages=document.n_pages, pages_read=len(document.pages), status="extracting",
    )

    try:
        grounded, report = extract_and_ground(document, extractor=extractor, progress=progress)
    except Exception as e:
        store.upsert_document(
            doc_id=document.doc_id, filename=document.filename, path=str(path),
            n_pages=document.n_pages, pages_read=len(document.pages),
            status="failed", error=f"{type(e).__name__}: {e}",
        )
        raise

    if progress:
        progress("embedding", {"total": len(grounded)})
    embedder = embedder or Embedder()
    keys = [Embedder.claim_key(g.claim.subject, g.claim.predicate) for g in grounded]
    vectors = embedder.encode(keys)

    store.insert_claims(grounded, vectors)
    store.upsert_document(
        doc_id=document.doc_id, filename=document.filename, path=str(path),
        n_pages=document.n_pages, pages_read=len(document.pages), status="extracted",
        stats=report.as_dict(),
    )
    if progress:
        progress("stored", report.as_dict())
    return report


# --------------------------------------------------------------------------
# Cross-document relationship building
# --------------------------------------------------------------------------
@dataclass
class RelateReport:
    candidates: int = 0
    compared: int = 0
    escalated: int = 0
    escalation_skipped: int = 0
    by_kind: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "candidate_pairs": self.candidates, "compared": self.compared,
            "escalated_to_llm": self.escalated,
            "escalations_skipped_over_budget": self.escalation_skipped,
            "by_kind": self.by_kind,
        }


def build_relations(store: Store, *, new_claim_ids: list[str] | None = None,
                    escalate: bool = True, max_escalations: int | None = None,
                    client: LLMClient | None = None,
                    progress: Progress | None = None) -> RelateReport:
    """Compare embedding-similar claims and persist the verdicts.

    Passing new_claim_ids compares only the newly added claims against the rest
    of the corpus, which is what makes ingesting document N+1 cheap.
    """
    report = RelateReport()
    candidates = find_candidates(store, new_claim_ids=new_claim_ids)
    report.candidates = len(candidates)
    if progress:
        progress("matching", {"candidate_pairs": len(candidates)})
    if not candidates:
        return report

    ids = sorted({c.a_id for c in candidates} | {c.b_id for c in candidates})
    claims = store.get_claims(ids)

    budget = CONFIG.max_escalations if max_escalations is None else max_escalations
    pending: list[tuple[dict, dict, Relation]] = []
    relations: list[Relation] = []

    for cand in candidates:
        a, b = claims.get(cand.a_id), claims.get(cand.b_id)
        if not a or not b:
            continue
        relation, needs_escalation = compare_claims(a, b, cand.similarity)
        report.compared += 1
        if needs_escalation and escalate:
            pending.append((a, b, relation))
        else:
            relations.append(relation)

    # Escalate the most similar ambiguous pairs first -- those are the ones most
    # likely to be a real disagreement rather than a loose match.
    pending.sort(key=lambda p: -p[2].similarity)
    if pending and escalate:
        if progress:
            progress("adjudicating", {"ambiguous": len(pending),
                                      "budget": budget})
        client = client or LLMClient(model=CONFIG.judge_model)
        for i, (a, b, relation) in enumerate(pending):
            if i >= budget:
                relation.reasoning_trace.append(
                    "not escalated: per-run adjudication budget exhausted"
                )
                report.escalation_skipped += 1
                relations.append(relation)
                continue
            relations.append(adjudicate(a, b, relation, client))
            report.escalated += 1
            if progress:
                progress("adjudicating", {"done": i + 1,
                                          "total": min(len(pending), budget)})
    else:
        relations.extend(r for _, _, r in pending)

    store.insert_relations(relations)
    for r in relations:
        report.by_kind[r.kind] = report.by_kind.get(r.kind, 0) + 1
    if progress:
        progress("related", report.as_dict())
    return report
