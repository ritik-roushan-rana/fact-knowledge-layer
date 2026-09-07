# Fact Knowledge Layer

Extracts checkable claims from PDFs, proves every claim against its source text,
and decides whether claims from different documents corroborate each other,
contradict each other, or only appear to conflict because they measure different
things.

**The core engine is deterministic and runs with no API key.** No LLM is
involved in reading a PDF, extracting a claim, or deciding a relationship. An
LLM can optionally be switched on to adjudicate the small number of pairs the
rules explicitly mark ambiguous.

Nothing in the pipeline is specific to a document, a company, or a metric.

---

## 1. Setup and Run Instructions

### Requirements

- Python 3.11 or 3.12 (3.13+ lacks wheels for some dependencies)
- No API key, no database server, no network access

### Install

```bash
git clone <your-repo-url> && cd fact-knowledge-layer
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### Run the UI

```bash
.venv/bin/python scripts/serve.py
```

Open <http://127.0.0.1:8000> and drag any PDF onto the upload area. Ingest runs
as a background job with live progress. The **Relationships** tab shows each
verdict with both evidence quotes, the explanation, the context differences and
an expandable step-by-step reasoning trace.

### Run from the command line

```bash
# ingest PDFs, then build cross-document relationships
.venv/bin/python scripts/ingest.py starter-datasets/delhivery/*.pdf

# the whole starter corpus (6 PDFs, 511 pages) -- about 90 seconds
.venv/bin/python scripts/ingest.py starter-datasets/*/*.pdf

# only the first N pages of each file, for a quick look
.venv/bin/python scripts/ingest.py --max-pages 16 some.pdf
```

Re-running the same file is a no-op: documents are content-addressed, so an
already-completed PDF is skipped. Use `--force` to redo it.

### Run the tests

```bash
.venv/bin/python -m pytest tests/ -q
```

60 tests, all offline. They cover every required case (corroboration across
units, contradiction in percentage points, standalone-vs-consolidated
reconciliation, fiscal-year and unit equivalence, predicate and subject false
positives, partial coverage, grounding failure, missing context) plus
provenance, table reconstruction, checkpoint/resume and cross-corpus
generalisation.

### Benchmark

```bash
.venv/bin/python scripts/benchmark.py --modes deterministic --pages 0
```

### Optional: enable the LLM fallback

Everything above works without this. To let the model adjudicate ambiguous
pairs only:

```bash
cp .env.example .env          # then add your key
export FKL_ENABLE_LLM_FALLBACK=1
.venv/bin/python scripts/ingest.py --escalate some.pdf
```

`.env` is gitignored. The default configuration targets Groq, but the provider
is three settings (`FKL_LLM_BASE_URL`, `FKL_LLM_API_KEY_ENV`, `FKL_LLM_MODEL`),
so any OpenAI-compatible endpoint works.

---

## 2. Video Demo

**TODO: link (≤ 3 minutes)**

---

## 3. Approach

### Architecture

```
                      PDF
                       │
            ┌──────────▼───────────┐
            │ PyMuPDF / pdfplumber │   text, words, coordinates,
            │  structured pages    │   headings, captions, tables
            └──────────┬───────────┘
                       │
            ┌──────────▼───────────┐
            │  Rule-based claim    │   sentence rules + table
            │     extraction       │   reconstruction
            └──────────┬───────────┘
                       │
            ┌──────────▼───────────┐
            │ Grounding +          │   locate the evidence for real,
            │ claim normalisation  │   quarantine what cannot be located
            └──────────┬───────────┘
                       │
            ┌──────────▼───────────┐
            │ Entity matching      │   two independent gates
            │ Predicate matching   │
            └──────────┬───────────┘
                       │
            ┌──────────▼───────────┐
            │ Context comparison   │   period / unit / scope / basis /
            │                      │   denominator / modality
            └──────────┬───────────┘
                       │
   ┌───────────┬───────┼────────┬──────────────┬─────────────┐
   ▼           ▼       ▼        ▼              ▼             ▼
CORROB.  CONTRADICT. RECONCILED SUPERSEDES PARTIAL_COVER UNDERSPECIFIED
                                                              │
                                                    ambiguous only, opt-in
                                                              ▼
                                                      Optional LLM
