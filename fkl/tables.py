"""Deterministic table reconstruction.

No single extractor handles every PDF. PyMuPDF's ruled-line finder is fast and
excellent on real grid tables but returns near-empty boxes on chart-heavy
slides and merges rows on some appendix layouts; pdfplumber's text-alignment
strategy recovers exactly those. So several strategies are run, each result is
scored on structural quality, overlapping candidates are deduplicated, and the
best one wins.

The scoring is structural only -- cell fill rate, whether a header row and a
label column exist, how many cells are numeric. It never looks at what the
table is about, so it behaves the same on a logistics deck and a central-bank
appendix.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from .normalize import parse_value, period_signature

log = logging.getLogger("fkl.tables")

BBox = tuple[float, float, float, float]

# A table needs at least this much structure to be worth reading.
MIN_ROWS, MIN_COLS = 2, 2
MIN_QUALITY = 0.34


@dataclass
class Table:
    page: int
    bbox: BBox
    header: list[str]                 # merged column headers, one per column
    rows: list[list[str]]             # data rows only (headers removed)
    label_col: int = 0
    caption: str | None = None
    unit_hint: str | None = None
    source: str = ""
    quality: float = 0.0
    n_header_rows: int = 1

    @property
    def shape(self) -> tuple[int, int]:
        return (len(self.rows), len(self.header))


# --------------------------------------------------------------------------
# scoring
# --------------------------------------------------------------------------
_NUM_TOKEN = re.compile(r"(?<![\w.])[-+(]?\d[\d,]*(?:\.\d+)?\)?(?![\w])")


def _is_number(text: str) -> bool:
    if not text or not text.strip():
        return False
    return parse_value(text).base_number is not None


def _clean(cell) -> str:
    return re.sub(r"\s+", " ", str(cell or "")).strip()


def score_grid(grid: list[list[str]]) -> float:
    """Structural quality in [0, 1]. Higher means more table-like."""
    if len(grid) < MIN_ROWS or not grid or len(grid[0]) < MIN_COLS:
        return 0.0
    cells = [c for row in grid for c in row]
    if not cells:
        return 0.0

    filled = sum(1 for c in cells if c.strip()) / len(cells)
    numeric = sum(1 for c in cells if _is_number(c)) / len(cells)

    # A usable table has a mostly-textual first row and first column.
    head = grid[0]
    head_textual = sum(1 for c in head if c.strip() and not _is_number(c)) / max(1, len(head))
    col0 = [r[0] for r in grid[1:] if r]
    label_textual = (sum(1 for c in col0 if c.strip() and not _is_number(c)) / max(1, len(col0)))

    # Merged-cell detection. Whitespace has already been collapsed by the time
    # cells reach here, so a literal newline is not a reliable signal. What does
    # survive cleaning is a cell carrying many separate numbers, or one holding
    # far more text than a cell should -- both mean several real rows were
    # collapsed into one, which silently destroys row/value alignment.
    merged = 0
    for c in cells:
        text = str(c)
        if len(text) > 90 or len(_NUM_TOKEN.findall(text)) > 2:
            merged += 1
    merged_ratio = merged / len(cells)

    return max(0.0, min(1.0,
        0.30 * filled + 0.25 * numeric + 0.20 * head_textual
        + 0.25 * label_textual - 1.20 * merged_ratio))


def _iou(a: BBox, b: BBox) -> float:
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    inter = (x1 - x0) * (y1 - y0)
    area = ((a[2] - a[0]) * (a[3] - a[1])) + ((b[2] - b[0]) * (b[3] - b[1])) - inter
    return inter / area if area > 0 else 0.0


# --------------------------------------------------------------------------
# header handling
# --------------------------------------------------------------------------
def split_header(grid: list[list[str]]) -> tuple[list[str], list[list[str]], int]:
    """Merge the leading header rows into one header per column.

    Multi-row headers are common ("Average" / "2003-04" / "to" / "2007-08" in
    four stacked rows). A leading row counts as header while none of its
    non-label cells parse as a number.
    """
    n_head = 0
    for row in grid:
        body = row[1:] if len(row) > 1 else row
        if any(_is_number(c) for c in body):
            break
        if not any(str(c).strip() for c in row):
            n_head += 1
            continue
        n_head += 1
        if n_head >= 4:            # guard against a table that is all text
            break

    if n_head == 0 or n_head >= len(grid):
        n_head = 1
    header_rows = grid[:n_head]
    width = max(len(r) for r in grid)

    header: list[str] = []
    for col in range(width):
        parts = []
        for hr in header_rows:
            cell = _clean(hr[col]) if col < len(hr) else ""
            if cell and cell not in parts:
                parts.append(cell)
        header.append(" ".join(parts).strip())

    body = [r for r in grid[n_head:] if any(str(c).strip() for c in r)]
    return header, body, n_head


_UNIT_PAREN = re.compile(
    r"[(\[]([^)\]]{1,40})[)\]]|(?:^|\s)(?:in|₹|rs\.?|inr|usd|\$)\s*"
    r"(million|billion|crore|lakh|thousand|trillion|mn|bn|cr|per cent|percent|%)",
    re.I)


def find_unit_hint(header: list[str], caption: str | None) -> str | None:
    """Units are usually declared once -- in the corner cell, the caption, or a
    column header -- and then inherited by every cell below."""
    candidates: list[str] = []
    if header:
        candidates.append(header[0])
    if caption:
        candidates.append(caption)
    candidates.extend(header[1:])
    for text in candidates:
        if not text:
            continue
        parsed = parse_value(text)
        if parsed.currency or parsed.scale != 1.0 or parsed.is_percent:
            m = _UNIT_PAREN.search(text)
            if m:
                return _clean(m.group(1) or m.group(0))
            return _clean(text)[:40]
    return None


# --------------------------------------------------------------------------
# extraction
# --------------------------------------------------------------------------
class TableExtractor:
    """Runs the available strategies for one document and picks the best result."""

    def __init__(self, path: str | Path, use_plumber: bool = True):
        self.path = str(path)
        self.use_plumber = use_plumber
        self._plumber = None
        self._plumber_failed = False

    def _plumber_doc(self):
        if self._plumber is None and not self._plumber_failed:
            try:
                import pdfplumber
                self._plumber = pdfplumber.open(self.path)
            except Exception as e:
                log.warning("pdfplumber unavailable (%s); using PyMuPDF tables only", e)
                self._plumber_failed = True
        return self._plumber

    def close(self) -> None:
        if self._plumber is not None:
            try:
                self._plumber.close()
            except Exception:
                pass
            self._plumber = None

    # -- individual strategies -------------------------------------------
    def _pymupdf_tables(self, page) -> list[tuple[list[list[str]], BBox, str]]:
        out = []
        try:
            found = page.find_tables()
        except Exception:
            return out
        for t in getattr(found, "tables", []):
            try:
                grid = [[_clean(c) for c in row] for row in t.extract()]
            except Exception:
                continue
            if grid:
                out.append((grid, tuple(t.bbox), "pymupdf"))
        return out

    def _plumber_tables(self, page_index: int) -> list[tuple[list[list[str]], BBox, str]]:
        doc = self._plumber_doc()
        if doc is None or page_index >= len(doc.pages):
            return []
        page = doc.pages[page_index]
        out = []
        for name, settings in (
            ("plumber-lines", {"vertical_strategy": "lines", "horizontal_strategy": "lines"}),
            ("plumber-mixed", {"vertical_strategy": "lines", "horizontal_strategy": "text"}),
        ):
            try:
                found = page.find_tables(settings)
            except Exception:
                continue
            for t in found:
                try:
                    grid = [[_clean(c) for c in row] for row in t.extract()]
                except Exception:
                    continue
                if grid:
                    out.append((grid, tuple(t.bbox), name))
        return out

    # -- public ------------------------------------------------------------
    def tables_for_page(self, pymupdf_page, page_number: int,
                        caption_lookup=None) -> list[Table]:
        """Best-scoring, non-overlapping tables on one page."""
        candidates = self._pymupdf_tables(pymupdf_page)
        best_so_far = max((score_grid(g) for g, _, _ in candidates), default=0.0)

        # Only pay for pdfplumber when the fast path did not find good structure.
        if self.use_plumber and best_so_far < 0.62:
            candidates += self._plumber_tables(page_number - 1)

        scored = []
        for grid, bbox, source in candidates:
            q = score_grid(grid)
            if q >= MIN_QUALITY:
                scored.append((q, grid, bbox, source))
        scored.sort(key=lambda x: -x[0])

        chosen: list[Table] = []
        for q, grid, bbox, source in scored:
            if any(_iou(bbox, t.bbox) > 0.35 for t in chosen):
                continue          # same region already covered by a better result
            header, body, n_head = split_header(grid)
            if not body or len(header) < MIN_COLS:
                continue
            # A table whose column headers are mostly blank cannot qualify its
            # cells with a period or scope, so its claims would be unusable.
            # Better to reject it and let sentence extraction handle the page.
            named = sum(1 for h in header if h.strip())
            if named / max(1, len(header)) < 0.5:
                continue
            caption = caption_lookup(bbox) if caption_lookup else None
            chosen.append(Table(
                page=page_number, bbox=bbox, header=header, rows=body,
                caption=caption, unit_hint=find_unit_hint(header, caption),
                source=source, quality=round(q, 3), n_header_rows=n_head,
            ))
        return chosen


def column_is_period(header_cell: str) -> bool:
    """Does this column header name a time period?  Used to decide whether a
    column contributes context.period or context.scope."""
    return not period_signature(header_cell).empty
