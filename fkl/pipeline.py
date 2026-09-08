"""Ingest and relationship orchestration.

    PDF -> structured pages -> rule-based claims -> grounding -> storage
                                                        |
                                                        v
                                    blocking -> deterministic comparison
                                                        |
                                          (ambiguous only, optional) -> LLM

Runs end to end with no API key. Resume is content-addressed: a document's id
is the hash of its bytes and claim ids are deterministic, so re-ingesting the
same file updates rows in place rather than duplicating them, and an already
completed document is skipped unless forced.
"""
from __future__ import annotations

import hashlib
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .adjudicate import adjudicate
from .compare import compare_claims, upgrade_components_of_total
from .config import CONFIG
from .extractors import ClaimExtractor, build_extractor
from .ground import ground_claim, score_claim
from .match import find_candidates
from .models import Claim, GroundedClaim, Relation
from .pdf import Document, load_pdf
from .store import Store

log = logging.getLogger("fkl.pipeline")
Progress = Callable[[str, dict], None]


def claim_id_for(doc_id: str, claim: Claim, page: int) -> str:
    """Deterministic id -- the basis of dedup and of resume."""
    key = "|".join([doc_id, claim.subject.strip().lower(), claim.predicate.strip().lower(),
                    claim.value.strip().lower(), str(page), claim.origin,
                    (claim.context.period or "").lower(),
                    (claim.context.scope or "").lower(),
                    claim.source_span.text.strip()[:80].lower()])
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:20]


@dataclass
class IngestReport:
    doc_id: str = ""
    filename: str = ""
    n_pages: int = 0
    pages_read: int = 0
    extractor: str = ""
    batch_id: str = ""
    skipped_resume: bool = False
    proposed: int = 0
    deduped: int = 0
    stored: int = 0
    grounded: int = 0
    quarantined: int = 0
    needs_review: int = 0
    errors: list[str] = field(default_factory=list)
    extractor_stats: dict = field(default_factory=dict)
    seconds: float = 0.0

    def as_dict(self) -> dict:
        return {
            "doc_id": self.doc_id, "filename": self.filename,
            "n_pages": self.n_pages, "pages_read": self.pages_read,
            "extractor": self.extractor, "batch_id": self.batch_id,
            "skipped_resume": self.skipped_resume,
            "claims_proposed": self.proposed, "claims_after_dedup": self.deduped,
            "claims_stored": self.stored, "claims_grounded": self.grounded,
            "claims_quarantined": self.quarantined,
            "claims_needing_review": self.needs_review,
            "errors": self.errors, "seconds": self.seconds,
            **{f"extractor_{k}": v for k, v in self.extractor_stats.items()},
        }


def _dedupe(claims: list[Claim]) -> list[Claim]:
    best: dict[tuple, Claim] = {}
    for c in claims:
        key = (c.subject.strip().lower(), c.predicate.strip().lower(),
               c.value.strip().lower(), (c.context.period or "").lower(),
               (c.context.scope or "").lower(), (c.context.unit or "").lower())
        cur = best.get(key)
        if cur is None or c.confidence > cur.confidence:
            best[key] = c
    return list(best.values())


def extract_and_ground(document: Document, *, extractor: ClaimExtractor,
                       progress: Progress | None = None
                       ) -> tuple[list[GroundedClaim], IngestReport]:
    report = IngestReport(doc_id=document.doc_id, filename=document.filename,
                          n_pages=document.n_pages, pages_read=len(document.pages),
                          extractor=extractor.name)

    result = extractor.extract(document, progress=progress)
    report.proposed = len(result.claims)
    report.errors.extend(result.errors)
    report.extractor_stats = result.stats

    unique = _dedupe(result.claims)
    report.deduped = len(unique)
    if progress:
        progress("grounding", {"done": 0, "total": len(unique)})

    grounded: list[GroundedClaim] = []
    seen: set[str] = set()
    for i, claim in enumerate(unique):
        # Grounding every claim takes long enough on a large document that the
        # UI would otherwise sit on a frozen bar for the whole stage.
        if progress and i and i % 100 == 0:
            progress("grounding", {"done": i, "total": len(unique)})
        g = ground_claim(document, claim)
        confidence, quarantined, needs_review, reasons = score_claim(
            claim, g, grounding_threshold=CONFIG.grounding_threshold,
            review_threshold=CONFIG.review_threshold)
        page = g.page or claim.source_span.page
        cid = claim_id_for(document.doc_id, claim, page)
        if cid in seen:
            continue
        seen.add(cid)
        grounded.append(GroundedClaim(
            claim_id=cid, doc_id=document.doc_id, claim=claim, grounding=g,
            confidence=confidence, quarantined=quarantined,
            needs_review=needs_review, review_reasons=reasons))

    report.stored = len(grounded)
    report.grounded = sum(1 for g in grounded if not g.quarantined)
    report.quarantined = sum(1 for g in grounded if g.quarantined)
    report.needs_review = sum(1 for g in grounded if g.needs_review)
    return grounded, report


