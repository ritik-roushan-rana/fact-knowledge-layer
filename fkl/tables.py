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
# Every table claim needs a numeric cell, so a grid with almost none cannot
# produce one. The case this rejects is a multi-column page layout: the gutter
# between text columns looks exactly like a column separator, so prose gets
# parsed as a wide, well-formed, entirely non-numeric "table".
MIN_NUMERIC_DENSITY = 0.10
# Data cells are short. A grid whose cells average sentence length is prose.
MAX_MEAN_CELL_CHARS = 45


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
_FOOTNOTE_MARKER = re.compile(r"^\(?\d{1,2}\)?\s*/?$")


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

    non_empty = [c for c in cells if c.strip()]
    mean_len = sum(len(c) for c in non_empty) / max(1, len(non_empty))
    if numeric < MIN_NUMERIC_DENSITY or mean_len > MAX_MEAN_CELL_CHARS:
        return 0.0

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
def _is_data_cell(text: str) -> bool:
    """A measurement, as opposed to a header that happens to contain digits.

    Year and fiscal-span headers ("2021/22", "2023-24") parse as numbers, so
    testing for "is a number" alone stops header detection at the very row that
    names the periods -- which then becomes data and every claim inherits the
    wrong period.
    """
    if not _is_number(text):
        return False
    # Footnote markers ("1/", "2/", "(3)") sit in header rows and parse as
    # numbers. Treating one as data stops header detection at the row above the
    # one that names the periods.
    if _FOOTNOTE_MARKER.match(text.strip()):
        return False
    return period_signature(text).empty


