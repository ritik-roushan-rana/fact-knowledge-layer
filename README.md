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

**Table reconstruction** runs three strategies and scores them:

1. **PyMuPDF ruled lines** — fast and excellent on real grid tables, but returns
   near-empty boxes on chart-heavy slides and merges rows on some appendices.
2. **pdfplumber text alignment** — recovers row structure where ruling lines
   are absent.
3. **Coordinate clustering** — builds a grid from word positions alone: rows by
   clustering on vertical centre, columns by finding the vertical whitespace
   that almost no row crosses. Wide statistical tables defeat both other
   strategies, which over-segment them into a sparse grid where most cells are
   empty; the words themselves still carry the structure.

Each candidate is scored on structural quality only — cell fill rate, whether a
header row and label column exist, how many cells are numeric, and whether cells
show signs of having been merged. Overlapping candidates are deduplicated and
the best kept. Multi-row headers are merged into one header per column, the row
that names the periods is preferred as the column header over a spanning title,
and units are inherited from the corner cell, caption or header.

Result: **221 usable tables across 511 pages**, producing 3,060 structured
claims, with chart slides and catastrophically-merged tables correctly rejected
so those pages fall back to sentence extraction. Adding the coordinate strategy
took the IMF staff report from 8 tables to 21, which is what made its
"Selected Economic Indicators" table — six years of GDP, inflation and fiscal
figures — available as claims at all.

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
| claims extracted | 3,750 |
| grounded claims | 3,682 |
| quarantined claims | 68 |
| tables reconstructed | 221 |
| claims reconstructed from tables | 3,060 |
| candidate pairs compared | 1,502 |
| LLM calls | **0** |
| total runtime | **98 seconds** |

Against the LLM-based pipeline it replaced, on the same machine and corpus:

| | deterministic | LLM-based |
|---|---|---|
| full corpus (511 pages) | **98 s** | ~3 hours (rate-limited, never completed) |
| Q4 deck (27 pages) | **1.4 s**, 519 claims | minutes, 43 claims from 12 pages |
| API calls | **0** | ~163 |
| runs without a key | **yes** | no |

The LLM comparison could not be completed at full scale: the free tier caps at
8,000 tokens/minute, and a corpus-wide run exhausted the budget without
finishing. That is itself part of the argument for the deterministic core —
the LLM path could not be run reliably at all, whereas the deterministic one
processes the whole corpus in under two minutes on a laptop with no account.

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

These are the things that genuinely do not work. Measurements and diagnoses,
not disclaimers.

### The starter corpus yields no contradiction — and I can say exactly why

The engine detects contradictions. The rules are tested and fire correctly on
constructed pairs (CPI inflation 4.0% vs 2.8%, same period → `contradiction`,
1.20 percentage points apart, with the trace naming every gate that passed).
**On the six starter PDFs it currently finds none**, and tracing the closest
real candidate shows precisely where it stops:

| claim | source | value | period |
|---|---|---|---|
| `inflation` | RBI Annual Report | 3.5% | 2024-25 |
| `inflation rate` | Economic Survey | 4.4% | FY25 |

```
predicate gate: same     -- identical after normalisation ('inflation')
period gate:    same     -- same period ('2024-25' ~ 'FY25')
entity gate:    REJECTED -- different entities ('reserve' vs 'india')
```

Two of three gates pass. The **entity gate** stops it, because primary-entity
detection elects the document's *publisher* rather than its *topic*: the RBI
Annual Report is published by the Reserve Bank but is about India, so its
claims are attributed to "Reserve Bank" and never meet the Economic Survey's
"India". Whether those two figures really conflict is a separate question — one
may be core and the other headline inflation — but the system should be
surfacing the pair for judgement, and it is not.

