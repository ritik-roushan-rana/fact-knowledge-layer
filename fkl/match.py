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
