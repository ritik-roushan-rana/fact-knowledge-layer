# Benchmark: deterministic vs LLM-based extraction

Corpus: **6 PDFs**, all pages.  
Model when an LLM is used: `openai/gpt-oss-120b` via `https://api.groq.com/openai/v1`.  
Each mode ingests the same input into a fresh database and then builds relationships, so the columns are directly comparable.

| metric | deterministic |
|---|---|
| PDF pages read | 511 |
| claims extracted | 3750 |
| grounded claims | 3682 |
| quarantined claims | 68 |
| claims from tables | 3060 |
| candidate pairs | 1502 |
| corroborations | 1 |
| contradictions | 0 |
| reconciliations | 142 |
| partial coverage | 942 |
| supersedes | 0 |
| underspecified | 31 |
| LLM calls | 0 |
| LLM fallback claims | 0 |
| LLM fallback % | 0.0 |
| runtime (s) | 97.8 |
