"""PDF -> structured pages: text, lines, coordinates, headings, offset map.

Grounding is only trustworthy if we can go from "fuzzy match found here" back to
the *exact* characters in the document. So every page keeps two parallel views:

    raw   : the text as PyMuPDF extracted it
    norm  : a matching-friendly version (unicode-folded, dehyphenated,
            whitespace-collapsed, lowercased)

plus ``norm_to_raw[i]`` = index in ``raw`` that produced ``norm[i]``. Fuzzy
matching runs on ``norm``; evidence is sliced out of ``raw``.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

# Characters that are visually equivalent but break exact matching.
_CHAR_FOLD = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"',
    "‐": "-", "‑": "-", "‒": "-", "–": "-",
    "—": "-", "―": "-", "−": "-",
    " ": " ", " ": " ", " ": " ", " ": " ",
    " ": " ", "﻿": "", "​": "", "­": "",
}


def normalize_with_map(raw: str) -> tuple[str, list[int]]:
    """Return (normalized_text, norm_to_raw_index_map)."""
    out: list[str] = []
    idx: list[int] = []

    i = 0
    n = len(raw)
    pending_space = False
    while i < n:
        ch = raw[i]

        # Soft hyphen at a line break: "reve-\nnue" -> "revenue".
        if ch == "-":
            j = i + 1
            while j < n and raw[j] in " \t\r":
                j += 1
            if j < n and raw[j] == "\n":
                k = j + 1
                while k < n and raw[k] in " \t\r":
                    k += 1
                if k < n and raw[k].isalpha():
                    i = k
                    continue

        folded = _CHAR_FOLD.get(ch)
        if folded is None:
            folded = unicodedata.normalize("NFKC", ch)
        if folded == "":
            i += 1
            continue

        if folded.isspace() or folded == " ":
            pending_space = True
            i += 1
            continue

        if pending_space and out:
            out.append(" ")
            idx.append(i)
        pending_space = False

        for c in folded.lower():
            out.append(c)
            idx.append(i)
        i += 1

    return "".join(out), idx


BBox = tuple[float, float, float, float]


@dataclass
class Line:
    """One visual line, with where it sits on the page and in the page's text."""
    text: str
    bbox: BBox
    size: float                  # largest span font size on the line
    bold: bool
    char_start: int              # offset into Page.raw
    char_end: int
    block_no: int
    is_heading: bool = False


def _union(boxes: list[BBox]) -> BBox | None:
    if not boxes:
        return None
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


@dataclass
class Page:
    number: int              # 1-based PDF page index -- always reliable
    label: str | None        # printed label if the PDF declares one
    raw: str
    norm: str = field(repr=False, default="")
    norm_to_raw: list[int] = field(repr=False, default_factory=list)
    lines: list[Line] = field(repr=False, default_factory=list)
    tables: list = field(repr=False, default_factory=list)   # filled by fkl.tables

    def raw_slice(self, norm_start: int, norm_end: int) -> str:
        """Map a normalized-text span back to the original characters."""
        if not self.norm_to_raw:
            return ""
        norm_start = max(0, min(norm_start, len(self.norm_to_raw) - 1))
        norm_end = max(norm_start + 1, min(norm_end, len(self.norm_to_raw)))
        start = self.norm_to_raw[norm_start]
        end = self.norm_to_raw[norm_end - 1] + 1
        return self.raw[start:end].strip()

    def raw_offsets(self, norm_start: int, norm_end: int) -> tuple[int, int]:
        """Same mapping, but returning raw character offsets."""
        if not self.norm_to_raw:
            return (0, 0)
        norm_start = max(0, min(norm_start, len(self.norm_to_raw) - 1))
        norm_end = max(norm_start + 1, min(norm_end, len(self.norm_to_raw)))
        return (self.norm_to_raw[norm_start], self.norm_to_raw[norm_end - 1] + 1)

    def bbox_for_chars(self, start: int, end: int) -> BBox | None:
        """Union of the bounding boxes of every line the raw span touches.

        This is what lets a claim carry coordinates: grounding resolves a quote
        to a character range, and the range resolves back to a region of the page.
        """
        hits = [ln.bbox for ln in self.lines
                if ln.char_start < end and ln.char_end > start]
        return _union(hits)

    def heading_before(self, char_pos: int) -> str | None:
        """Nearest heading at or above a position -- used to inherit context."""
        best = None
        for ln in self.lines:
            if ln.is_heading and ln.char_start <= char_pos:
                best = ln.text
        return best


@dataclass
class Document:
    doc_id: str
    filename: str
    path: Path
    pages: list[Page]
    n_pages: int

    def page(self, number: int) -> Page | None:
        if 1 <= number <= len(self.pages):
            return self.pages[number - 1]
        return None


def repeated_line_texts(document: "Document", min_fraction: float = 0.35) -> set[str]:
    """Text that appears on a large share of pages: headers, footers, watermarks.

    This is page furniture, not content. Excluding it keeps boilerplate out of
    both entity detection and claim extraction -- a purely structural rule that
    needs no knowledge of what the boilerplate says.
    """
    from collections import Counter
    seen: Counter[str] = Counter()
    for page in document.pages:
        for text in {ln.text.strip() for ln in page.lines if len(ln.text.strip()) > 3}:
            seen[text] += 1
    threshold = max(3, int(len(document.pages) * min_fraction))
    return {text for text, n in seen.items() if n >= threshold}


