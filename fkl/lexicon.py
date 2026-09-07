"""General-language vocabulary used by the deterministic extractor.

Everything in this file is ordinary English reporting vocabulary -- the words
documents use to attach a value to a label, to qualify how a figure was
measured, and to attribute a statement to a source. None of it names an
industry, a company, a metric, or a document.

The test for whether something belongs here: would it be equally true of a
medical trial report, a municipal budget, and a football statistics annual?
If not, it does not belong.
"""
from __future__ import annotations

# Verbs and connectors that link a label to a value:
#   "Revenue from operations WAS 81,415.38 million"
#   "GDP growth STOOD AT 6.5%"
LINKING_PHRASES = [
    "was recorded at", "were recorded at", "was reported at", "were reported at",
    "was estimated at", "were estimated at", "was projected at", "were projected at",
    "is estimated at", "are estimated at", "is projected at", "are projected at",
    "stood at", "stands at", "amounted to", "amounts to", "totalled", "totaled",
    "totalling", "came in at", "increased to", "decreased to", "rose to", "fell to",
    "grew to", "declined to", "remained at", "reached", "registered", "recorded",
    "reported", "posted", "stood", "was at", "were at", "is at", "are at",
    "was", "were", "is", "are", "of", "at", ":", "--", "-",
]

# Verbs describing a CHANGE rather than a level. Kept separate because
# "revenue increased by 578" and "revenue was 578" are different assertions;
# collapsing them would manufacture contradictions.
CHANGE_PHRASES = [
    "increased by", "decreased by", "rose by", "fell by", "grew by",
    "expanded by", "declined by", "contracted by", "dropped by", "gained by",
    "improved by", "widened by", "narrowed by", "up by", "down by",
    "increased", "decreased", "grew", "expanded", "declined", "contracted",
]

# Participles and connectives that often open a clause and are not part of the
# property being measured.
CLAUSE_OPENERS = {
    "following", "given", "despite", "including", "excluding", "reflecting",
    "driven", "supported", "led", "compared", "versus", "against", "after",
    "before", "during", "since", "though", "although", "whereas", "while",
    "meanwhile", "moreover", "furthermore", "additionally", "consequently",
    "therefore", "thus", "hence", "accordingly", "notably", "specifically",
}

# Words that qualify HOW a figure was measured. Mapped to the context field
# they populate, so a qualifier changes comparability rather than being lost.
SCOPE_TERMS = {
    "consolidated": "consolidated", "standalone": "standalone",
    "unconsolidated": "standalone", "combined": "combined",
    "group": "group", "segment": "segment", "segmental": "segment",
    "aggregate": "aggregate", "total": "total", "gross": "gross", "net": "net",
    "overall": "overall", "per capita": "per capita", "per share": "per share",
    "urban": "urban", "rural": "rural", "domestic": "domestic",
    "nominal": "nominal", "real": "real",
}

BASIS_TERMS = {
    "seasonally adjusted": "seasonally adjusted",
    "annualised": "annualised", "annualized": "annualised",
    "year-on-year": "year-on-year", "year on year": "year-on-year",
    "yoy": "year-on-year", "y-o-y": "year-on-year",
    "quarter-on-quarter": "quarter-on-quarter", "qoq": "quarter-on-quarter",
    "q-o-q": "quarter-on-quarter", "month-on-month": "month-on-month",
    "constant prices": "constant prices", "current prices": "current prices",
    "market prices": "market prices", "basic prices": "basic prices",
    "factor cost": "factor cost", "purchasing power parity": "ppp", "ppp": "ppp",
    "accrual": "accrual basis", "cash basis": "cash basis",
    "pro forma": "pro forma", "like-for-like": "like-for-like",
    "compounded": "compounded", "cagr": "cagr",
}

# Words that change the epistemic status of a figure. These matter because an
# estimate and a later actual are a revision, not a contradiction.
MODALITY_TERMS = {
    "estimate": "estimate", "estimated": "estimate", "estimates": "estimate",
    "advance estimate": "estimate", "first advance estimate": "estimate",
    "second advance estimate": "estimate", "provisional": "provisional",
    "provisionally": "provisional", "preliminary": "provisional",
    "projected": "projection", "projection": "projection", "forecast": "projection",
    "expected": "projection", "outlook": "projection",
    "revised": "revised", "restated": "revised", "revision": "revised",
    "target": "target", "targeted": "target", "guidance": "target",
    "actual": "reported", "reported": "reported",
}

# Attribution: "according to X", "X reported that", "as per X".
ATTRIBUTION_PATTERNS = [
    r"according to ([A-Z][\w&.\- ]{2,60}?)[,.]",
    r"as per (?:the )?([A-Z][\w&.\- ]{2,60}?)[,.]",
    r"([A-Z][\w&.\- ]{2,60}?) (?:reported|reports|estimates|projects|notes) that",
]

# Determiners and filler stripped from the front of a candidate predicate.
LEADING_FILLER = {
    "the", "a", "an", "its", "their", "our", "this", "that", "these", "those",
    "and", "but", "however", "meanwhile", "overall", "in", "for", "of", "on",
    "at", "as", "while", "with", "by", "total",
}

# Trailing words that are grammatical glue rather than part of the property name.
TRAILING_FILLER = {
    "of", "for", "in", "at", "to", "on", "by", "with", "from", "the", "a", "an",
    "was", "were", "is", "are", "and", "or",
}

# Generic organisation suffixes. Used only to *boost* a candidate entity's
# score, never as a requirement -- documents about countries or people have no
# such suffix and must still work.
ORG_SUFFIXES = {
    "limited", "ltd", "ltd.", "inc", "inc.", "incorporated", "corporation",
    "corp", "corp.", "company", "co", "co.", "plc", "llc", "llp", "gmbh",
    "ag", "sa", "nv", "bv", "pte", "pvt", "holdings", "group", "bank",
    "fund", "authority", "ministry", "department", "commission", "board",
    "association", "institute", "university", "foundation", "trust",
}

# Phrases that look like proper nouns but are document furniture.
ENTITY_STOPWORDS = {
    "annual report", "table of contents", "contents", "introduction", "overview",
    "executive summary", "notes", "appendix", "chapter", "section", "figure",
    "table", "source", "sources", "note", "page", "financial statements",
    "balance sheet", "cash flow", "income statement", "january", "february",
    "march", "april", "may", "june", "july", "august", "september", "october",
    "november", "december", "monday", "tuesday", "wednesday", "thursday",
    "friday", "saturday", "sunday",
}
