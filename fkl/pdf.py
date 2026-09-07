"""PDF -> pages -> normalized text with an offset map back to the original.

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


@dataclass
class Page:
    number: int              # 1-based PDF page index -- always reliable
    label: str | None        # printed label if the PDF declares one
    raw: str
    norm: str = field(repr=False, default="")
    norm_to_raw: list[int] = field(repr=False, default_factory=list)

    def raw_slice(self, norm_start: int, norm_end: int) -> str:
        """Map a normalized-text span back to the original characters."""
        if not self.norm_to_raw:
            return ""
        norm_start = max(0, min(norm_start, len(self.norm_to_raw) - 1))
        norm_end = max(norm_start + 1, min(norm_end, len(self.norm_to_raw)))
        start = self.norm_to_raw[norm_start]
        end = self.norm_to_raw[norm_end - 1] + 1
        return self.raw[start:end].strip()


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


def file_id(path: Path) -> str:
    """Content-addressed id, so re-uploading the same file is detectable."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()[:16]


def load_pdf(path: str | Path, max_pages: int | None = None) -> Document:
    path = Path(path)
    doc = pymupdf.open(path)
    pages: list[Page] = []
    limit = doc.page_count if max_pages is None else min(max_pages, doc.page_count)
    for i in range(limit):
        pg = doc[i]
        raw = pg.get_text("text")
        try:
            label = pg.get_label() or None
        except Exception:
            label = None
        norm, mapping = normalize_with_map(raw)
        pages.append(Page(number=i + 1, label=label, raw=raw, norm=norm, norm_to_raw=mapping))
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