```

### The central decision: extract claims, not facts

A "fact" flattened to a value cannot be compared safely. `8,142` and `2,076`
look like a contradiction until you know one is a full year and the other a
quarter. So the unit of extraction is a **claim**, which carries the context
that makes it comparable:

```json
{
  "subject": "Delhivery Limited",
  "predicate": "revenue from customers",
  "value": "8,142",
  "value_num": 81420000000.0,
  "unit": "INR crore",
  "context": {
    "period": "FY24", "scope": null, "basis": null,
    "geography": null, "as_of": null, "denominator": null
  },
  "modality": "reported",
  "evidence": {
    "page": 14, "text": "Revenue from customers(1) | FY24 | 8,142",
    "char_span": [1183, 1208], "bbox": [138.6, 170.8, 472.5, 184.2]
  },
  "confidence": { "extraction": 0.88, "grounding": 1.0 }
}
```

A null context field means *the document did not say*, which is not the same as
the two claims disagreeing. **Missing context can never, on its own, produce a
contradiction.**

### Why deterministic

The previous version of this system sent PDF chunks to an LLM and asked for
facts. Replacing that with rules was not about avoiding AI — it was driven by
three measured problems:

1. **It could not be trusted.** The model reconstructed a bar chart's *visual*
   layout into a quote (`"Cross Border Services revenue 109 153 174 Q4 FY23…"`)
   whose numbers were real but whose text never appeared in the document.
2. **It was unverifiable.** Structure that is recoverable — a row label, a
   column header, a corner cell declaring `₹ Cr` — was being guessed at rather
   than read.
3. **It was slow and rate-limited.** The full corpus was a 3-hour job against a
   free-tier token budget, and could not run at all without a key.

Where the evidence is structurally recoverable, reading it is better than
asking a model to guess it. The LLM is reserved for genuine ambiguity.

### Deterministic PDF understanding

Pages are rebuilt from PyMuPDF's structured dictionary rather than flat text, so
**every character offset maps to a line and therefore to a bounding box**. That
is what lets a grounded claim carry real coordinates. Headings are detected
typographically (relative font size and weight), and text repeated across many
pages is classified as page furniture and excluded — a structural rule that
never needs to know what the boilerplate says.

**Table reconstruction** runs several strategies and scores them. PyMuPDF's
ruled-line finder is fast and excellent on real grid tables but returns
near-empty boxes on chart-heavy slides and merges rows on some appendix
layouts; pdfplumber's text-alignment strategy recovers exactly those. Each
candidate is scored on structural quality only — cell fill rate, whether a
header row and label column exist, how many cells are numeric, and whether
cells show signs of having been merged — then overlapping candidates are
deduplicated and the best kept. Multi-row headers are merged into one header
per column, and units are inherited from the corner cell, caption or header.

Result: **92 usable tables across 511 pages in 84 seconds**, with chart slides
and catastrophically-merged tables correctly rejected so they fall back to
sentence extraction.

### Rule-based claim extraction

Two paths. **Sentence claims** match a label, a linking phrase and a value that
are contiguous in the page text (`"Core inflation increased to 4.6 percent"`);
because they are contiguous, the evidence span is exact. **Table claims**
recombine a row label, a column header and a cell into one claim, inheriting
period from the column header when it parses as one, and unit from the corner
cell or caption.

The vocabulary these rules use lives in `fkl/lexicon.py`, isolated so it is
auditable that it is ordinary English reporting language — linking verbs, scope
and basis qualifiers, modality words, attribution patterns — and not domain
knowledge. The test for whether a term belongs there: *would it be equally true
of a medical trial report, a municipal budget, and a football annual?*

Change verbs are handled separately from level verbs, because "revenue
increased by 578" and "revenue was 578" are different assertions; collapsing
them would manufacture contradictions.

### Grounding, and what gets quarantined

Nothing an extractor asserts is trusted. Deterministic sentence claims record
the character range they were cut from, so verification is an equality check.
Table claims verify the row label and the cell value on the page independently.
Claims that merely *quote* (an LLM, or a fallback path) are located by exact
then fuzzy search.

The fuzzy path had a real weakness worth describing, because finding it changed
the design. `partial_ratio` rewards the best-matching sub-window, and character
similarity cannot see reordering — so text rebuilt out of a chart still scored
0.80–0.85, above the acceptance threshold. Scoring is now scaled by how much of
the quote's **word order** survives in the located window (a typo-tolerant
longest-common-subsequence over tokens). Measured:

| quote | score | outcome |
|---|---|---|
| verbatim | 1.00 | accepted |
| verbatim with a typo | 0.98 | accepted |
| chart labels and values reordered | 0.71 | quarantined |
| labels then values, chart style | 0.60 | quarantined |
| stitched from non-adjacent lines | 0.31 | quarantined |
| wholly fabricated | 0.00 | quarantined |

A quarantined claim is stored and visible but **never enters comparison**. A
relationship must not rest on evidence we could not verify.

### Two independent gates

Identity is settled before values are looked at.

**Entity matching** is deterministic: unicode folding, legal-suffix removal,
containment, and acronym expansion (`RBI` = `Reserve Bank of India`, `IMF` =
`International Monetary Fund`) — while refusing `Delhivery` = `DHL`, which
share letters but no content words.

**Predicate matching** returns three outcomes, not two, because `revenue` and
`revenue from operations` are neither identical nor unrelated:

- `same` → compare the values
- `related` → same family, different measure; can never be a contradiction
- `different` → never compared at all

This is where an earlier design failed hard. Retrieval used a single embedding
over `(subject, predicate)`; when two claims share a subject, the subject
dominates the vector, so `headcount` and `total revenue` scored 0.765 against
each other — about the same as genuine synonyms — and were reported as a
confident contradiction. Blocking on the two fields separately removes the
failure mode and needs no model at all.

### Context-aware comparison

Comparison runs `entity → predicate → context → values → verdict`, and every
verdict is produced by a rule that actually fired. The trace records them in
order; it is not narration written after the fact.

```
entity gate: matched -- identical after normalisation ('delhivery')
predicate gate: same -- one predicate adds a single qualifier ('balance')
period: same -- neither claim states a period
unit: same -- different scale ('INR crore' vs 'INR million'); normalised
scope: same -- neither claim states a scope
value: equivalent -- values agree within 0.5% (5.444e+10 vs 5.44387e+10)
decision: comparable context + agreeing values -> corroboration
```

Seven outcomes, because forcing everything into three produces false
contradictions: `corroboration`, `contradiction`, `reconciled`, `supersedes`
(an estimate revised by a later actual), `partial_cover` (a segment against a
total), `underspecified`, `unrelated`.

Normalisation is general-language: magnitudes (thousand/lakh/million/crore/
billion), currencies, percentages, and periods (`FY24` = `FY2024` =
`fiscal year 2023-24`; `2024-25` = `FY25`; a quarter is not its year; a month
is not a fiscal year). **Rates are compared in percentage points, not
relatively** — a relative tolerance would call 4.0% and 2.8% a "30% difference"
and hide that they are two incompatible reported rates.

### Confidence is four numbers, not one

Extraction, grounding, matching and relationship confidence answer different
questions. A verdict is bounded by the weaker of the two claims, so a shaky
extraction cannot become a confident contradiction just because two numbers
differ. This is pinned by a test.

### Measured results

Full starter corpus, deterministic, no API key
(`samples/BENCHMARK_full_deterministic.md`):

| metric | value |
|---|---|
| PDF pages read | 511 |
| claims extracted | 1,731 |
| grounded claims | 1,710 |
| quarantined claims | 21 |
| claims reconstructed from tables | 1,042 |
| candidate pairs compared | 886 |
| LLM calls | **0** |
| total runtime | **91 seconds** |

Against the LLM-based pipeline it replaced, on the same machine and corpus:

| | deterministic | LLM-based |
|---|---|---|
| full corpus (511 pages) | **91 s** | ~3 hours (rate-limited, never completed) |
| Q4 deck (27 pages) | **1.3 s**, 518 claims | minutes, 43 claims from 12 pages |
| API calls | **0** | ~163 |
| runs without a key | **yes** | no |

A head-to-head across all three modes on an identical subset is in
`samples/BENCHMARK.md`.

### AI tools used

- **Claude Code (Opus 5)** as an engineering partner throughout: architecture,
  implementation, and — most usefully — inspecting real intermediate output at
  each stage, which is how most of the bugs listed below were found.
- **Groq (`openai/gpt-oss-120b`)** for the earlier LLM extraction pipeline that
  this architecture replaced, and now only for the optional adjudication of
  ambiguous pairs. Not required to run the system.

---

## 4. Limitations and Next Steps

These are the things that genuinely do not work well. Measurements, not
disclaimers.

### The starter corpus currently yields no contradiction

The system detects contradictions — it is tested, and the rules fire correctly
on constructed pairs (RBI 4.0% vs IMF 2.8% CPI inflation → `contradiction`,
1.20 percentage points apart). But **on the six starter PDFs it currently finds
zero**, because only 6 cross-document pairs share both an entity and a
predicate, and none of those share a period.

The cause is recall, not reasoning: prose-heavy documents yield few claims
(IMF: 73 claims from 95 pages) because the sentence rules require a
label-linking-phrase-value pattern that much real prose does not follow
("Following economic growth of 6.5 percent…", "down from 9.8 percent"). The
earlier LLM pipeline had better recall here and worse precision everywhere
else. **Next step:** dependency-light clause parsing so a value can be attached
to the nearest preceding noun phrase rather than requiring a specific verb
pattern, and support for comparative constructions (`X, down from Y`) which
carry two claims at once.

### `partial_cover` dominates the output

600 of 886 pairs land there. That verdict is *safe* — it never asserts a
conflict — but it is doing too much work, because any predicate that is a
strict refinement of another falls into it. Some of those are genuine
component-of-total relationships; many are just two differently-worded
properties. **Next step:** use the numeric relationship (does the narrower
value plausibly sum into the broader one across siblings?) to separate real
partial coverage from mere relatedness.

### Entity detection is wrong on one of six documents

The prospectus elects `Equity Shares` as its subject rather than
`Delhivery Limited`, because a prospectus genuinely mentions equity shares
constantly in mid-sentence position. Frequency plus page-spread plus an
organisation-suffix boost gets the other five right (`India`, `Reserve Bank`,
`Delhivery`, `Delhivery Limited`, `India`). I tried a heading-based signal and
it made things worse (`Tons`, `However`), so I reverted it rather than keep a
change I could not justify. **Next step:** cover-page title extraction as a
prior, evaluated against all six documents before being kept.

### Table extraction still fails on dense appendix layouts

The RBI appendix tables collapse many rows into single cells under every
strategy tried. The quality score correctly *rejects* them, so they produce no
false claims — they simply produce nothing, and those pages fall back to
sentence rules. **Next step:** a coordinate-clustering table strategy that
groups words into rows and columns by position rather than relying on ruling
lines or text alignment.

### Other known gaps

- **No OCR.** Scanned or image-only PDFs yield nothing. The architecture has a
  place for it (an optional fallback when a page has no native text) but it is
  not implemented.
- **Percentages without a stated denominator** are marked `underspecified`
  rather than compared. This is deliberate and correct — `revenue = 33%` and
  `revenue = 7.46%` are proportions of different bases — but it means genuine
  share-vs-share comparisons are also skipped.
- **Predicate quality varies.** Table row labels are clean; sentence labels
  still occasionally carry a trailing fragment (`"gdp is projected to grow"`).
- **Relationships are cross-document only** by default. Intra-document
  contradictions (a director active on page 5 and resigned on page 90) are not
  surfaced, though the machinery supports it.
- **No sum/aggregation reasoning.** The system cannot yet check that segments
  add up to a stated total.

---

## 5. Additional Notes

### Repository layout

```
fkl/
  pdf.py            structured pages: lines, coordinates, headings, furniture
  tables.py         multi-strategy table reconstruction + quality scoring
  lexicon.py        general-language vocabulary (auditable: no domain terms)
  extract_rules.py  sentence and table claim extraction
  normalize.py      magnitudes, currencies, percentages, fiscal periods
  ground.py         evidence verification and quarantine
  entity.py         deterministic entity matching
  predicate.py      deterministic predicate matching (same/related/different)
  match.py          blocking: entity cluster -> predicate token -> measure kind
  compare.py        context-aware comparison, seven verdicts, reasoning traces
  extractors.py     ClaimExtractor interface (deterministic | hybrid | llm)
  adjudicate.py     optional LLM fallback, isolated from the pipeline
  pipeline.py       ingest and relationship orchestration, resume
  store.py          SQLite, additive migrations
  api.py            FastAPI + static UI
scripts/            ingest.py, serve.py, benchmark.py, export_samples.py, check_llm.py
tests/              60 offline tests
```

### On not hard-coding

There is no reference anywhere in `fkl/` to a company, a document, a filename,
a page number, or a metric name. The rules key off document structure
(headings, tables, captions, page furniture) and general English patterns. The
one place a word list exists is `fkl/lexicon.py`, which is deliberately
separated so it can be reviewed for exactly this.

Both starter corpora — a logistics company's filings and three institutional
macroeconomics reports — run through the identical code path, and a test
asserts the macro corpus works without corpus-specific rules.

### Credentials

No credentials are in the repository. `.env` is gitignored, and the system's
default configuration needs none. Sample output is committed under `samples/`
so results can be inspected without running anything.

### Git history

The commit history follows the actual development sequence, including the
architectural change: extraction pipeline → grounding → provider swap →
cross-document matching → API/UI → deterministic re-architecture → tests and
benchmark.
