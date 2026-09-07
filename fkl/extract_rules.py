"""Rule-based claim extraction.

Two complementary paths, both deterministic:

* **Sentence claims** -- a label, a linking phrase, and a value that occur
  contiguously in the page text ("Revenue from operations was 81,415.38
  million"). Because label and value are contiguous, the evidence span is exact
  and grounding is trivially verifiable.
* **Table claims** -- a row label, a column header and a cell, reassembled into
  one claim, with units and periods inherited from the corner cell, caption or
  header. Label and value are verified separately on the page, and the claim
  carries the coordinates of the cell it came from.

Nothing here knows what any document is about. The rules key off document
structure (headings, tables, captions) and general English patterns; the
vocabulary they use lives in fkl.lexicon and is ordinary reporting language.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from .lexicon import (ATTRIBUTION_PATTERNS, BASIS_TERMS, CHANGE_PHRASES,
                      CLAUSE_OPENERS, ENTITY_STOPWORDS, LEADING_FILLER,
                      LINKING_PHRASES, MODALITY_TERMS, ORG_SUFFIXES,
                      SCOPE_TERMS, TRAILING_FILLER)
from .models import Claim, ClaimContext, SourceSpan
from .normalize import parse_value, period_signature
from .pdf import Document, Page, repeated_line_texts
from .tables import Table, column_is_period

# --------------------------------------------------------------------------
# value detection
# --------------------------------------------------------------------------
_CURRENCY_PREFIX = r"(?:[₹$€£¥]|\b(?:Rs\.?|INR|USD|US\$|EUR|GBP|JPY)\b)"
_MAGNITUDE = (r"(?:hundred|thousand|lakhs?|lacs?|million|billion|trillion|crores?"
              r"|mn|bn|tn|cr|k)\b")
_PERCENT = r"(?:%|per\s?cent(?:age)?|percent)"
_NUMBER = r"\(?-?\d[\d,]*(?:\.\d+)?\)?"

VALUE_RE = re.compile(
    rf"(?P<value>(?:{_CURRENCY_PREFIX}\s*)?{_NUMBER}"
    rf"(?:\s*{_MAGNITUDE})?(?:\s*{_PERCENT})?)",
    re.I)

# Numbers that are almost never measurements in running prose.
_YEARISH = re.compile(r"^\(?(19|20)\d{2}\)?$")
_ORDINAL_CTX = re.compile(r"(?:page|section|chapter|note|clause|para(?:graph)?|"
                          r"table|figure|item|no\.?|sr\.?)\s*$", re.I)

_LINKERS_SORTED = sorted(LINKING_PHRASES, key=len, reverse=True)
_LINK_RE = re.compile(
    "(?:" + "|".join(re.escape(p) for p in _LINKERS_SORTED) + r")\s*$", re.I)
_CHANGE_SORTED = sorted(CHANGE_PHRASES, key=len, reverse=True)
_CHANGE_RE = re.compile(
    "(?P<verb>" + "|".join(re.escape(p) for p in _CHANGE_SORTED) + r")\s*$", re.I)
_FOOTNOTE = re.compile(r"\s*\(\d{1,2}\)?\s*$")

_SENT_SPLIT = re.compile(
    r"(?<=[.!?;])\s+(?=[A-Z(\"'\u2018\u201c\u20b9$\u20ac\u00a3])|\n{2,}")
_PROPER = re.compile(
    r"\b([A-Z][A-Za-z0-9&.'’-]+(?:[ ]+(?:of|and|the|de|for)[ ]+[A-Z][A-Za-z0-9&.'’-]+"
    r"|[ ]+[A-Z][A-Za-z0-9&.'’-]+){0,4})\b")


_TRAILING_MEASURE = re.compile(
    rf"{_NUMBER}\s*(?:{_MAGNITUDE})?\s*(?:{_PERCENT})?", re.I)


def _clean_label(text: str) -> str:
    """Trim a candidate predicate down to the property name.

    Three generic trims, in order: keep only the last physical line (a label
    must not span a layout break), cut everything up to and including the last
    measurement already mentioned (otherwise the first half of "growth of 6.5
    percent in FY25, real GDP" leaks into the second claim's predicate), then
    keep only the final clause.
    """
    text = text.split("\n")[-1]
    # If the candidate label already contains a figure, the property name is
    # whatever follows it -- the earlier text belongs to a previous claim.
    last = None
    for m in _TRAILING_MEASURE.finditer(text):
        if m.group(0).strip(" ()"):
            last = m
    if last is not None and len(text) - last.end() > 3:
        text = text[last.end():]
    text = re.sub(r"\s+", " ", text).strip(" \t-–—:;,.()[]")
    # Keep only the last clause -- "In FY24, revenue from operations" -> the tail.
    for sep in (";", " but ", " while ", " whereas ", " although ", ", and ", " and ", ", "):
        if sep in text:
            tail = text.rsplit(sep, 1)[-1].strip()
            if len(tail) >= 3:
                text = tail
    text = _FOOTNOTE.sub("", text)          # drop trailing footnote markers
    text = re.sub(r"^[^A-Za-z]*", "", text)
    words = text.split()
    while words and (words[0].lower().strip(".,") in LEADING_FILLER
                     or words[0].lower().strip(".,") in CLAUSE_OPENERS):
        words.pop(0)
    while words and words[-1].lower().strip(".,") in TRAILING_FILLER:
        words.pop()
    return " ".join(words).strip(" -–—:;,.")


def _plausible_predicate(text: str) -> bool:
    if not (2 <= len(text) <= 80):
        return False
    if text.count("(") != text.count(")") or text.count("[") != text.count("]"):
        return False              # a fragment sliced out of a longer phrase
    if re.search(r"\d", text) and re.search(rf"\d\s*(?:{_PERCENT}|{_MAGNITUDE})", text, re.I):
        return False              # still carries a measurement: leaked clause
    if not re.search(r"[A-Za-z]{3}", text):
        return False
    if not period_signature(text).empty and len(text.split()) <= 3:
        return False              # the "label" is really just a date
    letters = sum(c.isalpha() for c in text)
    return letters >= len(text) * 0.45


# --------------------------------------------------------------------------
# context detection
# --------------------------------------------------------------------------
_PERIOD_RE = re.compile(
    r"\b(?:FY\s?-?'?\s?\d{2,4}(?:\s*-\s*\d{2,4})?"
    r"|(?:Q[1-4]|H[12])\s*(?:FY)?\s*\d{2,4}(?:\s*-\s*\d{2,4})?"
    r"|(?:fiscal|financial)\s+year\s+\d{4}(?:\s*[-/]\s*\d{2,4})?"
    r"|(?:19|20)\d{2}\s*[-/]\s*\d{2,4}"
    r"|(?:January|February|March|April|May|June|July|August|September|October"
    r"|November|December)\s+(?:19|20)\d{2}"
    r"|(?:19|20)\d{2})\b", re.I)


def find_period(text: str) -> str | None:
    hits = _PERIOD_RE.findall(text) if False else [m.group(0) for m in _PERIOD_RE.finditer(text)]
    if not hits:
        return None
    # Prefer the most specific expression (quarters and fiscal spans beat bare years).
    hits.sort(key=lambda h: (len(h), any(c in h.lower() for c in ("q", "fy", "-"))), reverse=True)
    return re.sub(r"\s+", " ", hits[0]).strip()


def _lookup_terms(text: str, table: dict[str, str]) -> str | None:
    low = text.lower()
    found = [v for k, v in table.items() if re.search(rf"\b{re.escape(k)}\b", low)]
    return found[0] if found else None


def find_attribution(text: str) -> str | None:
    for pat in ATTRIBUTION_PATTERNS:
        m = re.search(pat, text)
        if m:
            name = re.sub(r"\s+", " ", m.group(1)).strip(" ,.")
            if 2 < len(name) <= 60:
                return name
    return None


def build_context(text: str, *, extra: str = "", period: str | None = None) -> ClaimContext:
    """Read qualifiers out of the sentence and any inherited context."""
    both = f"{text} {extra}"
    return ClaimContext(
        period=period or find_period(text) or find_period(extra),
        scope=_lookup_terms(both, SCOPE_TERMS),
        basis=_lookup_terms(both, BASIS_TERMS),
        as_of=None,
        geography=None,
        denominator=None,
        other_qualifiers=None,
    )


def find_modality(text: str) -> str:
    hit = _lookup_terms(text, MODALITY_TERMS)
    return hit or "reported"


# --------------------------------------------------------------------------
# entity detection
# --------------------------------------------------------------------------
def detect_entities(document: Document, sample_pages: int = 25,
                    top_n: int = 12) -> tuple[str | None, set[str]]:
    """Pick the entity a document is mostly about, from the text alone.

    Frequency-driven: the most repeated proper-noun phrase wins. Organisation
    suffixes only *boost* a candidate, never gate it, so documents about a
    country or a person work the same way.
    """
    furniture = repeated_line_texts(document)
    counts: Counter[str] = Counter()
    pages_seen: dict[str, set[int]] = {}
    for page in document.pages[:sample_pages]:
        for line in page.lines:
            if line.text.strip() in furniture:
                continue          # header/footer/watermark, not content
            for m in _PROPER.finditer(line.text):
                # Capitalisation only signals a proper noun when it is NOT
                # explained by position. "However", "Revenue" and "Offer" are
                # capitalised because they start a sentence or a table row;
                # "Delhivery" is capitalised wherever it appears. Counting only
                # mid-sentence occurrences separates the two without needing a
                # dictionary of English words.
                prefix = line.text[:m.start()].rstrip()
                if not prefix or prefix[-1] in ".!?:;•|":
                    continue
                phrase = re.sub(r"\s+", " ", m.group(1)).strip(" .,")
                # A match must not run across a sentence boundary, or footer
                # text like "Monetary Fund. Not for Redistribution" becomes an entity.
                phrase = re.split(r"\.\s", phrase)[0].strip(" .,")
                # "India's" and "India" are the same entity; a possessive is a
                # grammatical form, not a different name.
                phrase = re.sub(r"[\u2019']s$", "", phrase).strip(" .,")
                low = phrase.lower()
                # A bare generic referent ("Company", "Bank", "Group") names no
                # one in particular -- it is a reference, not an entity.
                if len(low.split()) == 1 and low.strip(".") in ORG_SUFFIXES:
                    continue
                if low in ENTITY_STOPWORDS or len(phrase) < 4 or len(phrase) > 60:
                    continue
                if any(w in ENTITY_STOPWORDS for w in low.split()) and len(low.split()) <= 2:
                    continue
                if not re.search(r"[a-z]{3}", phrase):
                    continue      # all-caps abbreviations are too ambiguous alone
                counts[phrase] += 1
                pages_seen.setdefault(phrase, set()).add(page.number)
    if not counts:
        return (None, set())

    def score(item: tuple[str, int]) -> float:
        """Spread across pages beats raw frequency.

        A document's subject is mentioned throughout it. Terms that pile up on
        a cover page or in a definitions section score high on raw count but
        appear on few pages, so page spread is the stronger signal.
        """
        phrase, n = item
        words = phrase.lower().split()
        spread = len(pages_seen.get(phrase, ()))
        s = float(n) * (1.0 + spread)
        if any(w.strip(".") in ORG_SUFFIXES for w in words):
            s *= 2.5
        if len(words) >= 2:
            s *= 1.3
        return s

    ranked = sorted(counts.items(), key=score, reverse=True)
    best = ranked[0]
    # Prefer the longest frequent form ("Delhivery" -> "Delhivery Limited").
    head = best[0]
    for phrase, n in counts.items():
        if phrase != head and phrase.lower().startswith(head.lower()) \
                and n >= best[1] * 0.25 and len(phrase) > len(head):
            head = phrase
    # The vocabulary of entities this document actually discusses. A claim may
    # only take a subject from this set; any other capitalised word in the
    # sentence is far more likely to be a stray ("Global", "Core", "Chart I.42")
    # than a real actor, and letting those through fragments the entity
    # clusters that cross-document comparison depends on.
    known = {p for p, _ in ranked[:top_n]}
    known.add(head)
    return (head, known)


def detect_primary_entity(document: Document, sample_pages: int = 25) -> str | None:
    return detect_entities(document, sample_pages)[0]


def _subject_for(sentence: str, label: str, primary: str | None,
                 known: set[str] | None = None) -> tuple[str, str]:
    """Split an entity off the front of a label if one is there.

    "Delhivery Limited revenue" -> ("Delhivery Limited", "revenue").
    Otherwise fall back to a proper noun in the sentence, then the document's
    primary entity.
    """
    label = re.sub(r"\s+", " ", label).strip()
    m = _PROPER.match(label)
    if m and len(m.group(1)) < len(label) - 2 and known:
        candidate = m.group(1).strip()
        # Split an entity off the label only if it is a name this document uses.
        if any(candidate.lower() == k.lower() for k in known):
            rest = _clean_label(label[m.end():])
            if _plausible_predicate(rest):
                return (candidate, rest)
    if primary and primary.lower() in sentence.lower():
        return (primary, label)
    # Only a name the document actually uses as an entity may override the
    # document's primary subject.
    if known:
        low = sentence.lower()
        matches = [k for k in known if k.lower() in low]
        if matches:
            return (max(matches, key=len), label)
    return (primary or "(unspecified entity)", label)


# --------------------------------------------------------------------------
# sentence extraction
# --------------------------------------------------------------------------
def _iter_sentences(raw: str):
    pos = 0
    for m in _SENT_SPLIT.finditer(raw):
        chunk = raw[pos:m.start()]
        if chunk.strip():
            yield pos, chunk
        pos = m.end()
    if raw[pos:].strip():
        yield pos, raw[pos:]


def _skip_value(raw: str, start: int, value_text: str) -> bool:
    """Reject numbers that are references or bare years rather than measurements."""
    stripped = value_text.strip().strip(".,;:)")
    if _YEARISH.match(stripped):
        return True
    if _ORDINAL_CTX.search(raw[max(0, start - 14):start]):
        return True
    parsed = parse_value(stripped)
    return parsed.base_number is None


def sentence_claims(document: Document, page: Page, primary: str | None,
                    furniture: set[str] | None = None,
                    known: set[str] | None = None) -> list[Claim]:
    claims: list[Claim] = []
    raw = page.raw
    furniture = furniture or set()
    for sent_start, sentence in _iter_sentences(raw):
        if len(sentence) > 600:
            continue
        if sentence.strip() in furniture:
            continue                     # repeated header/footer, not content
        for vm in VALUE_RE.finditer(sentence):
            v_start, v_end = vm.span("value")
            value_text = vm.group("value").strip()
            if _skip_value(sentence, v_start, value_text):
                continue

            before = sentence[:v_start]
            # A level statement ("revenue was 100") and a change statement
            # ("revenue increased by 100") assert different things. Treating
            # them alike would compare a delta against a level and report a
            # contradiction, so the change form gets its own predicate suffix.
            is_change = False
            link = _LINK_RE.search(before)
            if link is None:
                link = _CHANGE_RE.search(before)
                is_change = link is not None
            if not link:
                continue
            label_raw = before[:link.start()]
            label = _clean_label(label_raw)
            if not _plausible_predicate(label):
                continue

            subject, predicate = _subject_for(sentence, label, primary, known)
            if not _plausible_predicate(predicate):
                continue
            if is_change:
                predicate = f"{predicate} (change)"
            rule = "label+change_verb+value" if is_change else "label+linking_phrase+value"

            abs_label_start = sent_start + (len(label_raw) - len(label_raw.lstrip()))
            abs_start = sent_start + max(0, link.start() - len(label))
            abs_start = max(sent_start, min(abs_start, sent_start + link.start()))
            abs_end = sent_start + v_end
            evidence = raw[abs_start:abs_end].strip()
            if len(evidence) < 6:
                continue

            heading = page.heading_before(abs_start) or ""
            ctx = build_context(sentence, extra=heading)
            parsed = parse_value(value_text)
            ctx.unit = _unit_label(parsed) or ctx.unit
            if is_change:
                ctx.basis = ctx.basis or "change over prior period"

            confidence = 0.82 if ctx.period else 0.68
            if parsed.is_percent or parsed.currency or parsed.scale != 1.0:
                confidence += 0.05

            claims.append(Claim(
                subject=subject, predicate=predicate.lower(), value=value_text,
                value_num=parsed.number, unit=_unit_label(parsed),
                context=ctx,
                asserted_by=find_attribution(sentence),
                modality=find_modality(sentence),
                source_document=document.filename,
                source_span=SourceSpan(
                    page=page.number, text=evidence,
                    char_span=(abs_start, abs_end),
                    bbox=page.bbox_for_chars(abs_start, abs_end)),
                origin="sentence", extraction_rule=rule,
                confidence=min(0.95, confidence),
            ))
    return claims


def _unit_label(parsed) -> str | None:
    """Canonical unit string built from what the value itself carries."""
    if parsed.is_percent:
        return "percent"
    bits = []
    if parsed.currency:
        bits.append(parsed.currency)
    scale_names = {1e3: "thousand", 1e5: "lakh", 1e6: "million",
                   1e7: "crore", 1e9: "billion", 1e12: "trillion"}
    if parsed.scale in scale_names:
        bits.append(scale_names[parsed.scale])
    return " ".join(bits) if bits else None


# --------------------------------------------------------------------------
# table extraction
# --------------------------------------------------------------------------
def _find_line_containing(page: Page, needle: str, bbox=None):
    """Locate a cell's text among the page's lines, to recover real coordinates."""
    needle = needle.strip()
    if not needle:
        return None
    for ln in page.lines:
        if needle in ln.text:
            if bbox and not (ln.bbox[0] >= bbox[0] - 4 and ln.bbox[2] <= bbox[2] + 4
                             and ln.bbox[1] >= bbox[1] - 4 and ln.bbox[3] <= bbox[3] + 4):
                continue
            return ln
    return None


def table_claims(document: Document, page: Page, table: Table,
                 primary: str | None, known: set[str] | None = None) -> list[Claim]:
    claims: list[Claim] = []
    heading = page.lines and page.heading_before(page.lines[0].char_start) or ""
    caption_ctx = f"{table.caption or ''} {heading or ''}"

    for row in table.rows:
        if not row:
            continue
        label = _clean_label(str(row[table.label_col] or ""))
        if not _plausible_predicate(label):
            continue
        # A unit declared in the row label applies to that row only.
        row_unit = None
        paren = re.search(r"[(\[]([^)\]]{1,30})[)\]]", str(row[table.label_col] or ""))
        if paren:
            pv = parse_value(paren.group(1))
            if pv.currency or pv.scale != 1.0 or pv.is_percent:
                row_unit = paren.group(1).strip()

        for col, cell in enumerate(row):
            if col == table.label_col:
                continue
            cell_text = str(cell or "").strip()
            if not cell_text:
                continue
            parsed = parse_value(cell_text)
            if parsed.base_number is None:
                continue

            header = table.header[col] if col < len(table.header) else ""
            is_period_col = column_is_period(header)

            unit_source = row_unit or table.unit_hint or ""
            unit_parsed = parse_value(f"{unit_source} {cell_text}")
            scale = unit_parsed.scale if parsed.scale == 1.0 else parsed.scale
            value_num = (parsed.base_number * scale) if parsed.base_number is not None else None
            if parsed.is_percent or unit_parsed.is_percent:
                unit = "percent"
                value_num = parsed.base_number
            else:
                unit = _unit_label(unit_parsed) or (unit_source or None)

            # Unlike a sentence, a table row label is already just the property
            # name -- splitting a capitalised first word off it produced
            # subject="Revenue", predicate="from customers". The subject is the
            # document's entity unless the row label names a different one.
            subject = primary or "(unspecified entity)"
            predicate = label
            if not _plausible_predicate(predicate):
                continue

            ctx = build_context(
                f"{label} {header} {caption_ctx}",
                period=(header if is_period_col else None) or find_period(caption_ctx),
            )
            ctx.unit = unit
            if not is_period_col and header:
                ctx.scope = ctx.scope or header

            line = (_find_line_containing(page, cell_text, table.bbox)
                    or _find_line_containing(page, cell_text))
            label_line = _find_line_containing(page, str(row[table.label_col] or "").strip())

            # Both halves of the claim must actually be present on the page.
            if cell_text not in page.raw or label.split()[0] not in page.raw:
                continue

            bbox = line.bbox if line else table.bbox
            char_span = (line.char_start, line.char_end) if line else None
            evidence = (f"{str(row[table.label_col]).strip()} | "
                        f"{header.strip()} | {cell_text}") if header else \
                       f"{str(row[table.label_col]).strip()} | {cell_text}"

            confidence = 0.72
            if is_period_col:
                confidence += 0.10
            if unit:
                confidence += 0.06
            confidence *= min(1.0, 0.6 + table.quality)

            claims.append(Claim(
                subject=subject, predicate=predicate.lower(), value=cell_text,
                value_num=value_num, unit=unit, context=ctx,
                asserted_by=None, modality=find_modality(f"{label} {header} {caption_ctx}"),
                source_document=document.filename,
                source_span=SourceSpan(page=page.number, text=evidence,
                                       char_span=char_span, bbox=bbox),
                origin="table",
                extraction_rule=f"table[{table.source}] row_label+column_header+cell",
                confidence=round(min(0.95, confidence), 3),
            ))
    return claims
