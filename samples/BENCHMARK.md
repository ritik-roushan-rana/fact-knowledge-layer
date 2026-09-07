# Benchmark: deterministic vs LLM-based extraction

Corpus: **1 PDFs**, first 8 pages of each.  
Model when an LLM is used: `openai/gpt-oss-120b` via `https://api.groq.com/openai/v1`.  
Each mode ingests the same input into a fresh database and then builds relationships, so the columns are directly comparable.

| metric | deterministic | hybrid | llm |
|---|---|---|---|
| PDF pages read | 8 | 8 | 8 |
| claims extracted | 22 | 22 | 0 |
| grounded claims | 21 | 21 | 0 |
| quarantined claims | 1 | 1 | 0 |
| claims from tables | 8 | 8 | 0 |
| candidate pairs | 0 | 0 | 0 |
| corroborations | 0 | 0 | 0 |
| contradictions | 0 | 0 | 0 |
| reconciliations | 0 | 0 | 0 |
| partial coverage | 0 | 0 | 0 |
| supersedes | 0 | 0 | 0 |
| underspecified | 0 | 0 | 0 |
| LLM calls | 0 | 0 | 1 |
| LLM fallback claims | 0 | 0 | 0 |
| LLM fallback % | 0.0 | 0.0 | 0.0 |
| runtime (s) | 0.5 | 0.5 | 121.0 |

## Reading this table

The `llm` column shows **0 claims in 121 seconds**. That is not a statement
about what the model can extract — it is a rate-limit failure. The free tier
caps at 8,000 tokens per minute, and a single extraction request for 8 pages
exhausted the budget, retried, and returned nothing within the retry window.

That is the operational point: on this account the LLM extraction path could
not be run reliably at all, while the deterministic path processed the same
pages in half a second with no account. The `hybrid` column matches
`deterministic` exactly because no page met the fallback trigger (a page with
substantial numeric content from which the rules extracted nothing), so no
request was made.

Full-corpus deterministic figures are in `BENCHMARK_full_deterministic.md`.
