"""Grounding: prove a claim's evidence actually exists in the document.

Nothing an extractor asserts is trusted. Every claim is located in the real
extracted text before it is allowed into comparison, and anything that cannot be
located is quarantined -- stored and visible, but never compared. An
ungroundable claim is a reporting output, not an input.

Three verification paths, because claims arrive in different shapes:

* **span claims** (deterministic sentence rules) already carry the character
  range they were cut from, so verification is an equality check.
* **table claims** are assembled from a row label, a column header and a cell
  that are not contiguous in the page's text. Both the label and the cell are
  verified to occur on the page independently, and the claim carries the
  coordinates of the cell.
* **proposed claims** (an LLM, or any extractor that quotes from memory) are
  located by exact then fuzzy search. This is the path that catches
  fabricated and stitched-together quotes.
"""
from __future__ import annotations

from rapidfuzz import fuzz

from .models import Claim, Confidence, Grounding
from .pdf import Document, Page, normalize_with_map, numbers_in

_NEARBY_WINDOW = 3
_VALUE_PAD = 100


def _page_search_order(document: Document, claimed_page: int) -> list[Page]:
    seen: set[int] = set()
    ordered: list[Page] = []

    def add(n: int) -> None:
        if n in seen:
            return
        pg = document.page(n)
        if pg is not None and pg.norm:
            seen.add(n)
            ordered.append(pg)

    add(claimed_page)
    for delta in range(1, _NEARBY_WINDOW + 1):
        add(claimed_page - delta)
        add(claimed_page + delta)
    for pg in document.pages:
        add(pg.number)
    return ordered


def _exact_locate(qnorm: str, pages: list[Page]):
    for pg in pages:
        pos = pg.norm.find(qnorm)
        if pos != -1:
            return pg, pos, pos + len(qnorm)
    return None


def _fuzzy_locate(qnorm: str, pages: list[Page]):
    """Find where a quote sits, then score how faithfully it reproduces that text.

    partial_ratio is the right tool for *finding* the window but the wrong one
    for scoring it: it rewards the best-matching sub-window, so a quote whose
    words were reordered or interleaved -- the signature of a chart rebuilt from
    its visual layout rather than read from the text -- still scores 0.80-0.85.
    Scoring the located window with an order-sensitive ratio makes that
    reordering visible, which is the whole point of grounding.
    """
    best_page, best_score = None, 0.0
    for pg in pages:
        score = fuzz.partial_ratio(qnorm, pg.norm, score_cutoff=60)
        if score > best_score:
            best_score, best_page = score, pg
            if score >= 99:
                break
    if best_page is None:
        return None
    align = fuzz.partial_ratio_alignment(qnorm, best_page.norm)
    if align is None:
        return None
    window = best_page.norm[align.dest_start:align.dest_end]
    faithfulness = fuzz.ratio(qnorm, window) / 100.0
    # Character similarity alone cannot see reordering: the same words in a
    # different order share nearly all their characters. Token order is what
    # separates a genuine quote from text reassembled out of a chart, so the
    # score is scaled by how much of the quote's word order actually survives
    # in the located window.
    return best_page, align.dest_start, align.dest_end, faithfulness * _order_factor(qnorm, window)


def _order_factor(query: str, window: str) -> float:
    """Fraction of the quote's tokens that appear in the window in the same order.

    Longest common subsequence over word tokens: 1.0 when the quote reads
    straight through the document text, lower the more it has been rearranged.
    """
    q, w = query.split(), window.split()
    if not q:
        return 0.0
    if q == w:
        return 1.0
    def alike(x: str, y: str) -> bool:
        # Tolerate OCR noise and small paraphrase differences; the signal we
        # care about is word ORDER, not exact spelling of every token.
        return x == y or (len(x) > 3 and len(y) > 3 and fuzz.ratio(x, y) >= 85)

    prev = [0] * (len(w) + 1)
    for token in q:
        cur = [0] * (len(w) + 1)
        for j, other in enumerate(w, 1):
            cur[j] = prev[j - 1] + 1 if alike(token, other) else max(prev[j], cur[j - 1])
        prev = cur
    return prev[len(w)] / len(q)


def _value_present(claim: Claim, text: str) -> bool | None:
    """Do the claim's own digits appear in the located evidence?

    Catches the case a fuzzy match cannot: a real quote carrying a number that
    is not in it.
    """
    wanted = numbers_in(claim.value)
    if not wanted:
        return None
    present = set(numbers_in(text))
    return all(w in present for w in wanted)


# --------------------------------------------------------------------------
def _ground_table_claim(document: Document, claim: Claim) -> Grounding:
    page = document.page(claim.source_span.page)
    if page is None:
        return Grounding(located=False, score=0.0, note="page not found")

    value_norm, _ = normalize_with_map(claim.value)
    # The evidence string is "row label | column header | cell"; the label is
    # the part that must also be verifiable on the page.
    label = claim.source_span.text.split("|")[0].strip()
    label_norm, _ = normalize_with_map(label)

    value_ok = bool(value_norm) and value_norm in page.norm
    label_ok = bool(label_norm) and label_norm in page.norm

    if value_ok and label_ok:
        score, note = 1.0, "table cell: row label and value both verified on the page"
    elif value_ok:
        score, note = 0.7, "table cell: value verified, row label not found verbatim"
    else:
        return Grounding(located=False, score=0.0, page=page.number,
                         page_label=page.label,
                         note="table cell value does not occur on the page")

    span = claim.source_span.char_span
    matched = (page.raw[span[0]:span[1]].strip() if span else claim.source_span.text)
    bbox = claim.source_span.bbox or (page.bbox_for_chars(*span) if span else None)

    return Grounding(
        located=True, score=score, page=page.number, page_label=page.label,
        matched_text=matched or claim.source_span.text,
        char_start=span[0] if span else None,
        char_end=span[1] if span else None,
        bbox=tuple(bbox) if bbox else None,
        page_shift=0,
        value_in_span=value_ok,
        note=note,
    )


