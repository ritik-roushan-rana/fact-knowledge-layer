# Benchmark: deterministic vs LLM-based extraction

Corpus: **6 PDFs**, all pages.  
Model when an LLM is used: `openai/gpt-oss-120b` via `https://api.groq.com/openai/v1`.  
Each mode ingests the same input into a fresh database and then builds relationships, so the columns are directly comparable.

| metric | deterministic |
|---|---|
| PDF pages read | 511 |
| claims extracted | 1731 |
| grounded claims | 1710 |
| quarantined claims | 21 |
| claims from tables | 1042 |
| candidate pairs | 886 |
| corroborations | 1 |
| contradictions | 0 |
| reconciliations | 24 |
| partial coverage | 600 |
| supersedes | 0 |
| underspecified | 25 |
| LLM calls | 0 |
| LLM fallback claims | 0 |
| LLM fallback % | 0.0 |
| runtime (s) | 91.1 |