def file_id(path: Path) -> str:
    """Content-addressed id, so re-uploading the same file is detectable."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()[:16]


def _build_page(pg, number: int) -> Page:
    """Assemble a page from PyMuPDF's structured dict.

    Text is rebuilt from the line structure rather than taken from
    get_text("text") so that every character offset in Page.raw maps back to a
    known line, and therefore to a bounding box.
    """
    try:
        data = pg.get_text("dict")
    except Exception:
        raw = pg.get_text("text")
        norm, mapping = normalize_with_map(raw)
        return Page(number=number, label=None, raw=raw, norm=norm, norm_to_raw=mapping)

    parts: list[str] = []
    lines: list[Line] = []
    cursor = 0
    for block in data.get("blocks", []):
        if block.get("type") != 0:        # 0 = text; skip images
            continue
        for ln in block.get("lines", []):
            spans = ln.get("spans", [])
            text = "".join(sp.get("text", "") for sp in spans)
            if not text.strip():
                continue
            size = max((sp.get("size", 0.0) for sp in spans), default=0.0)
            bold = any("bold" in str(sp.get("font", "")).lower() for sp in spans)
            bbox = tuple(ln.get("bbox", (0, 0, 0, 0)))
            lines.append(Line(text=text, bbox=bbox, size=round(size, 2), bold=bold,
                              char_start=cursor, char_end=cursor + len(text),
                              block_no=block.get("number", 0)))
            parts.append(text)
            cursor += len(text) + 1       # +1 for the newline joined below
        parts.append("")                  # blank line between blocks
        cursor += 1

    raw = "\n".join(parts)
    _mark_headings(lines)
    try:
        label = pg.get_label() or None
    except Exception:
        label = None
    norm, mapping = normalize_with_map(raw)
    return Page(number=number, label=label, raw=raw, norm=norm,
                norm_to_raw=mapping, lines=lines)


def _mark_headings(lines: list[Line]) -> None:
    """Flag lines that look structural rather than prose.

    Purely typographic: relatively large or bold, short, and not a row of
    figures. No knowledge of what any particular heading says.
    """
    sizes = sorted(ln.size for ln in lines if ln.size > 0)
    if not sizes:
        return
    median = sizes[len(sizes) // 2]
    for ln in lines:
        text = ln.text.strip()
        if not text or len(text) > 120:
            continue
        digits = sum(c.isdigit() for c in text)
        if digits > len(text) * 0.4:      # a figures row, not a heading
            continue
        larger = ln.size >= median * 1.12
        emphatic = ln.bold and ln.size >= median
        if larger or emphatic:
            ln.is_heading = True


def load_pdf(path: str | Path, max_pages: int | None = None) -> Document:
    path = Path(path)
    doc = pymupdf.open(path)
    pages: list[Page] = []
    limit = doc.page_count if max_pages is None else min(max_pages, doc.page_count)
    for i in range(limit):
        pages.append(_build_page(doc[i], i + 1))
    n_pages = doc.page_count
    doc.close()
    return Document(
        doc_id=file_id(path),
        filename=path.name,
        path=path,
        pages=pages,
        n_pages=n_pages,
    )


@dataclass
class Chunk:
    index: int
    first_page: int
    last_page: int
    text: str          # page-marked text handed to the extractor


def chunk_document(document: Document, chunk_chars: int) -> list[Chunk]:
    """Group whole pages into extraction units, never splitting a page.

    Page boundaries are preserved and labelled so the model can attribute each
    quote to a page without us having to guess afterwards.
    """
    chunks: list[Chunk] = []
    buf: list[str] = []
    buf_len = 0
    first_page = None

    def flush(last_page: int) -> None:
        nonlocal buf, buf_len, first_page
        if buf and first_page is not None:
            chunks.append(
                Chunk(index=len(chunks), first_page=first_page, last_page=last_page, text="".join(buf))
            )
        buf, buf_len, first_page = [], 0, None

    for page in document.pages:
        body = page.raw.strip()
        if not body:
            continue
        block = f"\n<<<PAGE {page.number}>>>\n{body}\n"
        if buf and buf_len + len(block) > chunk_chars:
            flush(prev_page)
        if first_page is None:
            first_page = page.number
        buf.append(block)
        buf_len += len(block)
        prev_page = page.number

    if buf:
        flush(document.pages[-1].number)
    return chunks


_NUM_RE = re.compile(r"\d[\d,. ]*")


def numbers_in(text: str) -> list[str]:
    """Digit sequences, normalised so 1,234.50 and 1234.5 compare equal."""
    out = []
    for m in _NUM_RE.finditer(text):
        s = m.group(0).replace(",", "").replace(" ", "").rstrip(".")
        if not s:
            continue
        if "." in s:
            s = s.rstrip("0").rstrip(".")
        out.append(s or "0")
    return out
