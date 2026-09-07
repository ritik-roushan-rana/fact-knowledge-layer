"""Grounding: prove the model's quote actually exists in the document.

The extractor proposes a page and a verbatim span. We do not trust either. This
module locates the span in the real extracted text and reports how well it
matched, where it really is, and whether the claim's numbers survive the trip.
That match quality is half of every claim's final confidence.
"""
from __future__ import annotations

from rapidfuzz import fuzz

from .models import Claim, Grounding
from .pdf import Document, Page, normalize_with_map, numbers_in

# How far either side of the claimed page to look before scanning the whole doc.
_NEARBY_WINDOW = 3
# Padding (in normalized chars) around a match when checking for the value's digits.
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


def _exact_locate(qnorm: str, pages: list[Page]) -> tuple[Page, int, int] | None:
    for pg in pages:
        pos = pg.norm.find(qnorm)
        if pos != -1:
            return pg, pos, pos + len(qnorm)
    return None


def _fuzzy_locate(qnorm: str, pages: list[Page]) -> tuple[Page, int, int, float] | None:
    """Cheap score pass over every page, alignment only on the winner."""
    best_page: Page | None = None
    best_score = 0.0
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
    return best_page, align.dest_start, align.dest_end, best_score / 100.0


def ground_claim(document: Document, claim: Claim) -> Grounding:
    quote = (claim.source_span.text or "").strip()
    if len(quote) < 8:
        return Grounding(
            located=False, score=0.0, note="proposed span too short to verify"
        )

    qnorm, _ = normalize_with_map(quote)
    if not qnorm:
        return Grounding(located=False, score=0.0, note="proposed span had no matchable content")

    pages = _page_search_order(document, claim.source_span.page)
    if not pages:
        return Grounding(located=False, score=0.0, note="document has no extractable text")

    exact = _exact_locate(qnorm, pages)
    if exact is not None:
        page, start, end = exact
        score = 1.0
    else:
        fuzzy = _fuzzy_locate(qnorm, pages)
        if fuzzy is None:
            return Grounding(located=False, score=0.0, note="span not found anywhere in document")
        page, start, end, score = fuzzy

    matched_text = page.raw_slice(start, end)
    padded = page.raw_slice(max(0, start - _VALUE_PAD), end + _VALUE_PAD)

    wanted = numbers_in(claim.value)
    if wanted:
        present = set(numbers_in(padded))
        value_in_span = all(w in present for w in wanted)
    else:
        value_in_span = None

    return Grounding(
        located=score > 0.0,
        score=round(score, 4),
        page=page.number,
        page_label=page.label,
        matched_text=matched_text,
        char_start=start,
        char_end=end,
        page_shift=page.number - claim.source_span.page,
        value_in_span=value_in_span,
        note="exact match" if score == 1.0 else "fuzzy match",
    )


def score_claim(claim: Claim, grounding: Grounding, *, grounding_threshold: float,
                review_threshold: float) -> tuple[float, bool, list[str]]:
    """Combine extraction confidence with grounding quality into one number."""
    reasons: list[str] = []

    confidence = float(claim.confidence) * float(grounding.score)

    if not grounding.located or grounding.score < grounding_threshold:
        reasons.append(
            f"quote could not be located in the document (match {grounding.score:.2f} "
            f"< {grounding_threshold:.2f})"
        )
    if grounding.value_in_span is False:
        # The model quoted real text but the number it reported is not in it.
        confidence *= 0.5
        reasons.append("numbers in the claimed value do not appear in the located evidence")
    if grounding.page_shift not in (None, 0):
        reasons.append(f"evidence was on page {grounding.page}, not the claimed page "
                       f"{claim.source_span.page}")
    if claim.confidence < 0.5:
        reasons.append(f"extractor was unsure (confidence {claim.confidence:.2f})")

    confidence = max(0.0, min(1.0, confidence))
    needs_review = confidence < review_threshold or not grounding.located or grounding.value_in_span is False
    if needs_review and not reasons:
        reasons.append(f"final confidence {confidence:.2f} below review threshold {review_threshold:.2f}")
    return round(confidence, 4), needs_review, reasons