def ingest_pdf(path: str | Path, store: Store, *, max_pages: int | None = None,
               extractor: ClaimExtractor | str | None = None,
               batch_id: str | None = None, force: bool = False,
               embed: bool | None = None,
               progress: Progress | None = None) -> IngestReport:
    started = time.time()
    path = Path(path)
    batch_id = batch_id or uuid.uuid4().hex[:12]
    if extractor is None or isinstance(extractor, str):
        extractor = build_extractor(extractor)

    if progress:
        progress("loading", {"filename": path.name})
    document = load_pdf(path, max_pages=max_pages)

    # Resume: the doc id is the hash of the file's bytes, so an identical file
    # already ingested successfully is skipped rather than redone.
    if not force and store.document_is_complete(document.doc_id):
        existing = store.get_document(document.doc_id)
        report = IngestReport(
            doc_id=document.doc_id, filename=document.filename,
            n_pages=document.n_pages, pages_read=existing.get("pages_read", 0),
            extractor=existing.get("extractor") or "", batch_id=batch_id,
            skipped_resume=True,
            stored=store.count_claims(doc_id=document.doc_id),
            seconds=round(time.time() - started, 2))
        if progress:
            progress("skipped", report.as_dict())
        return report

    store.upsert_document(doc_id=document.doc_id, filename=document.filename,
                          path=str(path), n_pages=document.n_pages,
                          pages_read=len(document.pages), status="extracting",
                          extractor=extractor.name, batch_id=batch_id)
    try:
        grounded, report = extract_and_ground(document, extractor=extractor,
                                              progress=progress)
    except Exception as e:
        store.upsert_document(doc_id=document.doc_id, filename=document.filename,
                              path=str(path), n_pages=document.n_pages,
                              pages_read=len(document.pages), status="failed",
                              error=f"{type(e).__name__}: {e}",
                              extractor=extractor.name, batch_id=batch_id)
        raise
    report.batch_id = batch_id

    # Embeddings are optional: matching is deterministic and does not need them.
    vectors = None
    if CONFIG.enable_embeddings if embed is None else embed:
        try:
            from .embed import Embedder
            embedder = Embedder()
            vectors = embedder.encode(
                [Embedder.claim_key(g.claim.subject, g.claim.predicate) for g in grounded])
        except Exception as e:
            report.errors.append(f"embeddings unavailable: {type(e).__name__}: {e}")

    store.insert_claims(grounded, vectors)
    report.seconds = round(time.time() - started, 2)
    store.upsert_document(doc_id=document.doc_id, filename=document.filename,
                          path=str(path), n_pages=document.n_pages,
                          pages_read=len(document.pages), status="extracted",
                          extractor=extractor.name, batch_id=batch_id,
                          stats=report.as_dict())
    if progress:
        progress("stored", report.as_dict())
    return report


# --------------------------------------------------------------------------
@dataclass
class RelateReport:
    candidates: int = 0
    compared: int = 0
    stored: int = 0
    rejected_unrelated: int = 0
    ambiguous: int = 0
    escalated: int = 0
    escalation_skipped: int = 0
    llm_available: bool = False
    by_kind: dict = field(default_factory=dict)
    seconds: float = 0.0

    def as_dict(self) -> dict:
        total = max(1, self.compared)
        return {
            "candidate_pairs": self.candidates, "compared": self.compared,
            "relations_stored": self.stored,
            "rejected_as_unrelated": self.rejected_unrelated,
            "ambiguous_pairs": self.ambiguous,
            "escalated_to_llm": self.escalated,
            "escalations_skipped_over_budget": self.escalation_skipped,
            "llm_fallback_percent": round(100.0 * self.escalated / total, 2),
            "llm_available": self.llm_available,
            "by_kind": self.by_kind, "seconds": self.seconds,
        }


