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
    "moderated to", "moderating to", "eased to", "easing to", "softened to",
    "accelerated to", "picked up to", "settled at", "printed at",
    "was placed at", "were placed at", "is placed at", "averaged", "averaging",
    "hovered around", "stood around", "came to", "worked out to",
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
    # Prepositions and pronoun/verb openers. Without these, "we had cash" and
    # "swung to a profit" survive as predicates and fail to match the plain
    # property name the other document uses.
    "to", "from", "into", "onto", "upto", "up", "down", "we", "it", "they",
    "he", "she", "there", "had", "have", "has", "having", "been", "being",
    "achieved", "delivered", "generated", "maintained", "held", "saw", "reaching",
}

# Trailing words that are grammatical glue rather than part of the property name.
TRAILING_FILLER = {
    "of", "for", "in", "at", "to", "on", "by", "with", "from", "the", "a", "an",
    "was", "were", "is", "are", "and", "or",
    # Auxiliaries, bare verbs and hedges that trail a label but are not part of
    # the property name: "headline inflation has", "GDP is projected to grow".
    "has", "have", "had", "been", "being", "be", "will", "would", "may",
    "grow", "grew", "grown", "rise", "rose", "risen", "fall", "fell", "fallen",
    "reach", "reached", "stand", "stood", "remain", "remained", "come", "came",
    "print", "printed", "place", "placed", "record", "recorded", "register",
    "registered", "post", "posted", "expand", "expanded", "moderate",
    "moderated", "ease", "eased", "decline", "declined", "increase",
    "increased", "decrease", "decreased",
    "projected", "estimated", "expected", "forecast", "revised", "provisional",
    "about", "around", "nearly", "approximately", "roughly", "some", "over",
    "under", "above", "below", "just", "only", "also", "still", "further",
    "respectively", "buoyant", "strong", "weak", "higher", "lower",
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


# Pairs of qualifiers that describe mutually exclusive ways of measuring the
# same thing. If one predicate carries one and the other carries its opposite,
# they are not the same measurement -- comparing them would manufacture a
# contradiction out of a definitional difference.
ANTONYM_GROUPS = [
    {"net", "gross"},
    {"real", "nominal"},
    {"standalone", "consolidated"},
    {"urban", "rural"},
    {"domestic", "external", "foreign", "overseas"},
    {"current", "constant"},
    {"export", "exports", "import", "imports"},
    {"inflow", "inflows", "outflow", "outflows"},
    {"revenue", "expense", "expenses", "expenditure", "cost", "costs"},
    {"asset", "assets", "liability", "liabilities"},
    {"opening", "closing"},
    {"average", "median", "total", "peak", "minimum", "maximum"},
    {"male", "female"},
    {"public", "private"},
    {"short-term", "long-term"},
]

# Words carrying no discriminating power inside a predicate.
PREDICATE_STOPWORDS = {
    "the", "a", "an", "of", "for", "in", "at", "on", "to", "by", "with", "from",
    "and", "or", "as", "per", "its", "their", "our", "value", "values",
    "amount", "amounts", "figure", "figures", "number", "numbers", "level",
    "levels", "rate", "was", "were", "is", "are", "during", "over",
}


# Words that make a predicate inherently a rate or a ratio. A percentage value
# under such a predicate is expected and self-describing ("CPI inflation =
# 4.0%"). A percentage under a predicate that names a LEVEL ("revenue = 33%")
# is a share or a margin whose denominator the extractor did not capture, and
# comparing two such figures is meaningless -- they are proportions of
# different bases.
RATE_PREDICATE_TERMS = {
    "growth", "inflation", "rate", "rates", "margin", "margins", "share",
    "ratio", "yield", "change", "cagr", "percentage", "percent", "proportion",
    "penetration", "utilisation", "utilization", "occupancy", "return",
    "returns", "deficit", "surplus", "coverage", "density", "incidence",
    "prevalence", "unemployment", "participation", "literacy", "mortality",
}


# Headings that mark the end of a document's own assertions. Everything after
# one of these is other people's titles, dates and identifiers -- mining it
# yields claims like "arxiv = 1903.02613". A general document convention, in
# the same category as running headers, not domain knowledge.
END_MATTER_HEADINGS = {
    "references", "reference", "bibliography", "works cited", "citations",
    "further reading", "notes and references",
}