def _ground_span_claim(document: Document, claim: Claim) -> Grounding | None:
    """Fast path: the extractor recorded exactly where it cut the evidence."""
    span = claim.source_span.char_span
    if not span:
        return None
    page = document.page(claim.source_span.page)
    if page is None:
        return None
    actual = page.raw[span[0]:span[1]]
    if actual.strip() != claim.source_span.text.strip():
        return None                    # offsets do not agree; fall back to search
    bbox = claim.source_span.bbox or page.bbox_for_chars(*span)
    padded = page.raw[max(0, span[0] - _VALUE_PAD):span[1] + _VALUE_PAD]
    return Grounding(
        located=True, score=1.0, page=page.number, page_label=page.label,
        matched_text=actual.strip(), char_start=span[0], char_end=span[1],
        bbox=tuple(bbox) if bbox else None, page_shift=0,
        value_in_span=_value_present(claim, padded),
        note="verified at the recorded character offsets",
    )


def _ground_by_search(document: Document, claim: Claim) -> Grounding:
    quote = (claim.source_span.text or "").strip()
    if len(quote) < 8:
        return Grounding(located=False, score=0.0, note="proposed span too short to verify")
    qnorm, _ = normalize_with_map(quote)
    if not qnorm:
        return Grounding(located=False, score=0.0, note="proposed span has no matchable content")

    pages = _page_search_order(document, claim.source_span.page)
    if not pages:
        return Grounding(located=False, score=0.0, note="document has no extractable text")

    exact = _exact_locate(qnorm, pages)
    if exact is not None:
        page, start, end = exact
        score, note = 1.0, "exact match"
    else:
        fuzzy = _fuzzy_locate(qnorm, pages)
        if fuzzy is None:
            return Grounding(located=False, score=0.0,
                             note="span not found anywhere in the document")
        page, start, end, score = fuzzy
        note = "fuzzy match"

    matched_text = page.raw_slice(start, end)
    raw_start, raw_end = page.raw_offsets(start, end)
    padded = page.raw_slice(max(0, start - _VALUE_PAD), end + _VALUE_PAD)
    bbox = page.bbox_for_chars(raw_start, raw_end)

    return Grounding(
        located=True, score=round(score, 4), page=page.number, page_label=page.label,
        matched_text=matched_text, char_start=raw_start, char_end=raw_end,
        bbox=tuple(bbox) if bbox else None,
        page_shift=page.number - claim.source_span.page,
        value_in_span=_value_present(claim, padded),
        note=note,
    )


def ground_claim(document: Document, claim: Claim) -> Grounding:
    if claim.origin == "table":
        return _ground_table_claim(document, claim)
    fast = _ground_span_claim(document, claim)
    if fast is not None:
        return fast
    return _ground_by_search(document, claim)


# --------------------------------------------------------------------------
def score_claim(claim: Claim, grounding: Grounding, *, grounding_threshold: float,
                review_threshold: float) -> tuple[Confidence, bool, bool, list[str]]:
    """Return (confidence, quarantined, needs_review, reasons).

    Extraction and grounding confidence are kept apart rather than collapsed:
    they answer different questions, and a weak extraction must not inherit
    certainty from a clean quote match.
    """
    reasons: list[str] = []
    grounding_conf = float(grounding.score)

    if not grounding.located or grounding.score < grounding_threshold:
        reasons.append(
            f"evidence could not be located in the document "
            f"(match {grounding.score:.2f} < {grounding_threshold:.2f})")
    if grounding.value_in_span is False:
        grounding_conf *= 0.5
        reasons.append("the claim's numbers do not appear in the located evidence")
    if grounding.page_shift not in (None, 0):
        reasons.append(f"evidence was found on page {grounding.page}, not the claimed "
                       f"page {claim.source_span.page}")
    if claim.confidence < 0.5:
        reasons.append(f"extractor was unsure (extraction confidence {claim.confidence:.2f})")

    confidence = Confidence(extraction=round(float(claim.confidence), 4),
                            grounding=round(max(0.0, min(1.0, grounding_conf)), 4))

    # Quarantine is about evidence only. A claim whose quote cannot be located,
    # or whose number is absent from the text it points at, is not admissible.
    quarantined = (not grounding.located
                   or grounding.score < grounding_threshold
                   or grounding.value_in_span is False)
    needs_review = quarantined or confidence.combined < review_threshold
    if needs_review and not reasons:
        reasons.append(f"combined confidence {confidence.combined:.2f} is below the "
                       f"review threshold {review_threshold:.2f}")
    return confidence, quarantined, needs_review, reasons
