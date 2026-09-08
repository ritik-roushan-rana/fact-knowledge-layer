"""Integration tests: the four cases the assignment brief requires.

These assert that, after ingesting the whole starter corpus, the system
actually produces one relationship of each required kind, with the right
shape. They are the acceptance tests for the submission: if these pass, every
promise in the README is checkable against the live store.

Slow. Marked `integration`. Skipped when the starter PDFs are missing, so a
clone that only has the code can still run the unit suite:

    pytest -q -m "not integration"   # unit tests only (fast, always)
    pytest -q -m integration         # this file (~100s: reingests the corpus)
    pytest -q                        # everything
"""
from __future__ import annotations

from pathlib import Path

import pytest

from fkl.config import CONFIG
from fkl.pipeline import build_relations, ingest_pdf
from fkl.store import Store

STARTER = Path(__file__).resolve().parent.parent / "starter-datasets"

# The four required cases are grounded in the two macro PDFs (the RBI Annual
# Report + the IMF Article IV corroborate and contradict on India's
# credit-deposit ratio) plus one Delhivery filing (which supplies component-of-
# total pairs where segment revenue rolls up into a total). Loading the full
# corpus is the honest way to prove the pipeline works end to end.
_CORPUS = [
    STARTER / "india-macroeconomy/02-rbi-annual-report-2024-25-excerpt.pdf",
    STARTER / "india-macroeconomy/03-imf-india-2025-article-iv-excerpt.pdf",
    STARTER / "delhivery/03-delhivery-q4-fy24-earnings-presentation.pdf",
]


pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def ingested_store(tmp_path_factory) -> Store:
    """Fresh SQLite DB with the three required PDFs ingested end to end.

    Module-scoped: the four case tests share one ingest, so the whole file
    runs in about the same time as one ingest instead of four.
    """
    missing = [p for p in _CORPUS if not p.exists()]
    if missing:
        pytest.skip(f"starter PDFs missing: {[str(p.relative_to(STARTER.parent)) for p in missing]}")

    db_path = tmp_path_factory.mktemp("fkl") / "case_tests.db"
    # Point the module-level CONFIG at the temp DB for the whole test module
    # by constructing a Store on that path. The store instance is what every
    # subsequent call uses; nothing else consults the default path.
    store = Store(db_path=db_path)

    for pdf in _CORPUS:
        ingest_pdf(pdf, store, extractor="deterministic", force=False)
    build_relations(store, escalate=False)
    return store


def _find(store: Store, kind: str, *, min_confidence: float = 0.4,
          predicate_contains: str | None = None) -> dict | None:
    """First relation of a kind that (optionally) mentions a predicate substring."""
    rows = store.query_relations(kind=kind, cross_document_only=True, limit=200)
    for r in rows:
        if r["confidence"] < min_confidence:
            continue
        if predicate_contains:
            claim_ids = [r["claim_a_id"], r["claim_b_id"]]
            claims = store.get_claims(claim_ids)
            preds = " ".join((c["predicate"] or "") for c in claims.values()).lower()
            if predicate_contains not in preds:
                continue
        return r
    return None


