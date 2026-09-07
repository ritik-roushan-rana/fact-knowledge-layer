"""Candidate generation.

Fully deterministic and embedding-free. Pairs are produced by blocking, in two
stages that mirror the two independent gates:

1. **Entity clustering** -- distinct subject strings are clustered with the
   deterministic entity matcher. Only claims in the same cluster can ever be
   compared, so "Republic of India" and "Acme Corp" never meet.
2. **Predicate token blocking** -- within a cluster, two claims become
   candidates only if their predicates share at least one discriminating
   content word. This is what stops "employee count" being compared against
   "revenue" merely because both belong to the same company.

An earlier version retrieved candidates with a single embedding over
(subject, predicate). That failed in a specific and damaging way: when both
claims share a subject, the subject dominates the vector, so unrelated
properties scored as high as genuine synonyms. Blocking on the two fields
separately removes the failure mode and needs no model.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass

from .config import CONFIG
from .entity import normalize_entity, same_entity
from .normalize import parse_value, unit_signature
from .predicate import content_tokens
from .store import Store

log = logging.getLogger("fkl.match")


def measure_kind(claim: dict) -> str:
    """Coarse kind of quantity: a rate is never the same measurement as a level.

    Blocking on this removes the largest single source of junk pairs -- growth
    percentages being compared against the amounts they describe.
    """
    sig = unit_signature(claim.get("unit") or claim.get("ctx_unit"),
                         parse_value(claim["value"]))
    if sig["percent"]:
        return "rate"
    if claim.get("value_num") is None and parse_value(claim["value"]).base_number is None:
        return "text"
    return "quantity"


@dataclass
class Candidate:
    a_id: str
    b_id: str
    similarity: float = 0.0            # lexical predicate overlap, 0-1
    subject_similarity: float | None = None
    predicate_similarity: float | None = None


def cluster_entities(subjects: list[str]) -> dict[str, str]:
    """Map each distinct subject string to a cluster key.

    Distinct subject strings are few relative to claims, so the quadratic
    comparison here is cheap and exact.
    """
    clusters: dict[str, str] = {}
    representatives: list[str] = []
    for subject in sorted(set(subjects), key=lambda s: (-len(s), s)):
        key = normalize_entity(subject)
        if not key:
            continue
        for rep in representatives:
            ok, _score, _why = same_entity(subject, rep)
            if ok:
                clusters[subject] = clusters[rep]
                break
        else:
            representatives.append(subject)
            clusters[subject] = key
    return clusters


def find_candidates(store: Store, *, new_claim_ids: list[str] | None = None,
                    cross_document_only: bool = True,
                    grounded_only: bool = True,
                    max_pairs_per_claim: int | None = None) -> list[Candidate]:
    """Pairs worth comparing.

    new_claim_ids restricts one side to just-ingested claims, so adding a
    document costs O(new x corpus) rather than re-running every pair.
    """
    top_k = max_pairs_per_claim or CONFIG.match_top_k

    claims = store.query_claims(limit=10**9)
    if grounded_only:
        # Quarantined claims never enter comparison: a relationship must not
        # rest on evidence we could not verify.
        claims = [c for c in claims if not c["quarantined"]]
    if len(claims) < 2:
        return []

    by_id = {c["claim_id"]: c for c in claims}
    clusters = cluster_entities([c["subject"] for c in claims])
    kinds = {c["claim_id"]: measure_kind(c) for c in claims}

    # entity cluster -> predicate token -> claim ids
    index: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    tokens_for: dict[str, set[str]] = {}
    for c in claims:
        key = clusters.get(c["subject"])
        if not key:
            continue
        toks = set(content_tokens(c["predicate"]))
        tokens_for[c["claim_id"]] = toks
        for tok in toks:
            index[key][tok].append(c["claim_id"])

    targets = (new_claim_ids if new_claim_ids is not None
               else [c["claim_id"] for c in claims])
    targets = [t for t in targets if t in by_id]

    seen: set[tuple[str, str]] = set()
    out: list[Candidate] = []
    for cid in targets:
        a = by_id[cid]
        key = clusters.get(a["subject"])
        if not key:
            continue
        a_toks = tokens_for.get(cid, set())
        partners: set[str] = set()
        for tok in a_toks:
            partners.update(index[key].get(tok, ()))
        partners.discard(cid)

        scored: list[tuple[float, str]] = []
        for pid in partners:
            b = by_id.get(pid)
            if b is None:
                continue
            if cross_document_only and b["doc_id"] == a["doc_id"]:
                continue
            if kinds.get(pid) != kinds.get(cid):
                continue        # a rate and an amount are not the same measurement
            b_toks = tokens_for.get(pid, set())
            union = a_toks | b_toks
            overlap = len(a_toks & b_toks) / len(union) if union else 0.0
            scored.append((overlap, pid))

        scored.sort(reverse=True)
        for overlap, pid in scored[:top_k]:
            pair = (cid, pid) if cid < pid else (pid, cid)
            if pair in seen:
                continue
            seen.add(pair)
            out.append(Candidate(a_id=pair[0], b_id=pair[1],
                                 similarity=round(overlap, 4)))

    out.sort(key=lambda c: -c.similarity)
    return out
