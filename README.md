# Fact Knowledge Layer

Extracts checkable claims from PDFs, proves every claim against its source text,
and works out whether claims from different documents corroborate each other,
contradict each other, or only appear to conflict because they measure different
things.

Nothing in the pipeline is specific to a document, a company, or a metric. What
counts as a fact is decided by the document.

---

## 1. Setup and Run Instructions

### Requirements

- Python 3.11 (3.12 works; 3.13+ has no wheels for some dependencies yet)
- An API key for any OpenAI-compatible chat endpoint. The defaults target
  [Groq](https://console.groq.com) and its free tier is enough to run everything here.

### Install

```bash
git clone <your-repo-url> && cd <repo>
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### Configure

```bash
cp .env.example .env
# then put your key in .env:
#   GROQ_API_KEY=gsk_...
```

`.env` is gitignored; no credentials are stored in the repository. Confirm the
endpoint works before ingesting anything:

```bash
.venv/bin/python scripts/check_llm.py
```

This lists the models your key can reach and runs a structured-output smoke test.

### Run the UI

```bash
.venv/bin/python scripts/serve.py
```

Open <http://127.0.0.1:8000>. Drag any PDF onto the upload area. Ingest runs as a
background job with live progress; the **Relationships** tab shows the results.

### Or run from the command line

```bash
# ingest one or more PDFs, then build cross-document relationships
.venv/bin/python scripts/ingest.py starter-datasets/delhivery/*.pdf

# quick smoke run on part of a document
.venv/bin/python scripts/ingest.py --max-pages 12 some.pdf

# the comparison logic alone, no API key and no PDFs needed
.venv/bin/python tests_decision_matrix.py
```

### Using a different provider

Only three settings are provider-specific. Any OpenAI-compatible endpoint works:

```bash
FKL_LLM_BASE_URL=https://api.openai.com/v1
FKL_LLM_API_KEY_ENV=OPENAI_API_KEY
FKL_LLM_MODEL=gpt-4.1
```

Embeddings run locally, so the matching and comparison stages need no credentials
at all.

---

## 2. Video Demo

**TODO: link (≤ 3 minutes)**

---

## 3. Approach

### The core decision: extract claims, not facts

A "fact" flattened to a value cannot be compared safely. `8,142` and `2,076` look
like a contradiction until you know one is a full year and the other a quarter.
So the unit of extraction is a **claim**, which carries its own qualifying context:

```json
{
  "subject": "…",  "predicate": "…",  "value": "…",
  "context": { "period": …, "unit": …, "scope": …, "other_qualifiers": … },
  "source_document": "…",
  "source_span": { "page": 7, "text": "…verbatim…" },
  "confidence": 0.95
}
```

`context` is always populated (fields may be null). It is the only reason the
system can tell a genuine contradiction from a reconciled one.

### Pipeline

```
PDF ──▶ page text ──▶ chunks ──▶ LLM extraction ──▶ grounding ──▶ embeddings
                                                        │              │
                                                        ▼              ▼
                                                  needs-review   candidate pairs
                                                     queue              │
                                                                        ▼
                                            deterministic comparison ──▶ verdict
                                                        │
                                              (ambiguous only) ──▶ LLM adjudication
```

**TODO: fill in architecture decisions, trade-offs and measured results**

### AI tools used

**TODO**

---

## 4. Limitations and Next Steps

**TODO: fill in honestly from what actually broke**

---

## 5. Additional Notes

**TODO**