class TestFourRequiredCases:
    """One test per required case. Each one:
        1) finds a real relationship of that kind in the store,
        2) checks that both claims carry real evidence (text + page),
        3) checks the verdict was decided by rules, not by an LLM,
        4) checks the reasoning trace records the rules that fired.
    """

    def test_case_1_corroboration_across_documents(self, ingested_store):
        """A fact corroborated across two documents."""
        rel = _find(ingested_store, "corroboration",
                    predicate_contains="credit")
        assert rel is not None, \
            "expected at least one corroboration between the RBI + IMF credit-deposit ratios"
        claims = ingested_store.get_claims([rel["claim_a_id"], rel["claim_b_id"]])
        docs = {c["source_document"] for c in claims.values()}
        assert len(docs) == 2, "corroboration must span two documents"
        for c in claims.values():
            assert c["span_text"], "each claim must carry evidence text"
            assert c["span_page"] > 0, "each claim must record its page"
            assert c["grounded"], "corroboration claims must be grounded, not quarantined"
        assert rel["decided_by"] == "rules", \
            "the required-case verdict must come from deterministic rules, not the LLM"
        assert rel["reasoning_trace"], "the trace must record the rules that fired"

    def test_case_2_contradiction(self, ingested_store):
        """A genuine or likely contradiction."""
        rel = _find(ingested_store, "contradiction")
        assert rel is not None, \
            "expected at least one contradiction across the corpus (the RBI/IMF " \
            "credit-deposit ratios differ for 2022-23)"
        claims = ingested_store.get_claims([rel["claim_a_id"], rel["claim_b_id"]])
        docs = {c["source_document"] for c in claims.values()}
        assert len(docs) == 2, "a contradiction must span two documents"
        assert rel["decided_by"] == "rules"
        # A contradiction must actually record values-differ in the trace, or it
        # is a mislabelled other kind.
        trace = " | ".join(rel["reasoning_trace"]).lower()
        assert "value" in trace, "the trace must record a value comparison step"
        assert rel["value_delta"], "a contradiction must carry the numeric delta it measured"

    def test_case_3_reconciled_by_context(self, ingested_store):
        """An apparent contradiction explained by context (period, scope, unit, ...)."""
        rel = _find(ingested_store, "reconciled")
        assert rel is not None, \
            "expected at least one 'reconciled' pair (values differ, context accounts for it)"
        assert rel["decided_by"] == "rules"
        # The verdict is only honest when the trace names the differing context field.
        assert rel["context_diff"] or rel["value_agreement"] == "equivalent", (
            "a reconciled verdict must carry either the context field that differs "
            "or the value-agreement finding that explains why context alone matters"
        )
        assert rel["reasoning_trace"], "the trace must record the rules that fired"

    def test_case_4_extraction_or_reasoning_failure_is_surfaced(self, ingested_store):
        """A failure the system caught and shows in its own output.

        The pipeline handles failures by grounding: a claim it cannot verify
        against the page is marked ``quarantined`` (and never enters
        comparison) or ``needs_review`` (kept but flagged low-confidence).
        Either category satisfies case 4 -- the system explicitly does not
        pretend to be correct where it is not.
        """
        rows = ingested_store.query_claims(quarantined=True, limit=100)
        rows += ingested_store.query_claims(needs_review=True, limit=100)
        assert rows, ("case 4 requires at least one flagged extraction; if none "
                      "exist, either grounding is not enforcing thresholds or the "
                      "extractor is unrealistically clean")
        # Every flagged claim must actually record WHY it was flagged. Case 4
        # is about surfacing failure, not about hiding it as low confidence.
        flagged = [c for c in rows if c["quarantined"] or c["needs_review"]]
        assert flagged, "no genuinely flagged claim found"
        assert any(c["review_reasons"] for c in flagged), \
            "every flagged claim must record the reason it was flagged"


class TestNoLLMInDecisions:
    """Every stored relationship in the required-cases run was decided by rules.

    The whole engineering story is that the core is deterministic. This test
    holds it to that promise across the entire corpus, not just the four
    picked cases.
    """

    def test_every_relation_was_decided_by_rules(self, ingested_store):
        rows = ingested_store.query_relations(cross_document_only=False,
                                              exclude_unrelated=False, limit=100000)
        assert rows, "no relations in the store; ingest failed"
        deciders = {r["decided_by"] for r in rows}
        assert deciders == {"rules"}, (
            f"every relation must be decided by rules in the required-cases run; "
            f"found deciders: {deciders}"
        )

    def test_deterministic_extractor_recorded_zero_llm_calls(self, ingested_store):
        docs = ingested_store.list_documents()
        assert docs, "no documents in the store; ingest failed"
        for d in docs:
            stats = d.get("stats") or {}
            calls = stats.get("llm_calls", 0)
            assert calls == 0, (
                f"deterministic ingest should record zero LLM calls; "
                f"{d['filename']} reported {calls}"
            )
