"""Candidate matching across documents.

Finding related claims must not depend on knowing what any predicate means, so
matching is done with embeddings over (subject, predicate) rather than string
rules. Two claims become candidates when they are talking about the same
property of the same entity; whether they agree is decided later, by
fkl.compare.

Incremental ingest is the reason this returns candidates rather than doing the
comparison itself: a new document only needs comparing against the claims that
are embedding-similar to it, not against the whole corpus.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .config import CONFIG
from .store import Store


@dataclass
class Candidate:
    a_id: str
    b_id: str
    similarity: float
    subject_similarity: float | None = None
    predicate_similarity: float | None = None


def find_candidates(store: Store, *, new_claim_ids: list[str] | None = None,
                    threshold: float | None = None, top_k: int | None = None,
                    cross_document_only: bool = True,
                    grounded_only: bool = True) -> list[Candidate]:
    """Return claim pairs worth comparing.

    new_claim_ids restricts the left-hand side to just-ingested claims, which is
    what makes adding a document cost O(new x corpus) instead of O(corpus^2).
    """
    threshold = CONFIG.match_threshold if threshold is None else threshold
    top_k = CONFIG.match_top_k if top_k is None else top_k

    ids, docs, mat = store.all_embeddings()
    if len(ids) < 2:
        return []

    if grounded_only:
        # Never assert a relationship on top of evidence we could not locate.
        keep = {
            r["claim_id"] for r in store.query_claims(limit=10**9)
            if r["grounded"] and r["grounding_score"] >= CONFIG.grounding_threshold
        }
        sel = [i for i, cid in enumerate(ids) if cid in keep]
        if len(sel) < 2:
            return []
        ids = [ids[i] for i in sel]
        docs = [docs[i] for i in sel]
        mat = mat[sel]

    index = {cid: i for i, cid in enumerate(ids)}
    if new_claim_ids is None:
        rows = list(range(len(ids)))
    else:
        rows = [index[c] for c in new_claim_ids if c in index]
    if not rows:
        return []

    sims = mat[rows] @ mat.T  # cosine: vectors are unit-normalised at encode time

    seen: set[tuple[str, str]] = set()
    out: list[Candidate] = []
    for local_i, global_i in enumerate(rows):
        row = sims[local_i].copy()
        row[global_i] = -1.0
        if cross_document_only:
            row[np.array([d == docs[global_i] for d in docs])] = -1.0

        k = min(top_k, len(ids) - 1)
        if k <= 0:
            continue
        best = np.argpartition(-row, k - 1)[:k]
        for j in best:
            score = float(row[j])
            if score < threshold:
                continue
            a, b = ids[global_i], ids[int(j)]
            key = (a, b) if a < b else (b, a)
            if key in seen:
                continue
            seen.add(key)
            out.append(Candidate(a_id=key[0], b_id=key[1], similarity=round(score, 4)))

    out.sort(key=lambda c: -c.similarity)
    return out


def gate_candidates(store: Store, candidates: list[Candidate], embedder,
                    *, subject_threshold: float | None = None,
                    predicate_threshold: float | None = None
                    ) -> tuple[list[Candidate], int]:
    """Drop pairs whose subjects or predicates are not actually the same thing.

    The retrieval embedding covers subject and predicate together, which means
    two claims about the same entity score highly even when they describe
    completely unrelated properties -- enough to be reported as a contradiction.
    Embedding subject and predicate independently and requiring both to clear a
    floor removes that failure mode. Recall cost: short synonym predicates that
    share no words ("headcount" vs "number of employees") can fall below the
    floor and be missed. That trade is deliberate -- a false contradiction is
    far more damaging than a missed corroboration.
    """
    subject_threshold = CONFIG.subject_threshold if subject_threshold is None else subject_threshold
    predicate_threshold = (CONFIG.predicate_threshold if predicate_threshold is None
                           else predicate_threshold)
    if not candidates:
        return [], 0

    ids = sorted({c.a_id for c in candidates} | {c.b_id for c in candidates})
    claims = store.get_claims(ids)

    subjects = sorted({c["subject"].strip().lower() for c in claims.values()})
    predicates = sorted({c["predicate"].strip().lower() for c in claims.values()})
    s_vecs = {s: v for s, v in zip(subjects, embedder.encode(subjects))}
    p_vecs = {p: v for p, v in zip(predicates, embedder.encode(predicates))}

    kept: list[Candidate] = []
    dropped = 0
    for cand in candidates:
        a, b = claims.get(cand.a_id), claims.get(cand.b_id)
        if not a or not b:
            dropped += 1
            continue
        s_sim = float(s_vecs[a["subject"].strip().lower()] @ s_vecs[b["subject"].strip().lower()])
        p_sim = float(p_vecs[a["predicate"].strip().lower()] @ p_vecs[b["predicate"].strip().lower()])
        if s_sim < subject_threshold or p_sim < predicate_threshold:
            dropped += 1
            continue
        cand.subject_similarity = round(s_sim, 4)
        cand.predicate_similarity = round(p_sim, 4)
        kept.append(cand)
    return kept, dropped