def build_relations(store: Store, *, new_claim_ids: list[str] | None = None,
                    escalate: bool | None = None, max_escalations: int | None = None,
                    progress: Progress | None = None) -> RelateReport:
    started = time.time()
    report = RelateReport()
    escalate = CONFIG.enable_llm_fallback if escalate is None else escalate

    if progress:
        progress("matching", {})
    candidates = find_candidates(store, new_claim_ids=new_claim_ids)
    report.candidates = len(candidates)
    if progress:
        progress("comparing", {"done": 0, "total": len(candidates),
                               "candidate_pairs": len(candidates)})
    if not candidates:
        report.seconds = round(time.time() - started, 2)
        return report

    ids = sorted({c.a_id for c in candidates} | {c.b_id for c in candidates})
    claims = store.get_claims(ids)

    relations: list[Relation] = []
    pending: list[tuple[dict, dict, Relation]] = []
    for i, cand in enumerate(candidates):
        if progress and i and i % 200 == 0:
            progress("comparing", {"done": i, "total": len(candidates),
                                   "candidate_pairs": len(candidates)})
        a, b = claims.get(cand.a_id), claims.get(cand.b_id)
        if not a or not b:
            continue
        relation, ambiguous = compare_claims(
            a, b, similarity=cand.similarity,
            subject_similarity=cand.subject_similarity,
            predicate_similarity=cand.predicate_similarity)
        report.compared += 1
        if relation.kind == "unrelated":
            # A rejected candidate is not a finding; count it, do not store it.
            report.rejected_unrelated += 1
            continue
        if ambiguous:
            report.ambiguous += 1
            pending.append((a, b, relation))
        else:
            relations.append(relation)

    if pending:
        client = None
        # Strict-deterministic mode never touches a provider, full stop.
        if CONFIG.strict_deterministic:
            if escalate:
                log.info("strict_deterministic=1: refusing to escalate ambiguous pairs")
            escalate = False
        if escalate:
            try:
                from .llm import LLMClient
                client = LLMClient(model=CONFIG.judge_model)
                report.llm_available = True
            except Exception as e:
                log.info("LLM fallback not available (%s); keeping rule verdicts", e)

        budget = CONFIG.max_escalations if max_escalations is None else max_escalations
        pending.sort(key=lambda p: -p[2].match_confidence)
        for i, (a, b, relation) in enumerate(pending):
            if client is None:
                if CONFIG.strict_deterministic:
                    relation.reasoning_trace.append(
                        "strict_deterministic=1: LLM path unreachable by policy")
                else:
                    relation.reasoning_trace.append(
                        "LLM fallback disabled; the deterministic verdict stands")
                relations.append(relation)
                continue
            if i >= budget:
                relation.reasoning_trace.append(
                    "not escalated: per-run adjudication budget exhausted")
                report.escalation_skipped += 1
                relations.append(relation)
                continue
            if progress:
                progress("adjudicating", {"done": i + 1,
                                          "total": min(len(pending), budget)})
            relations.append(adjudicate(a, b, relation, client))
            report.escalated += 1

    # Corpus-wide check: siblings that sum to the broader claim get their
    # verdict strengthened from "plausible component" to "confirmed component".
    if relations:
        claims_by_id = {**claims}
        # Pull in any claims we may have skipped above (unrelated verdicts).
        needed = {r.claim_a_id for r in relations} | {r.claim_b_id for r in relations}
        missing = [cid for cid in needed if cid not in claims_by_id]
        if missing:
            claims_by_id.update(store.get_claims(missing))
        try:
            strengthened = upgrade_components_of_total(relations, claims_by_id)
            if strengthened and progress:
                progress("components_confirmed", {"upgraded": strengthened})
        except Exception as e:
            log.warning("component-of-total post-pass failed: %s", e)

    store.insert_relations(relations)
    report.stored = len(relations)
    for r in relations:
        report.by_kind[r.kind] = report.by_kind.get(r.kind, 0) + 1
    report.seconds = round(time.time() - started, 2)
    if progress:
        progress("related", report.as_dict())
    return report
