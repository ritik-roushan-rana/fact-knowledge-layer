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
    ap.add_argument("--db", default=None)
    ap.add_argument("--json", action="store_true", help="Print the report as JSON.")
    ap.add_argument("--no-relate", action="store_true",
                    help="Skip cross-document relationship building.")
    ap.add_argument("--no-escalate", action="store_true",
                    help="Rules only; never escalate ambiguous pairs to the LLM.")
    args = ap.parse_args()

    store = Store(args.db)

    def progress(stage: str, info: dict) -> None:
        if args.json:
            return
        if stage == "extracting":
            msg = f"  chunk {info['done']}/{info['total']}: {info['claims']} claims"
            if info.get("error"):
                msg += f"  [ERROR: {info['error']}]"
            print(msg, flush=True)
        else:
            print(f"[{stage}] {info}", flush=True)

    reports = []
    for pdf in args.pdfs:
        print(f"\n=== {pdf} ===", flush=True)
        report = ingest_pdf(pdf, store, max_pages=args.max_pages, progress=progress)
        reports.append(report.as_dict())
        if not args.json:
            r = report.as_dict()
            print(f"  stored={r['claims_stored']} grounded={r['claims_grounded']} "
                  f"review={r['claims_needing_review']} "
                  f"tokens_in={r['input_tokens']} tokens_out={r['output_tokens']}")

    if not args.no_relate:
        print("\n=== building cross-document relationships ===", flush=True)
        rel = build_relations(store, escalate=not args.no_escalate, progress=progress)
        reports.append({"relations": rel.as_dict()})
        if not args.json:
            print(f"  {rel.as_dict()}")

    if args.json:
        print(json.dumps(reports, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
