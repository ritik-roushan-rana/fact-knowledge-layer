"""LLM claim extraction.

The prompt is deliberately domain-blind: it never names an industry, a metric,
or a document type. What counts as a fact is decided by the document, which is
what makes the pipeline generalise to PDFs it has not seen.

Strict json_schema structured output is used rather than free-form JSON so the
response is schema-valid by construction and we can spend our error budget on
grounding instead of parsing. The provider sits behind fkl.llm.
"""
from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from pydantic import ValidationError

from .config import CONFIG
from .llm import LLMClient
from .models import Claim, ClaimBatch
from .pdf import Chunk

log = logging.getLogger("fkl.extract")

_NULLABLE_STR = {"type": ["string", "null"]}

# Hand-written rather than generated from Pydantic so the contract sent to the
# model is explicit and reviewable, and so every field is required-but-nullable
# (which is what forces `context` to always be populated).
CLAIM_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "subject": {"type": "string"},
                    "predicate": {"type": "string"},
                    "value": {"type": "string"},
                    "context": {
                        "type": "object",
                        "properties": {
                            "period": _NULLABLE_STR,
                            "unit": _NULLABLE_STR,
                            "scope": _NULLABLE_STR,
                            "other_qualifiers": _NULLABLE_STR,
                        },
                        "required": ["period", "unit", "scope", "other_qualifiers"],
                        "additionalProperties": False,
                    },
                    "source_page": {"type": "integer"},
                    "source_text": {"type": "string"},
                    "confidence": {"type": "number"},
                },
                "required": ["subject", "predicate", "value", "context",
                             "source_page", "source_text", "confidence"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["claims"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """\
You extract atomic, checkable claims from documents. You work on any kind of \
document; you have no expectations about the domain, and you must not assume \
which topics matter. Let the document decide what counts as a fact.

You will be given a slice of a PDF's extracted text. Page boundaries are marked \
with lines of the form <<<PAGE n>>>. The text comes from automatic PDF \
extraction, so tables may be flattened, columns may be interleaved, and spacing \
may be irregular. Work with the text as given.

WHAT TO EXTRACT
Extract each assertion the document makes that a careful reader could later \
verify or dispute against another document. That includes, but is not limited \
to: measured or reported quantities, dates and durations, counts, rates and \
ratios, statuses, roles and appointments, identifiers, locations, definitions, \
ratings, targets, and stated relationships between entities. Extract a claim \
whenever the document commits to something specific.

Do NOT extract: running headers and footers, page numbers, table-of-contents \
lines, navigation text, purely legal boilerplate that asserts nothing specific, \
or generic marketing language with no verifiable content. Do not extract \
anything you cannot quote verbatim.

FIELDS
- subject: the ENTITY the claim is about -- an organisation, country, person, \
  place, market, product, or population. Not the metric, and not the metric \
  plus a date. If the text uses a pronoun or a generic reference ("the \
  Company", "the Bank", "it") and the actual name appears anywhere in this \
  slice, use the actual name. If the whole slice is about one entity, use that \
  entity for every claim so the same subject is written the same way \
  throughout.
- predicate: the specific property being asserted about that subject, as a \
  short lowercase phrase. Include enough words to identify WHICH property it \
  is, so it stands on its own. Write it the way the document frames it, but \
  trim filler words. Do not invent a controlled vocabulary and do not force \
  different documents into the same wording -- near-duplicate predicates are \
  reconciled downstream.
- value: ONE atomic asserted value, exactly as written, including any magnitude \
  word that is part of it. If a sentence states several values -- a change from \
  one figure to another, a figure for each of several periods, a range \
  presented as two endpoints -- emit a SEPARATE claim for each, each with its \
  own context. Never pack more than one figure into a single value.

CRITICAL -- DO NOT DUPLICATE CONTEXT INTO subject OR predicate.
The time period, the unit, the scope and the qualifiers belong in `context` and \
nowhere else. Subject and predicate are what identify the same property across \
different documents, so anything that varies between documents must be kept out \
of them.
  WRONG: subject "FY24 revenue from services", predicate "amount"
  RIGHT: subject "<the named entity>", predicate "revenue from services",
         context.period "FY24"
  WRONG: subject "unemployment rate in 2023 (urban)", predicate "value"
  RIGHT: subject "<the named entity>", predicate "unemployment rate",
         context.period "2023", context.scope "urban"
A predicate of "amount", "value", "figure", "number" or "rate" on its own is \
always wrong -- it means the property name was left in the subject by mistake.
- context: ALWAYS provide all four keys; use null only when the document really \
  does not state it. These fields are what later distinguishes a genuine \
  contradiction from two compatible measurements, so read the surrounding text, \
  table headers, column titles, and section headings for them.
    * period: the time the value refers to (a year, fiscal year, quarter, range, \
      or as-of date), copied in the document's own words.
    * unit: the unit or denomination -- currency, scale word (thousand, million, \
      lakh, crore, billion), percent, count, index points, per-unit rates.
    * scope: what the value covers -- e.g. consolidated vs standalone, a named \
      segment, a geography, a subset of a population, an accounting basis.
    * other_qualifiers: anything else that changes how the value must be read: \
      estimate, projection, provisional, revised, restated, adjusted, \
      annualised, seasonally adjusted, attributed to a named source, or a \
      condition the claim depends on.
- source_page: the number from the <<<PAGE n>>> marker the quote came from.
- source_text: a CONTIGUOUS verbatim copy of the document text that states this \
  claim. This is the single most important field. Rules:
    * Copy characters exactly as they appear, including punctuation, spacing, \
      digits and symbols. Do not paraphrase, correct, reformat, or normalise.
    * It must be one unbroken run of text from ONE page, in the order the text \
      actually appears in the slice above. Never stitch together text from \
      lines that are not adjacent, from different columns, or from different \
      pages.
    * Charts and tables are the main trap here. Their labels and their numbers \
      are often far apart in the extracted text even though they look adjacent \
      on the page. Do NOT rebuild the visual layout. Quote only the contiguous \
      run that actually contains the value, even if that run looks incomplete \
      or contains neighbouring values -- a short honest quote is always better \
      than a reconstructed one.
    * It must contain the value, and ideally the subject cue too.
    * Aim for 15-300 characters. Prefer the shortest run that still supports \
      the claim.
    * If you cannot produce such a quote, do not emit the claim at all.
- confidence: 0-1, how sure you are that the quoted span really asserts this \
  claim with this context. Lower it when the subject is inferred, when the \
  period or unit had to be read from a distant header, or when the extracted \
  text is garbled.

Prefer precision over volume. A smaller set of well-grounded, well-qualified \
claims is worth more than many vague ones. If the slice contains nothing \
worth extracting, return an empty list."""

USER_TEMPLATE = """Document filename: {filename}
Pages {first}-{last} of {total}.

<document_slice>
{text}
</document_slice>

Extract the claims from this slice, following the rules exactly."""


@dataclass
class ChunkResult:
    chunk: Chunk
    claims: list[Claim]
    error: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0


class Extractor:
    def __init__(self, client: LLMClient | None = None, model: str | None = None):
        self.client = client or LLMClient(model=model)

    def _call(self, filename: str, total_pages: int, chunk: Chunk) -> ChunkResult:
        user = USER_TEMPLATE.format(
            filename=filename, first=chunk.first_page, last=chunk.last_page,
            total=total_pages, text=chunk.text,
        )
        try:
            result = self.client.complete_json(
                system=SYSTEM_PROMPT, user=user,
                schema=CLAIM_SCHEMA, schema_name="extracted_claims",
            )
        except Exception as e:
            # Network, rate limit, refusal, unparseable JSON -- all recorded per
            # chunk so a partial document still yields usable claims and the
            # failure stays visible in the ingest report.
            return ChunkResult(chunk=chunk, claims=[], error=f"{type(e).__name__}: {e}")

        payload = result.data
        if not isinstance(payload, dict) or "claims" not in payload:
            return ChunkResult(chunk=chunk, claims=[], error="response had no 'claims' key",
                               input_tokens=result.input_tokens,
                               output_tokens=result.output_tokens)

        claims: list[Claim] = []
        skipped = 0
        for raw in payload.get("claims") or []:
            try:
                claims.append(Claim.model_validate(_to_claim_dict(raw, filename)))
            except (ValidationError, KeyError, TypeError, ValueError):
                skipped += 1  # one malformed claim must not lose the whole chunk

        return ChunkResult(
            chunk=chunk, claims=claims,
            error=f"{skipped} malformed claim(s) discarded" if skipped else None,
            input_tokens=result.input_tokens, output_tokens=result.output_tokens,
        )

    def extract_document(self, filename: str, total_pages: int, chunks: list[Chunk],
                         concurrency: int | None = None,
                         progress=None) -> list[ChunkResult]:
        workers = concurrency or CONFIG.extract_concurrency
        results: list[ChunkResult] = []
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(self._call, filename, total_pages, ch): ch for ch in chunks}
            for fut in as_completed(futures):
                res = fut.result()
                results.append(res)
                if progress:
                    progress(len(results), len(chunks), res)
        results.sort(key=lambda r: r.chunk.index)
        return results


def _to_claim_dict(raw: dict, filename: str) -> dict:
    """Flatten the model's response shape into the assignment's Claim shape."""
    ctx = dict(raw.get("context") or {})
    return {
        "subject": raw["subject"],
        "predicate": raw["predicate"],
        "value": raw["value"],
        "context": {
            "period": ctx.get("period"), "unit": ctx.get("unit"),
            "scope": ctx.get("scope"), "basis": None, "geography": None,
            "as_of": None, "denominator": None,
            "other_qualifiers": ctx.get("other_qualifiers"),
        },
        "source_document": filename,
        "source_span": {"page": int(raw["source_page"]), "text": raw["source_text"]},
        "origin": "llm",
        "extraction_rule": "llm_chunk_extraction",
        "confidence": max(0.0, min(1.0, float(raw["confidence"]))),
    }
