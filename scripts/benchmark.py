#!/usr/bin/env python
"""Benchmark the extraction strategies against each other on the same input.

    python scripts/benchmark.py --pages 16            # head-to-head, all modes
    python scripts/benchmark.py --modes deterministic --pages 0   # full corpus

Every mode ingests the same PDFs into its own throwaway database and then
builds relationships, so the numbers are comparable. Modes that need a provider
are skipped automatically when no key is configured, and that is reported
rather than hidden.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fkl.config import CONFIG  # noqa: E402
from fkl.pipeline import build_relations, ingest_pdf  # noqa: E402
from fkl.store import Store  # noqa: E402

METRICS = [
    ("pdf_pages", "PDF pages read"),
    ("claims", "claims extracted"),
    ("grounded", "grounded claims"),
    ("quarantined", "quarantined claims"),
    ("table_claims", "claims from tables"),
    ("candidate_pairs", "candidate pairs"),
    ("corroboration", "corroborations"),
    ("contradiction", "contradictions"),
    ("reconciled", "reconciliations"),
    ("partial_cover", "partial coverage"),
    ("supersedes", "supersedes"),
    ("underspecified", "underspecified"),
    ("llm_calls", "LLM calls"),
    ("llm_fallback_claims", "LLM fallback claims"),
    ("llm_fallback_pct", "LLM fallback %"),
    ("seconds", "runtime (s)"),
]


def has_key() -> bool:
    return bool(os.environ.get(CONFIG.llm_api_key_env))


def run_mode(mode: str, pdfs: list[Path], pages: int | None,
             escalate: bool, workdir: Path) -> dict:
    db = workdir / f"bench_{mode}.db"
    for suffix in ("", "-wal", "-shm"):
        Path(str(db) + suffix).unlink(missing_ok=True)
    store = Store(db)

    started = time.time()
    pdf_pages = claims = grounded = quarantined = 0
    llm_calls = llm_fallback = 0
    for pdf in pdfs:
        report = ingest_pdf(pdf, store, max_pages=pages, extractor=mode, force=True)
        pdf_pages += report.pages_read
        claims += report.stored
        grounded += report.grounded
        quarantined += report.quarantined
        llm_calls += report.extractor_stats.get("llm_calls", 0) or 0
        llm_fallback += report.extractor_stats.get("llm_fallback_claims", 0) or 0
    ingest_seconds = time.time() - started

    relations = build_relations(store, escalate=escalate)
    total_seconds = time.time() - started
    kinds = relations.by_kind

    return {
        "mode": mode,
        "pdf_pages": pdf_pages,
        "claims": claims,
        "grounded": grounded,
        "quarantined": quarantined,
        "table_claims": store.count_claims(origin="table"),
        "candidate_pairs": relations.candidates,
        "corroboration": kinds.get("corroboration", 0),
        "contradiction": kinds.get("contradiction", 0),
        "reconciled": kinds.get("reconciled", 0),
        "partial_cover": kinds.get("partial_cover", 0),
        "supersedes": kinds.get("supersedes", 0),
        "underspecified": kinds.get("underspecified", 0),
        "llm_calls": llm_calls + relations.escalated,
        "llm_fallback_claims": llm_fallback,
        "llm_fallback_pct": round(
            100.0 * (llm_fallback + relations.escalated) / max(1, claims), 2),
        "ingest_seconds": round(ingest_seconds, 1),
        "seconds": round(total_seconds, 1),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdfs", nargs="*", default=None)
    ap.add_argument("--pages", type=int, default=16,
                    help="Pages per PDF. 0 means the whole document.")
    ap.add_argument("--modes", nargs="*",
                    default=["deterministic", "hybrid", "llm"])
    ap.add_argument("--out", default="samples/BENCHMARK.md")
    args = ap.parse_args()

    repo = Path(__file__).resolve().parent.parent
    if args.pdfs:
        pdfs = [Path(p) for p in args.pdfs]
    else:
        pdfs = sorted((repo / "starter-datasets").glob("*/*.pdf"))
    pages = None if args.pages == 0 else args.pages
    workdir = repo / "data" / "bench"
    workdir.mkdir(parents=True, exist_ok=True)

    print(f"corpus: {len(pdfs)} PDFs, "
          f"{'all pages' if pages is None else f'first {pages} pages each'}")
    results, skipped = [], []
    for mode in args.modes:
        if mode in ("llm", "hybrid") and not has_key():
            skipped.append(mode)
            print(f"  {mode}: SKIPPED (no {CONFIG.llm_api_key_env} configured)")
            continue
        print(f"  running {mode} ...", flush=True)
        try:
            results.append(run_mode(mode, pdfs, pages,
                                    escalate=(mode != "deterministic"),
                                    workdir=workdir))
        except Exception as e:
            print(f"    FAILED: {type(e).__name__}: {e}")
            skipped.append(f"{mode} (failed: {type(e).__name__})")

    if not results:
        print("nothing to report")
        return 1

    header = "| metric | " + " | ".join(r["mode"] for r in results) + " |"
    sep = "|---|" + "---|" * len(results)
    lines = [
        "# Benchmark: deterministic vs LLM-based extraction", "",
        f"Corpus: **{len(pdfs)} PDFs**, "
        f"{'all pages' if pages is None else f'first {pages} pages of each'}.  ",
        f"Model when an LLM is used: `{CONFIG.llm_model}` via `{CONFIG.llm_base_url}`.  ",
        "Each mode ingests the same input into a fresh database and then builds "
        "relationships, so the columns are directly comparable.", "",
        header, sep,
    ]
    for key, label in METRICS:
        lines.append(f"| {label} | " + " | ".join(str(r.get(key, "")) for r in results) + " |")
    if skipped:
        lines += ["", f"_Not run: {', '.join(skipped)}._"]

    out = repo / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n")
    (out.parent / "benchmark.json").write_text(json.dumps(results, indent=2))

    print("\n" + "\n".join(lines[6:]))
    print(f"\nwrote {out}")
    shutil.rmtree(workdir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
