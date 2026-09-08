# Benchmark: deterministic vs LLM-based extraction

Corpus: **6 PDFs**, all pages.  
Model when an LLM is used: `openai/gpt-oss-120b` via `https://api.groq.com/openai/v1`.  
Each mode ingests the same input into a fresh database and then builds relationships, so the columns are directly comparable.

| metric | deterministic |
|---|---|
| PDF pages read | 511 |
| claims extracted | 4773 |
| grounded claims | 4690 |
| quarantined claims | 83 |
| claims from tables | 4131 |
| candidate pairs | 21973 |
| corroborations | 28 |
| contradictions | 10 |
| reconciliations | 1814 |
| partial coverage | 13380 |
| supersedes | 105 |
| underspecified | 1417 |
| LLM calls | 0 |
| LLM fallback claims | 0 |
| LLM fallback % | 0.0 |
| runtime (s) | 97.5 |