**Next step:** separate publisher from topic. A publisher appears in the title
block, in page furniture, and in attribution position ("the Reserve Bank
projects…"); a topic appears as the grammatical subject of claims throughout.
Attributing a subject-less claim to the topic rather than the author is the fix.

### Lexical predicate matching cannot bridge institutional vocabularies

The IMF states `consumer prices - combined`, the RBI states `inflation`, the
Survey states `inflation rate`. The first shares no content word with the other
two, so it is never even a candidate. Acronym alignment handles `CPI inflation`
≡ `consumer price inflation`, but not `consumer prices` ≡ `inflation`, which
needs meaning rather than spelling.

This is the clearest case where the optional LLM would earn its place — except
that the current design escalates only pairs the rules marked ambiguous, and
these never become pairs at all. **Next step:** an optional semantic
candidate-generation channel (local embeddings over predicates only, which
measured well: 0.62–0.85 for true synonym pairs against ≤0.334 for false ones)
that proposes *additional* candidates while the deterministic rules still decide
every verdict.

### `partial_cover` dominates the output

942 of 1,502 pairs land there. The verdict is *safe* — it never asserts a
conflict — but it is doing too much work: any predicate that is a strict
refinement of another falls into it, which lumps genuine component-of-total
relationships together with merely-related properties. **Next step:** use the
numeric relationship (do sibling values plausibly sum into the broader one?) to
separate real partial coverage from mere relatedness.

### Entity detection is correct on four of six documents

Correct: `India` (IMF), `Reserve Bank` (RBI), `Delhivery Limited` (Q4 deck),
`Delhivery` (annual report). Wrong: the prospectus elects `Equity Shares` and
the Economic Survey elects `Economy`, because both terms genuinely dominate
mid-sentence usage in those documents. I tried a heading-based signal and it
made things worse (`Tons`, `However`), so I reverted it rather than keep a
change I could not justify. It is also unstable: the winner can change with the
number of pages sampled.

### Other known gaps

- **No OCR.** Scanned or image-only PDFs yield nothing. The architecture has a
  place for it (an optional fallback when a page has no native text); it is not
  implemented.
- **Percentages without a stated denominator** are marked `underspecified`
  rather than compared. That is deliberate — `revenue = 33%` and
  `revenue = 7.46%` are proportions of different bases — but it also skips
  genuine share-vs-share comparisons.
- **Sentence recall is lower than table recall.** 3,060 of 3,750 claims come
  from tables. Prose-heavy documents give up fewer claims because the sentence
  rules need a label-linking-phrase-value pattern that much real writing does
  not follow ("Following economic growth of 6.5 percent…", "down from 9.8
  percent"). **Next step:** attach a value to the nearest preceding noun phrase
  rather than requiring a specific verb, and handle comparative constructions
  that carry two claims at once.
- **Predicate quality varies.** Table row labels are clean; sentence labels
  still occasionally carry a trailing fragment.
- **Relationships are cross-document only** by default. Intra-document
  contradictions (a director active on page 5, resigned on page 90) are not
  surfaced, though the machinery supports it.
- **No aggregation reasoning.** The system cannot check that segments sum to a
  stated total.
- **The LLM comparison is incomplete.** On this account the LLM extraction path
  could not complete even 8 pages within the free tier's token budget, so the
  head-to-head in `samples/BENCHMARK.md` shows it producing nothing. The
  comparison against the earlier LLM pipeline rests on measurements taken while
  that pipeline was the live implementation.

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
  api.py            FastAPI, serving the JSON API and the static UI
  web/              frontend (see below)
scripts/            ingest.py, serve.py, benchmark.py, export_samples.py, check_llm.py
tests/              60 offline tests
```

### Frontend

No build step, no framework, no bundler — the page is served as-is and edits
are visible on reload. Native ES modules and separate stylesheets give the
structure a build tool would otherwise provide:

```
fkl/web/
  index.html            markup shell only; no inline styles or scripts
  css/
    tokens.css          the only file that names a colour (light + dark palettes)
    base.css            reset, app shell, layout primitives
    components.css      buttons, chips, cards, badges, tables, meters
    views.css           view-specific layout
  js/
    app.js              entry point: routing, shared refresh, event delegation
    core/
      dom.js            auto-escaping `html` template tag, render, delegate
      api.js            the only module that knows endpoint paths
      format.js         verdict labels, scores, context fields, evidence choice
      state.js          app state and a minimal pub/sub
      theme.js          auto / light / dark, remembered per browser
    components/         primitives, claim-panel, relation-card, shell
    views/              relations, claims, review, documents
```

Two decisions worth naming. **Escaping is the default**: `html` is a tagged
template that escapes every interpolated value and returns a marked result, so
nesting templates composes correctly while a bare string is always escaped —
an injection bug is hard to write by accident rather than merely discouraged.
**Events are delegated** from stable containers rather than rebound after each
render, because views re-render wholesale.

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
