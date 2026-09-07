#!/usr/bin/env python
"""CLI: ingest one or more PDFs into the knowledge layer.

    python scripts/ingest.py starter-datasets/delhivery/*.pdf
    python scripts/ingest.py --max-pages 12 some.pdf     # quick smoke run
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fkl.pipeline import build_relations, ingest_pdf  # noqa: E402
from fkl.store import Store  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Ingest PDFs into the fact knowledge layer.")
    ap.add_argument("pdfs", nargs="+")
    ap.add_argument("--max-pages", type=int, default=None,
                    help="Only read the first N pages (useful for a fast smoke test).")
    ap.add_argument("--extractor", default=None,
                    choices=["deterministic", "hybrid", "llm"],
                    help="Extraction strategy. Default: deterministic (no API key needed).")
    ap.add_argument("--force", action="store_true",
                    help="Re-ingest even if this exact file was already completed.")
    ap.add_argument("--batch-id", default=None)
    ap.add_argument("--db", default=None)
    ap.add_argument("--json", action="store_true", help="Print the report as JSON.")
    ap.add_argument("--no-relate", action="store_true",
                    help="Skip cross-document relationship building.")
    ap.add_argument("--escalate", action="store_true",
                    help="Send genuinely ambiguous pairs to the LLM for adjudication. "
                         "Off by default: the pipeline is deterministic and needs no API key.")
    args = ap.parse_args()

    store = Store(args.db)

    def progress(stage: str, info: dict) -> None:
        if args.json:
            return
        if stage == "extracting":
            if info["done"] % 25 and info["done"] != info["total"]:
                return
            print(f"  page {info['done']}/{info['total']}: "
                  f"{info['claims']} claims so far", flush=True)
        else:
            print(f"[{stage}] {info}", flush=True)

    reports = []
    for pdf in args.pdfs:
        print(f"\n=== {pdf} ===", flush=True)
        report = ingest_pdf(pdf, store, max_pages=args.max_pages,
                            extractor=args.extractor, force=args.force,
                            batch_id=args.batch_id, progress=progress)
        reports.append(report.as_dict())
        if not args.json:
            r = report.as_dict()
            if r["skipped_resume"]:
                print(f"  already ingested ({r['claims_stored']} claims); skipped. "
                      f"Use --force to redo.")
            else:
                print(f"  stored={r['claims_stored']} grounded={r['claims_grounded']} "
                      f"quarantined={r['claims_quarantined']} "
                      f"review={r['claims_needing_review']} in {r['seconds']}s")

    if not args.no_relate:
        print("\n=== building cross-document relationships ===", flush=True)
        rel = build_relations(store, escalate=args.escalate, progress=progress)
        reports.append({"relations": rel.as_dict()})
        if not args.json:
            print(f"  {rel.as_dict()}")

    if args.json:
        print(json.dumps(reports, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
