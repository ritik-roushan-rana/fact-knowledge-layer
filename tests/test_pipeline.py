"""End-to-end, with no API key configured.

These tests are the acceptance criteria for the architecture: the pipeline must
ingest real PDFs, ground claims, and produce relationships without any provider
being reachable.
"""
import os

import pytest

from fkl.extractors import DeterministicClaimExtractor, build_extractor
from fkl.pipeline import build_relations, ingest_pdf
from fkl.store import Store
from tests.conftest import STARTER

DELHIVERY = STARTER / "delhivery" / "03-delhivery-q4-fy24-earnings-presentation.pdf"
MACRO = STARTER / "india-macroeconomy" / "03-imf-india-2025-article-iv-excerpt.pdf"

pytestmark = pytest.mark.skipif(not DELHIVERY.exists(),
                                reason="starter dataset not present")


@pytest.fixture
def no_api_key(monkeypatch):
    """Remove every provider credential for the duration of the test."""
    for var in ("GROQ_API_KEY", "OPENAI_API_KEY", "XAI_API_KEY",
                "GEMINI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    return True


class TestRunsWithoutAnyApiKey:
    def test_default_extractor_needs_no_credentials(self):
        extractor = build_extractor()
        assert isinstance(extractor, DeterministicClaimExtractor)
        assert extractor.requires_api_key is False

    def test_llm_fallback_is_off_by_default(self):
        from fkl.config import CONFIG
        assert CONFIG.enable_llm_fallback is False
        assert CONFIG.extractor == "deterministic"

    def test_full_ingest_with_no_key(self, tmp_path, no_api_key):
        store = Store(tmp_path / "t.db")
        report = ingest_pdf(DELHIVERY, store, max_pages=16, progress=None)
        assert report.stored > 50, "deterministic extraction produced too few claims"
        assert report.extractor == "deterministic"
        assert report.extractor_stats["llm_calls"] == 0

    def test_relations_build_with_no_key(self, tmp_path, no_api_key):
        store = Store(tmp_path / "t.db")
        ingest_pdf(DELHIVERY, store, max_pages=16)
        ingest_pdf(MACRO, store, max_pages=16)
        report = build_relations(store)
        assert report.escalated == 0
        assert report.llm_available is False
        assert report.compared >= 0


class TestProvenance:
    def test_every_stored_claim_carries_evidence(self, tmp_path, no_api_key):
        store = Store(tmp_path / "t.db")
        ingest_pdf(DELHIVERY, store, max_pages=16)
        claims = store.query_claims(limit=10**9)
        assert claims
        for c in claims:
            assert c["source_document"]
            assert c["span_page"] >= 1
            assert c["span_text"].strip()
            if not c["quarantined"]:
                assert c["grounding_page"] is not None

    def test_claims_carry_coordinates(self, tmp_path, no_api_key):
        store = Store(tmp_path / "t.db")
        ingest_pdf(DELHIVERY, store, max_pages=16)
        with_box = [c for c in store.query_claims(limit=10**9) if c["bbox"]]
        assert with_box, "no claim recorded a bounding box"
        assert len(with_box[0]["bbox"]) == 4


class TestTablesBecomeClaims:
    def test_table_cells_are_structured_claims(self, tmp_path, no_api_key):
        store = Store(tmp_path / "t.db")
        ingest_pdf(DELHIVERY, store, max_pages=16)
        table_claims = store.query_claims(origin="table", limit=10**9)
        assert table_claims, "no claims were reconstructed from tables"
        # A table claim should inherit a period and a unit from its header/corner.
        qualified = [c for c in table_claims if c["ctx_period"] and c["ctx_unit"]]
        assert qualified, "table claims inherited neither period nor unit"
        assert any(c["value_num"] is not None for c in table_claims)


class TestCheckpointAndResume:
    def test_completed_document_is_skipped_on_re_ingest(self, tmp_path, no_api_key):
        store = Store(tmp_path / "t.db")
        first = ingest_pdf(DELHIVERY, store, max_pages=16)
        assert not first.skipped_resume
        second = ingest_pdf(DELHIVERY, store, max_pages=16)
        assert second.skipped_resume
        assert second.stored == first.stored

    def test_force_re_ingests_without_duplicating(self, tmp_path, no_api_key):
        store = Store(tmp_path / "t.db")
        first = ingest_pdf(DELHIVERY, store, max_pages=16)
        again = ingest_pdf(DELHIVERY, store, max_pages=16, force=True)
        assert not again.skipped_resume
        # Deterministic claim ids mean a re-run updates rows rather than adding.
        assert store.count_claims() == first.stored


class TestGeneralisesAcrossCorpora:
    def test_macro_corpus_works_with_no_corpus_specific_rules(self, tmp_path, no_api_key):
        """The same rules must work on documents of a completely different kind."""
        store = Store(tmp_path / "t.db")
        report = ingest_pdf(MACRO, store, max_pages=20)
        assert report.stored > 5
        entity = report.extractor_stats.get("primary_entity")
        assert entity and "delhivery" not in entity.lower()