def split_header(grid: list[list[str]]) -> tuple[list[str], list[list[str]], int]:
    """Merge the leading header rows into one header per column.

    Multi-row headers are common ("Average" / "2003-04" / "to" / "2007-08" in
    four stacked rows, or a title row above a row of years above a row of
    "Est./Projections"). A leading row counts as header until one of its
    non-label cells holds an actual measurement.
    """
    n_head = 0
    for row in grid:
        body = row[1:] if len(row) > 1 else row
        if any(_is_data_cell(c) for c in body):
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

    merged: list[str] = []
    for col in range(width):
        parts = []
        for hr in header_rows:
            cell = _clean(hr[col]) if col < len(hr) else ""
            if cell and cell not in parts:
                parts.append(cell)
        merged.append(" ".join(parts).strip())

    # When one header row names the periods, that row IS the column header.
    # Merging it with a spanning title row above ("Table 1. India: Selected
    # Economic Indicators, 2021/22-2026/27") would otherwise leak the title's
    # date range into every column and override the real per-column period.
    period_counts = [sum(1 for c in hr[1:] if not period_signature(c).empty)
                     for hr in header_rows]
    header = merged
    if period_counts and max(period_counts) >= 2:
        primary = header_rows[period_counts.index(max(period_counts))]
        header = [
            _clean(primary[i]) if i < len(primary) and _clean(primary[i]) else merged[i]
            for i in range(width)
        ]

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
    @staticmethod
    def _word_grid(page) -> list[tuple[list[list[str]], BBox, str]]:
        """Build grids from word coordinates alone.

        Ruled-line and text-alignment strategies both fail on wide statistical
        tables: they over-segment into a sparse grid where most cells are
        empty. The words themselves carry the structure.

        The page is not treated as one grid. A table is found first as a *band*
        of consecutive rows that carry several numbers, and columns are then
        derived from the words in that band alone. Doing it the other way round
        reads a two-column page layout as a two-column table, because the
        gutter between text columns looks exactly like a column separator.
        """
        try:
            words = page.get_text("words")
        except Exception:
            return []
        words = [w for w in words if str(w[4]).strip()]
        if len(words) < 12:
            return []

        heights = sorted(w[3] - w[1] for w in words)
        line_h = heights[len(heights) // 2] or 8.0
        tol = max(1.5, line_h * 0.6)

        # --- rows: cluster on vertical centre ---
        rows: list[list] = []
        for w in sorted(words, key=lambda w: ((w[1] + w[3]) / 2, w[0])):
            centre = (w[1] + w[3]) / 2
            if rows and abs(centre - rows[-1][0]) <= tol:
                rows[-1][1].append(w)
            else:
                rows.append([centre, [w]])
        row_words = [r[1] for r in rows]
        if len(row_words) < MIN_ROWS + 1:
            return []

        # --- bands: runs of rows carrying several figures ---
        def numeric_count(row) -> int:
            return sum(1 for w in row if _is_number(str(w[4])))

        counts = [numeric_count(r) for r in row_words]
        bands, run = [], []
        gap = 0
        for i, n in enumerate(counts):
            if n >= 2:
                run.append(i)
                gap = 0
            elif run:
                gap += 1
                if gap > 1:            # one non-numeric row inside a table is fine
                    bands.append(run)
                    run, gap = [], 0
                else:
                    run.append(i)
        if run:
            bands.append(run)

        out: list[tuple[list[list[str]], BBox, str]] = []
        for band in bands:
            data_rows = [i for i in band if counts[i] >= 2]
            if len(data_rows) < MIN_ROWS:
                continue
            # Take up to three preceding rows as a possible header.
            first = band[0]
            header_start = max(0, first - 3)
            indices = list(range(header_start, band[-1] + 1))
            grid = TableExtractor._grid_for(
                [row_words[i] for i in indices], line_h)
            if grid is None:
                continue
            cells, bbox = grid
            if len(cells) >= MIN_ROWS + 1 and len(cells[0]) >= MIN_COLS:
                out.append((cells, bbox, "word-grid"))
        return out

    @staticmethod
    def _grid_for(band_rows: list[list], line_h: float):
        """Columns are derived from the words of one band, not the whole page."""
        words = [w for row in band_rows for w in row]
        if not words:
            return None
        x0 = min(w[0] for w in words)
        x1 = max(w[2] for w in words)
        span = int(x1 - x0) + 2
        if span <= 4:
            return None

        # Occupancy per row, so a single wide caption cannot erase every gap.
        occupied = [0] * span
        for row in band_rows:
            covered: set[int] = set()
            for w in row:
                covered.update(range(max(0, int(w[0] - x0)),
                                     min(span, int(w[2] - x0) + 1)))
            for x in covered:
                occupied[x] += 1

        threshold = max(0, int(len(band_rows) * 0.10))
        min_gap = max(3, int(line_h * 0.5))
        separators, run_start = [], None
        for x in range(span):
            if occupied[x] <= threshold:
                run_start = x if run_start is None else run_start
            else:
                if run_start is not None and x - run_start >= min_gap:
                    separators.append((run_start + x) / 2 + x0)
                run_start = None
        if not separators:
            return None

        bounds = [x0 - 1] + separators + [x1 + 1]
        n_cols = len(bounds) - 1
        if not (MIN_COLS <= n_cols <= 24):
            return None

        def column_of(w) -> int:
            centre = (w[0] + w[2]) / 2
            for i in range(n_cols):
                if bounds[i] <= centre < bounds[i + 1]:
                    return i
            return n_cols - 1

        grid: list[list[str]] = []
        for row in band_rows:
            cells = [[] for _ in range(n_cols)]
            for w in sorted(row, key=lambda w: w[0]):
                cells[column_of(w)].append(str(w[4]))
            built = [_clean(" ".join(c)) for c in cells]
            if sum(1 for c in built if c) >= 2:
                grid.append(built)

        bbox = (x0, min(w[1] for w in words), x1, max(w[3] for w in words))
        return (grid, bbox) if grid else None

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

        # Only pay for the slower strategies when the fast path found nothing good.
        if best_so_far < 0.62:
            if self.use_plumber:
                candidates += self._plumber_tables(page_number - 1)
            candidates += self._word_grid(pymupdf_page)

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
