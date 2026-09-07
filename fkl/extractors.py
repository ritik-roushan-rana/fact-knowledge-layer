"""Claim extractors behind one interface.

The deterministic extractor is the default and the only one required. The LLM
extractor is optional and opt-in; nothing in the pipeline assumes a provider is
configured, and the whole system runs with no API key set.

    DeterministicClaimExtractor   rules over structure and text  (default)
    LLMClaimExtractor             optional, whole-chunk extraction
    HybridClaimExtractor          rules first, LLM only where rules found
                                  nothing on a page that clearly contains data
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import pymupdf

from .config import CONFIG
from .extract_rules import detect_entities, sentence_claims, table_claims
from .models import Claim
from .pdf import Document, repeated_line_texts
from .tables import TableExtractor

log = logging.getLogger("fkl.extractors")


@dataclass
class ExtractionResult:
    claims: list[Claim]
    stats: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


class ClaimExtractor:
    """Interface. Implementations must not require any network access."""
    name: str = "base"
    requires_api_key: bool = False

    def extract(self, document: Document, progress=None) -> ExtractionResult:
        raise NotImplementedError


# --------------------------------------------------------------------------
class DeterministicClaimExtractor(ClaimExtractor):
    name = "deterministic"
    requires_api_key = False

    def __init__(self, use_tables: bool = True):
        self.use_tables = use_tables

    def extract(self, document: Document, progress=None) -> ExtractionResult:
        started = time.time()
        entity, known = detect_entities(document)
        furniture = repeated_line_texts(document)
        log.info("primary entity for %s: %r", document.filename, entity)

        claims: list[Claim] = []
        n_tables = 0
        errors: list[str] = []

        table_extractor = None
        native = None
        if self.use_tables:
            try:
                table_extractor = TableExtractor(document.path)
                native = pymupdf.open(document.path)
            except Exception as e:
                errors.append(f"table extraction unavailable: {type(e).__name__}: {e}")
                table_extractor = None

        try:
            for page in document.pages:
                try:
                    claims.extend(sentence_claims(document, page, entity, furniture, known))
                except Exception as e:
                    errors.append(f"page {page.number} sentence rules: {type(e).__name__}: {e}")

                if table_extractor is not None and native is not None:
                    try:
                        for table in table_extractor.tables_for_page(
                                native[page.number - 1], page.number):
                            n_tables += 1
                            claims.extend(table_claims(document, page, table, entity, known))
                    except Exception as e:
                        errors.append(f"page {page.number} tables: {type(e).__name__}: {e}")

                if progress:
                    progress("extracting", {"done": page.number,
                                            "total": len(document.pages),
                                            "claims": len(claims)})
        finally:
            if table_extractor is not None:
                table_extractor.close()
            if native is not None:
                native.close()

        by_origin: dict[str, int] = {}
        for c in claims:
            by_origin[c.origin] = by_origin.get(c.origin, 0) + 1

        return ExtractionResult(
            claims=claims,
            stats={
                "extractor": self.name,
                "primary_entity": entity,
                "known_entities": sorted(known)[:12],
                "tables_found": n_tables,
                "claims_by_origin": by_origin,
                "furniture_lines": len(furniture),
                "seconds": round(time.time() - started, 2),
                "llm_calls": 0,
                "input_tokens": 0,
                "output_tokens": 0,
            },
            errors=errors,
        )


# --------------------------------------------------------------------------
class LLMClaimExtractor(ClaimExtractor):
    """Optional. Only constructed when explicitly requested."""
    name = "llm"
    requires_api_key = True

    def __init__(self, client=None, model: str | None = None):
        from .extract import Extractor          # imported lazily: needs a provider
        self._impl = Extractor(client=client, model=model)

    def extract(self, document: Document, progress=None) -> ExtractionResult:
        from .pdf import chunk_document
        started = time.time()
        chunks = chunk_document(document, CONFIG.chunk_chars)

        def on_chunk(done, total, res):
            if progress:
                progress("extracting", {"done": done, "total": total,
                                        "claims": len(res.claims), "error": res.error})

        results = self._impl.extract_document(
            document.filename, document.n_pages, chunks, progress=on_chunk)
        claims, errors, tin, tout = [], [], 0, 0
        for r in results:
            claims.extend(r.claims)
            tin += r.input_tokens
            tout += r.output_tokens
            if r.error:
                errors.append(f"pages {r.chunk.first_page}-{r.chunk.last_page}: {r.error}")
        return ExtractionResult(claims=claims, errors=errors, stats={
            "extractor": self.name, "llm_calls": len(results),
            "input_tokens": tin, "output_tokens": tout,
            "claims_by_origin": {"llm": len(claims)},
            "seconds": round(time.time() - started, 2),
        })


# --------------------------------------------------------------------------
class HybridClaimExtractor(ClaimExtractor):
    """Deterministic everywhere, LLM only where the rules clearly failed.

    "Clearly failed" is defined narrowly and measurably: a page that contains
    numeric content but from which the rules extracted nothing. Those are the
    pages where structure was not recoverable -- exactly the ambiguity the LLM
    is meant to cover. Every other page never reaches a provider.
    """
    name = "hybrid"
    requires_api_key = True

    def __init__(self, max_llm_pages: int | None = None):
        self.deterministic = DeterministicClaimExtractor()
        self.max_llm_pages = (CONFIG.hybrid_max_llm_pages
                              if max_llm_pages is None else max_llm_pages)

    def extract(self, document: Document, progress=None) -> ExtractionResult:
        base = self.deterministic.extract(document, progress=progress)
        covered = {c.source_span.page for c in base.claims}

        gaps = [p for p in document.pages
                if p.number not in covered
                and sum(ch.isdigit() for ch in p.raw) > 40]
        gaps = gaps[: self.max_llm_pages]
        base.stats["hybrid_gap_pages"] = len(gaps)

        if not gaps:
            base.stats["extractor"] = self.name
            base.stats["llm_fallback_claims"] = 0
            return base

        try:
            from .extract import Extractor
            from .pdf import Chunk
            impl = Extractor()
        except Exception as e:
            base.errors.append(f"LLM fallback unavailable, deterministic results kept: {e}")
            base.stats["extractor"] = self.name
            base.stats["llm_fallback_claims"] = 0
            return base

        chunks = [Chunk(index=i, first_page=p.number, last_page=p.number,
                        text=f"\n<<<PAGE {p.number}>>>\n{p.raw}\n")
                  for i, p in enumerate(gaps)]
        results = impl.extract_document(document.filename, document.n_pages, chunks)
        added, tin, tout = [], 0, 0
        for r in results:
            added.extend(r.claims)
            tin += r.input_tokens
            tout += r.output_tokens
            if r.error:
                base.errors.append(f"llm fallback page {r.chunk.first_page}: {r.error}")

        base.claims.extend(added)
        base.stats.update({
            "extractor": self.name,
            "llm_calls": len(results),
            "llm_fallback_claims": len(added),
            "input_tokens": tin, "output_tokens": tout,
        })
        base.stats.setdefault("claims_by_origin", {})["llm"] = len(added)
        return base


def build_extractor(name: str | None = None) -> ClaimExtractor:
    """Factory. Defaults to deterministic -- never requires a key."""
    name = (name or CONFIG.extractor).lower()
    if name in ("deterministic", "rules", "default"):
        return DeterministicClaimExtractor()
    if name == "llm":
        return LLMClaimExtractor()
    if name == "hybrid":
        return HybridClaimExtractor()
    raise ValueError(f"Unknown extractor {name!r}. "
                     f"Choose from: deterministic, hybrid, llm.")
