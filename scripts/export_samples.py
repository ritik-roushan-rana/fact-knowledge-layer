#!/usr/bin/env python
"""Export the knowledge layer to committed sample files.

The assignment requires the result to be evaluable without the author's API
key. This writes the full extracted state as JSON plus a readable walkthrough
of the four required cases, chosen from real output rather than hand-picked
examples.

    python scripts/export_samples.py
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fkl.store import Store  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "samples"

CASES = [
    ("corroboration", "1. A fact corroborated across documents",
     "The same fact stated in two documents, in different words or at a different scale."),
    ("contradiction", "2. A genuine or likely contradiction",
     "Same entity, same property, comparable context -- and values that cannot both be right."),
    ("reconciled", "3. An apparent contradiction explained by context",
     "The values differ, but a difference in period, scope or unit accounts for it."),
    ("supersedes", "3b. A revision rather than a disagreement",
     "The same measurement at two stages -- an estimate and a later actual."),
    ("partial_cover", "3c. Partial coverage, not conflict",
     "One claim measures only part of what the other measures, so the values are "
     "not expected to match."),
]


def fmt_claim(c: dict, label: str) -> str:
    ctx = {k: c[f"ctx_{k}"] for k in ("period", "unit", "scope", "other")}
    ctx_s = ", ".join(f"{k}={v!r}" for k, v in ctx.items() if v) or "none stated"
    page = c["grounding_page"] or c["span_page"]
    return (
        f"**{label}** — `{c['source_document']}`, page {page}\n\n"
        f"| field | value |\n|---|---|\n"
        f"| subject | {c['subject']} |\n"
        f"| predicate | {c['predicate']} |\n"
        f"| value | **{c['value']}** |\n"
        f"| context | {ctx_s} |\n"
        f"| confidence | {c['final_confidence']:.2f} "
        f"(extraction {c['extraction_confidence']:.2f} x grounding {c['grounding_score']:.2f}) |\n"
        f"| origin | {c.get('origin','sentence')}"
        f"{' , bbox ' + str([round(v,1) for v in c['bbox']]) if c.get('bbox') else ''} |\n\n"
        f"> {(c['matched_text'] or c['span_text']).strip()}\n"
    )


def fmt_relation(r: dict) -> str:
    out = [fmt_claim(r["claim_a"], "Claim A"), "", fmt_claim(r["claim_b"], "Claim B"), ""]
    out.append(f"**Verdict: `{r['kind']}`** "
               f"(similarity {r['similarity']:.3f}, confidence {r['confidence']:.2f}, "
               f"decided by {'LLM adjudication' if r['decided_by']=='llm' else 'deterministic rules'})\n")
    out.append(f"{r['explanation']}\n")
    if r.get("context_diff"):
        diffs = "; ".join(f"**{k}**: {v[0]!r} vs {v[1]!r}" for k, v in r["context_diff"].items())
        out.append(f"Context differences — {diffs}\n")
    out.append("<details><summary>System reasoning trace</summary>\n")
    for step in r["reasoning_trace"]:
        out.append(f"- {step}")
    out.append("\n</details>\n")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    ap.add_argument("--max-rows", type=int, default=600,
                    help="Cap rows written to claims.json / relations.json so the "
                         "repository stays light. Aggregates are always complete.")
    args = ap.parse_args()

    store = Store(args.db)
    OUT.mkdir(exist_ok=True)

    docs = store.list_documents()
    claims = store.query_claims(limit=10**9)
    relations = store.query_relations(limit=10**9)
    ids = {r["claim_a_id"] for r in relations} | {r["claim_b_id"] for r in relations}
    by_id = store.get_claims(sorted(ids))
    for r in relations:
        r["claim_a"] = by_id.get(r["claim_a_id"])
        r["claim_b"] = by_id.get(r["claim_b_id"])

    stats = {
        "documents": len(docs),
        "claims": len(claims),
        "claims_needing_review": sum(1 for c in claims if c["needs_review"]),
        "relations_by_kind": store.relation_counts(),
        "claims_quarantined": sum(1 for c in claims if c["quarantined"]),
        "claims_from_tables": sum(1 for c in claims if c["origin"] == "table"),
        "claims_from_sentences": sum(1 for c in claims if c["origin"] == "sentence"),
        "grounding": {
            "exact_match": sum(1 for c in claims if c["grounding_score"] == 1.0),
            "fuzzy_match": sum(1 for c in claims if 0 < c["grounding_score"] < 1.0),
            "not_located": sum(1 for c in claims if not c["grounded"]),
        },
    }

    # Full aggregates, sampled rows. A reviewer needs enough real output to
    # judge the system, not every row of it.
    cap = args.max_rows

    def sample_claims(rows: list[dict]) -> list[dict]:
        quarantined = [c for c in rows if c["quarantined"]]
        table = [c for c in rows if c["origin"] == "table" and not c["quarantined"]]
        sentence = [c for c in rows if c["origin"] == "sentence" and not c["quarantined"]]
        keep = quarantined[:cap // 4] + sentence[:cap // 2] + table[:cap // 2]
        return keep[:cap]

    def sample_relations(rows: list[dict]) -> list[dict]:
        by_kind: dict[str, list[dict]] = {}
        for r in rows:
            by_kind.setdefault(r["kind"], []).append(r)
        keep: list[dict] = []
        # Every relation of a rare kind, a slice of the common ones.
        for kind, group in sorted(by_kind.items(), key=lambda kv: len(kv[1])):
            keep.extend(group[: max(40, cap // max(1, len(by_kind)))])
        return keep[:cap]

    claim_rows, relation_rows = sample_claims(claims), sample_relations(relations)
    stats["sampled_for_export"] = {
        "claims_written": len(claim_rows), "claims_total": len(claims),
        "relations_written": len(relation_rows), "relations_total": len(relations),
        "note": "Row files are a sample; the counts above are complete.",
    }

    (OUT / "stats.json").write_text(json.dumps(stats, indent=2))
    (OUT / "documents.json").write_text(json.dumps(docs, indent=2))
    (OUT / "claims.json").write_text(json.dumps(claim_rows, indent=2))
    (OUT / "relations.json").write_text(json.dumps(relation_rows, indent=2))

    # ---- the four required cases, picked from real output ----
    md = ["# Required Cases — real output from this system", "",
          "Generated by `scripts/export_samples.py` from the ingested corpus. ",
          "Every quote below was located programmatically in the source PDF; ",
          "none of it is hand-written.", "",
          f"Corpus: **{stats['documents']} documents**, **{stats['claims']} claims**, "
          f"relationships: `{stats['relations_by_kind']}`", "", "---", ""]

    for kind, title, blurb in CASES:
        md += [f"## {title}", "", blurb, ""]
        pool = [r for r in relations
                if r["kind"] == kind and r["claim_a"] and r["claim_b"]]
        pool.sort(key=lambda r: -r["confidence"])
        if not pool:
            md += [f"_No `{kind}` relationship in the current corpus._", "", "---", ""]
            continue
        for r in pool[:2]:
            md += [fmt_relation(r), "---", ""]

    md += ["## 4. An extraction or reasoning failure", "",
           "Both halves are selected by rule from the live output, not hand-picked: "
           "the first three are the lowest-confidence flagged claims, the last is "
           "the weakest surviving contradiction.", "",
           "### 4a. Failures the system caught", "",
           "Every claim below was proposed by the extractor and then quarantined or "
           "marked for review. The reason differs case by case and is printed as the "
           "system recorded it -- a span that could not be relocated in the page "
           "text, or a value that does not appear in the evidence it cites.", ""]
    bad = sorted([c for c in claims if c["quarantined"] or c["needs_review"]],
                 key=lambda c: c["final_confidence"])
    if not bad:
        md += ["_Nothing currently flagged._", ""]
    for c in bad[:3]:
        page = c["grounding_page"] or c["span_page"]
        md += [
            f"**`{c['source_document']}`** — claim: "
            f"*{c['subject']} — {c['predicate']} → {c['value']}*", "",
            f"- final confidence **{c['final_confidence']:.2f}**, "
            f"grounding score {c['grounding_score']:.2f}",
            "- flagged because: " + "; ".join(c["review_reasons"]), "",
            "What the model quoted:", "", f"> {c['span_text'].strip()}", "",
            f"Closest text actually in the document (page {page}):", "",
            f"> {(c['matched_text'] or '— not found anywhere —').strip()}", "", "---", "",
        ]

    # The interesting failure is not the one the guards caught -- it is the one
    # that walked through every gate. The weakest surviving contradiction is
    # where the rules are closest to being wrong, so that is what gets shown.
    md += ["### 4b. A failure that got through", "",
           "The verdict below is the lowest-confidence `contradiction` the system "
           "still asserts. Nothing flagged it; it passed the entity, predicate, "
           "period and value gates in order. It is shown because it is the most "
           "informative thing in the output about where the rules end.", ""]
    weak = sorted([r for r in relations if r["kind"] == "contradiction"],
                  key=lambda r: r["confidence"])
    if not weak:
        md += ["_No contradiction in the current corpus._", ""]
    else:
        md += [fmt_relation(weak[0]), "---", ""]

    (OUT / "REQUIRED_CASES.md").write_text("\n".join(md))

    print(f"wrote {OUT}/")
    for f in sorted(OUT.iterdir()):
        print(f"  {f.name:24s} {f.stat().st_size:>9,} bytes")
    print(f"\nstats: {json.dumps(stats)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
