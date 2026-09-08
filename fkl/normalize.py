"""Generic normalisation of values, units and periods.

Everything here is general-language processing -- magnitude words, currency
symbols, percentages, calendar and fiscal-period expressions. None of it knows
anything about a particular document, company, or metric, which is what keeps
the comparison stage portable to unseen PDFs.

The point of this module is that comparing "Rs 8,142 Cr" with "INR 81.42
billion" must be able to conclude *equal*, and comparing "FY24" with "Q4 FY24"
must be able to conclude *different period* -- otherwise every restatement looks
like a contradiction.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# --- magnitude words: general language, not domain vocabulary ---------------
MAGNITUDES: dict[str, float] = {
    "hundred": 1e2,
    "thousand": 1e3, "k": 1e3,
    "lakh": 1e5, "lac": 1e5, "lakhs": 1e5,
    "million": 1e6, "mn": 1e6, "mln": 1e6, "m": 1e6,
    "crore": 1e7, "cr": 1e7, "crores": 1e7,
    "billion": 1e9, "bn": 1e9, "b": 1e9,
    "trillion": 1e12, "tn": 1e12, "tr": 1e12,
}

CURRENCY_SYMBOLS: dict[str, str] = {
    "₹": "INR", "rs": "INR", "rs.": "INR", "inr": "INR", "rupees": "INR", "rupee": "INR",
    "$": "USD", "us$": "USD", "usd": "USD", "dollars": "USD", "dollar": "USD",
    "€": "EUR", "eur": "EUR", "euros": "EUR", "euro": "EUR",
    "£": "GBP", "gbp": "GBP", "pounds": "GBP",
    "¥": "JPY", "jpy": "JPY", "yen": "JPY",
}

_NUMBER_RE = re.compile(r"[-+]?\(?\s*\d[\d,\s]*(?:\.\d+)?\s*\)?")
_WORD_RE = re.compile(r"[a-z₹$€£¥%.]+")


@dataclass
class ParsedValue:
    raw: str
    number: float | None = None          # magnitude-adjusted numeric value
    base_number: float | None = None     # the digits as written, before scaling
    scale: float = 1.0
    currency: str | None = None
    is_percent: bool = False
    is_negative_parens: bool = False
    text: str = ""                       # lowercased text with numbers removed

    @property
    def is_numeric(self) -> bool:
        return self.number is not None


def _clean(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    for src, dst in (("–", "-"), ("—", "-"), ("−", "-"), (" ", " ")):
        text = text.replace(src, dst)
    return text.strip()


def parse_value(raw: str) -> ParsedValue:
    """Pull a comparable number, scale, currency and percent flag out of a value string."""
    cleaned = _clean(raw)
    low = cleaned.lower()
    out = ParsedValue(raw=raw, text=_NUMBER_RE.sub(" ", low).strip())

    m = _NUMBER_RE.search(cleaned)
    if m:
        token = m.group(0).strip()
        negative = token.startswith("(") or (token.startswith("-") and True)
        if token.startswith("(") and token.endswith(")"):
            out.is_negative_parens = True
        digits = token.strip("()+-").replace(",", "").replace(" ", "")
        try:
            value = float(digits)
            if negative or out.is_negative_parens:
                value = -abs(value)
            out.base_number = value
        except ValueError:
            out.base_number = None

    # Scale and currency come from the words around the number.
    for word in _WORD_RE.findall(low):
        bare = word.strip(".")
        if bare in MAGNITUDES and out.scale == 1.0:
            out.scale = MAGNITUDES[bare]
        if bare in CURRENCY_SYMBOLS and out.currency is None:
            out.currency = CURRENCY_SYMBOLS[bare]
    for sym, code in CURRENCY_SYMBOLS.items():
        if len(sym) == 1 and sym in cleaned and out.currency is None:
            out.currency = code
    if "%" in cleaned or "percent" in low or "per cent" in low:
        out.is_percent = True

    if out.base_number is not None:
        out.number = out.base_number * out.scale
    return out


def unit_signature(unit: str | None, value: ParsedValue | None = None) -> dict:
    """Normalise a unit string into comparable parts.

    Returns the currency, the scale multiplier and whether it is a percentage,
    so that "Rs. Cr" and "INR billion" differ only in scale -- a difference that
    is reconcilable rather than contradictory.
    """
    sig = {"currency": None, "scale": 1.0, "percent": False, "raw": unit}
    if unit:
        low = _clean(unit).lower()
        for word in _WORD_RE.findall(low):
            bare = word.strip(".")
            if bare in MAGNITUDES and sig["scale"] == 1.0:
                sig["scale"] = MAGNITUDES[bare]
            if bare in CURRENCY_SYMBOLS and sig["currency"] is None:
                sig["currency"] = CURRENCY_SYMBOLS[bare]
        for sym, code in CURRENCY_SYMBOLS.items():
            if len(sym) == 1 and sym in low and sig["currency"] is None:
                sig["currency"] = code
        if "%" in low or "percent" in low or "per cent" in low:
            sig["percent"] = True
    if value is not None:
        if sig["currency"] is None:
            sig["currency"] = value.currency
        if sig["scale"] == 1.0 and value.scale != 1.0:
            sig["scale"] = value.scale
        sig["percent"] = sig["percent"] or value.is_percent
    return sig


# --- periods ---------------------------------------------------------------
_YEAR_RE = re.compile(r"\b(19|20)(\d{2})\b")
_SHORT_FY_RE = re.compile(r"\bfy\s*[-']?\s*(\d{2,4})\b")
# Both orders occur: "Q1 FY24" and the finance-style "1Q24".
_QUARTER_RE = re.compile(r"\bq([1-4])\b|\b([1-4])q(?:\d{2,4})?\b")
_HALF_RE = re.compile(r"\bh([12])\b")
_MONTHS = ["january", "february", "march", "april", "may", "june", "july",
           "august", "september", "october", "november", "december"]
_MONTH_ABBR = {m[:3]: i + 1 for i, m in enumerate(_MONTHS)}
_RANGE_RE = re.compile(r"\b(\d{4})\s*[-/]\s*(\d{2,4})\b")


@dataclass
class PeriodSignature:
    years: set[int] = field(default_factory=set)
    quarters: set[int] = field(default_factory=set)
    halves: set[int] = field(default_factory=set)
    months: set[int] = field(default_factory=set)
    fiscal: bool = False
    raw: str | None = None

    @property
    def empty(self) -> bool:
        return not (self.years or self.quarters or self.halves or self.months)

    def as_dict(self) -> dict:
        return {"years": sorted(self.years), "quarters": sorted(self.quarters),
                "halves": sorted(self.halves), "months": sorted(self.months),
                "fiscal": self.fiscal, "raw": self.raw}


def period_signature(period: str | None) -> PeriodSignature:
    """Turn a free-text period into comparable calendar components.

    Handles the common ways documents write time -- calendar years, fiscal years
    in long or short form, spans like 2023-24, quarters, halves, month names --
    without assuming any particular document's conventions.
    """
    sig = PeriodSignature(raw=period)
    if not period:
        return sig
    low = _clean(period).lower()

    if "fy" in low or "fiscal" in low or "financial year" in low:
        sig.fiscal = True

    # Spans: "2023-24" / "2023-2024". A span written this way names ONE period
    # by its ending year (FY2023-24 is FY24), so only the end year is recorded.
    # The span text is then removed so its halves are not re-read as two
    # separate standalone years.
    remainder = low
    for m in _RANGE_RE.finditer(low):
        start, end = m.group(1), m.group(2)
        sig.years.add(int(end) if len(end) == 4 else int(start[:2] + end))
        remainder = remainder.replace(m.group(0), " ")

    for m in _YEAR_RE.finditer(remainder):
        sig.years.add(int(m.group(0)))

    for m in _SHORT_FY_RE.finditer(low):
        digits = m.group(1)
        sig.fiscal = True
        if len(digits) == 4:
            sig.years.add(int(digits))
        else:
            n = int(digits)
            sig.years.add(2000 + n if n < 80 else 1900 + n)

    for m in _QUARTER_RE.finditer(low):
        sig.quarters.add(int(m.group(1) or m.group(2)))
    for m in _HALF_RE.finditer(low):
        sig.halves.add(int(m.group(1)))
    for name in _MONTHS:
        if name in low:
            sig.months.add(_MONTHS.index(name) + 1)
    if not sig.months:
        for abbr, num in _MONTH_ABBR.items():
            if re.search(rf"\b{abbr}\b", low):
                sig.months.add(num)
    return sig


def precision_of(value_text: str, scale: float = 1.0) -> float | None:
    """Half a unit of the value's least significant digit, in normalised units.

    Two figures written to different precisions are effectively saying the same
    thing when they agree to the coarser one's rounding. `8,142` under
    ``INR crore`` is written to the nearest whole crore -- its precision is
    ±0.5 crore, or ±5,000,000 in normalised units. `81,415.38` under
    ``INR million`` is written to two decimals of a million, precision
    ±0.005 million. The coarser rounding wins, and any real difference smaller
    than that is at the limit of what the documents claim to know.

    A flat relative tolerance treats both writings the same way, which is fine
    on average but loses this signal exactly where it matters -- when the
    values are near the coarser side's rounding boundary. Returns None for a
    value that carries no digit precision (a bare integer with no decimal
    point). Callers should fall back to a relative tolerance.
    """
    if not value_text:
        return None
    text = _clean(value_text).lstrip("-+()")
    # Strip trailing non-digit tokens (currency, percent, magnitude words).
    m = _NUMBER_RE.search(text)
    if not m:
        return None
    digits = m.group(0).strip().replace(",", "").replace(" ", "")
    if "." in digits:
        _, frac = digits.split(".", 1)
        # Rounding half-unit at the last decimal place.
        half = 0.5 * (10 ** (-len(frac)))
    else:
        # No decimal point: precision is half of the least significant
        # non-zero digit in the integer part. `8,142` -> ±0.5. `8,000` -> ±500
        # (the trailing zeros are ambiguous but the safer read is that they
        # are not significant).
        stripped = digits.rstrip("0")
        trailing_zeros = len(digits) - len(stripped)
        half = 0.5 * (10 ** trailing_zeros)
    return half * scale


def period_precedes(a: str | None, b: str | None) -> int:
    """Order two periods. Returns -1 if a is earlier than b, +1 if later, 0 otherwise.

    Only compares periods that name a specific calendar or fiscal window. Two
    unstated or overlapping periods return 0, so a status claim without a date
    cannot be silently ordered before another.
    """
    sa, sb = period_signature(a), period_signature(b)
    if sa.empty or sb.empty:
        return 0
    year_a = max(sa.years) if sa.years else None
    year_b = max(sb.years) if sb.years else None
    if year_a is not None and year_b is not None and year_a != year_b:
        return -1 if year_a < year_b else 1
    # Same year: quarters and months settle the order.
    if year_a == year_b:
        q_a = max(sa.quarters) if sa.quarters else 0
        q_b = max(sb.quarters) if sb.quarters else 0
        if q_a != q_b:
            return -1 if q_a < q_b else 1
        m_a = max(sa.months) if sa.months else 0
        m_b = max(sb.months) if sb.months else 0
        if m_a != m_b:
            return -1 if m_a < m_b else 1
    return 0


def compare_periods(a: str | None, b: str | None) -> tuple[str, str]:
    """Return (verdict, explanation) where verdict is same / different / unknown."""
    sa, sb = period_signature(a), period_signature(b)
    if sa.empty and sb.empty:
        return ("unknown", "neither claim states a period")
    if sa.empty or sb.empty:
        stated = a if not sa.empty else b
        return ("unknown", f"only one claim states a period ({stated!r})")

    # A quarter and a full year are different windows even in the same year.
    if sa.quarters != sb.quarters or sa.halves != sb.halves:
        return ("different", f"different sub-period: {a!r} vs {b!r}")
    if sa.years and sb.years and sa.years.isdisjoint(sb.years):
        return ("different", f"different years: {a!r} vs {b!r}")
    # A month-level period and a year-level one describe different windows even
    # when the year matches: "September 2025" is not "FY25".
    if sa.months != sb.months:
        return ("different", f"different granularity or month: {a!r} vs {b!r}")
    if sa.years == sb.years and sa.quarters == sb.quarters:
        return ("same", f"same period ({a!r} ~ {b!r})")
    return ("unknown", f"periods overlap but are not identical: {a!r} vs {b!r}")


def compare_values(a: str, b: str, *, rel_tol: float = 0.005,
                   a_scale: float | None = None, b_scale: float | None = None
                   ) -> tuple[str, str, dict]:
    """Compare two value strings.

    Returns (verdict, explanation, detail) with verdict in
    equal / equivalent / differ / incomparable.
    "equivalent" means the same quantity written at a different scale or with
    different formatting -- the case that must not be reported as a conflict.
    """
    pa, pb = parse_value(a), parse_value(b)

    # A value often carries no magnitude word because the unit column states it
    # ("8,142" under a heading of "Rs. crore"). Fall back to the scale declared
    # in context.unit, otherwise identical quantities look wildly different.
    for parsed, override in ((pa, a_scale), (pb, b_scale)):
        if override and parsed.scale == 1.0 and parsed.base_number is not None:
            parsed.scale = override
            parsed.number = parsed.base_number * override

    detail = {
        "a": {"number": pa.number, "scale": pa.scale, "currency": pa.currency, "percent": pa.is_percent},
        "b": {"number": pb.number, "scale": pb.scale, "currency": pb.currency, "percent": pb.is_percent},
    }

    if pa.is_numeric and pb.is_numeric:
        if pa.currency and pb.currency and pa.currency != pb.currency:
            return ("incomparable",
                    f"different currencies ({pa.currency} vs {pb.currency}); not directly comparable",
                    detail)
        if pa.is_percent != pb.is_percent:
            return ("incomparable",
                    "one value is a percentage and the other is not", detail)

        x, y = pa.number, pb.number
        if x == y:
            return ("equal", f"identical values ({x:g})", detail)

        # Rates are compared in percentage points. A relative tolerance would
        # call 6.4% and 6.5% "the same", but a 0.1pp gap between two reported
        # rates is exactly the kind of disagreement worth surfacing.
        if pa.is_percent and pb.is_percent:
            diff = abs(x - y)
            if diff < 0.05:
                return ("equivalent",
                        f"same rate to the reported precision ({x:g}% vs {y:g}%)", detail)
            return ("differ", f"rates differ by {diff:.2f} percentage points "
                              f"({x:g}% vs {y:g}%)", detail)

        denom = max(abs(x), abs(y))
        if denom == 0:
            return ("equal", "both values are zero", detail)
        rel = abs(x - y) / denom
        if rel <= rel_tol:
            same_digits = abs(pa.base_number or 0) == abs(pb.base_number or 0)
            if same_digits and pa.scale != pb.scale:
                return ("equivalent",
                        f"same quantity at a different scale ({a!r} = {b!r})", detail)
            return ("equivalent",
                    f"values agree within {rel_tol:.1%} ({x:g} vs {y:g})", detail)
        # Rounding: 8,142 Cr vs 8.1 thousand Cr, or 12.4 vs 12
        if rel <= 0.02:
            return ("equivalent",
                    f"values differ by {rel:.2%}, consistent with rounding ({x:g} vs {y:g})", detail)
        return ("differ", f"values differ by {rel:.1%} ({x:g} vs {y:g})", detail)

    if not pa.is_numeric and not pb.is_numeric:
        from rapidfuzz import fuzz
        score = fuzz.token_set_ratio(pa.text or a.lower(), pb.text or b.lower()) / 100.0
        if score >= 0.92:
            return ("equal", f"textual values match ({a!r} ~ {b!r})", detail | {"similarity": score})
        if score >= 0.72:
            return ("equivalent", f"textual values are close ({a!r} ~ {b!r})",
                    detail | {"similarity": score})
        return ("differ", f"textual values differ ({a!r} vs {b!r})", detail | {"similarity": score})

    return ("incomparable", "one value is numeric and the other is not", detail)
