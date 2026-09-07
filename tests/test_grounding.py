"""Grounding must reject evidence that is not really in the document.

A claim whose quote cannot be located is quarantined: stored and visible, but
never allowed into comparison.
"""
import pytest

from fkl.ground import ground_claim, score_claim
from fkl.models import Claim, SourceSpan
from fkl.pdf import Document, Line, Page, normalize_with_map

PAGE_ONE = (
    "Delhivery Limited Annual Report\n"
    "Revenue from operations was 81,415.38 million for the year ended March 2024.\n"
    "The Company employed 12,500 people as at 31 March 2024.\n"
)
PAGE_TWO = (
    "Segment information\n"
    "Express Parcel 6,595\n"
    "Part Truckload 1,547\n"
)


def _page(number: int, raw: str) -> Page:
    norm, mapping = normalize_with_map(raw)
    lines, cursor = [], 0
    for text in raw.split("\n"):
        lines.append(Line(text=text, bbox=(0.0, cursor * 10.0, 500.0, cursor * 10.0 + 9.0),
                          size=10.0, bold=False, char_start=cursor,
                          char_end=cursor + len(text), block_no=0))
        cursor += len(text) + 1
    return Page(number=number, label=None, raw=raw, norm=norm,
                norm_to_raw=mapping, lines=lines)


@pytest.fixture
def document() -> Document:
    pages = [_page(1, PAGE_ONE), _page(2, PAGE_TWO)]
    return Document(doc_id="test", filename="test.pdf", path=None,
                    pages=pages, n_pages=2)


def _claim(text: str, page: int = 1, value: str = "81,415.38") -> Claim:
    return Claim(subject="Delhivery Limited", predicate="revenue from operations",
                 value=value, source_document="test.pdf",
                 source_span=SourceSpan(page=page, text=text), confidence=0.9)


def _score(claim, grounding):
    return score_claim(claim, grounding, grounding_threshold=0.80, review_threshold=0.60)


class TestGroundingAccepts:
    def test_verbatim_quote_is_located_exactly(self, document):
        g = ground_claim(document, _claim("Revenue from operations was 81,415.38 million"))
        assert g.located and g.score == 1.0 and g.page == 1
        _conf, quarantined, _review, _reasons = _score(_claim("Revenue from operations was 81,415.38 million"), g)
        assert not quarantined

    def test_located_evidence_carries_coordinates(self, document):
        g = ground_claim(document, _claim("Revenue from operations was 81,415.38 million"))
        assert g.bbox is not None and len(g.bbox) == 4
        assert g.char_start is not None and g.char_end is not None

    def test_minor_whitespace_differences_still_ground(self, document):
        g = ground_claim(document, _claim("Revenue  from   operations was 81,415.38 million"))
        assert g.located and g.score >= 0.9


class TestGroundingQuarantines:
    def test_fabricated_quote_is_rejected(self, document):
        claim = _claim("Revenue from operations was 99,999.99 million", value="99,999.99")
        g = ground_claim(document, claim)
        _conf, quarantined, _review, reasons = _score(claim, g)
        assert quarantined, "a quote that is not in the document must not be usable"
        assert reasons

    def test_non_contiguous_stitched_quote_is_rejected(self, document):
        """Text assembled from separate places on the page is not evidence.

        This is the real failure mode observed with generative extraction:
        the model rebuilds a chart's visual layout into a quote that never
        occurs in the extracted text.
        """
        claim = _claim("Express Parcel 6,595 Part Truckload 1,547 Segment information",
                       page=2, value="6,595")
        g = ground_claim(document, claim)
        _conf, quarantined, _review, _reasons = _score(claim, g)
        assert quarantined

    def test_real_quote_carrying_an_invented_number_is_rejected(self, document):
        """The quote exists, but the number the claim asserts does not."""
        claim = _claim("Revenue from operations was 81,415.38 million", value="70,000")
        g = ground_claim(document, claim)
        assert g.located and g.score == 1.0
        assert g.value_in_span is False
        conf, quarantined, _review, reasons = _score(claim, g)
        assert quarantined
        assert any("numbers" in r for r in reasons)
        assert conf.grounding < 1.0

    def test_extraction_and_grounding_confidence_stay_separate(self, document):
        claim = _claim("Revenue from operations was 81,415.38 million")
        claim.confidence = 0.4
        g = ground_claim(document, claim)
        conf, _q, _r, _reasons = _score(claim, g)
        assert conf.extraction == 0.4
        assert conf.grounding == 1.0
        assert conf.combined == 0.4


class TestQuarantinedClaimsNeverCompare:
    def test_quarantined_claims_are_excluded_from_matching(self, tmp_path):
        from fkl.match import find_candidates
        from fkl.models import Confidence, GroundedClaim, Grounding
        from fkl.store import Store

        store = Store(tmp_path / "q.db")
        for doc in ("a.pdf", "b.pdf"):
            store.upsert_document(doc_id=doc, filename=doc, path=doc, n_pages=1,
                                  pages_read=1, status="extracted")
        good, bad = [], []
        for doc, bucket, quarantined in (("a.pdf", good, False), ("b.pdf", bad, True)):
            claim = Claim(subject="Acme Corp", predicate="revenue", value="100",
                          value_num=100.0, source_document=doc,
                          source_span=SourceSpan(page=1, text="revenue was 100"))
            bucket.append(GroundedClaim(
                claim_id=f"c-{doc}", doc_id=doc, claim=claim,
                grounding=Grounding(located=not quarantined, score=0.0 if quarantined else 1.0),
                confidence=Confidence(extraction=0.9, grounding=0.0 if quarantined else 1.0),
                quarantined=quarantined))
        store.insert_claims(good + bad, None)
        assert find_candidates(store) == []
