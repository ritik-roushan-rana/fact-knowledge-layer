# Benchmark: deterministic vs LLM-based extraction

Corpus: **1 PDFs**, first 8 pages of each.  
Model when an LLM is used: `openai/gpt-oss-120b` via `https://api.groq.com/openai/v1`.  
Each mode ingests the same input into a fresh database and then builds relationships, so the columns are directly comparable.

| metric | deterministic | hybrid | llm |
|---|---|---|---|
| PDF pages read | 8 | 8 | 8 |
| claims extracted | 13 | 46 | 44 |
| grounded claims | 12 | 45 | 44 |
| quarantined claims | 1 | 1 | 0 |
| claims from tables | 8 | 8 | 0 |
| candidate pairs | 0 | 0 | 0 |
| corroborations | 0 | 0 | 0 |
| contradictions | 0 | 0 | 0 |
| reconciliations | 0 | 0 | 0 |
| partial coverage | 0 | 0 | 0 |
| supersedes | 0 | 0 | 0 |
| underspecified | 0 | 0 | 0 |
| LLM calls | 0 | 2 | 1 |
| LLM fallback claims | 0 | 33 | 0 |
| LLM fallback % | 0.0 | 71.74 | 0.0 |
| runtime (s) | 0.5 | 7.6 | 73.5 |
