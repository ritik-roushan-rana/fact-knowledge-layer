#!/usr/bin/env python
"""Recompute every verdict from the stored claims, without re-ingesting.

Fast regression test for a rule change. Reads every stored relation, runs
`compare_claims` again on the two claims, and prints only the pairs whose
verdict, rule_id, or explanation has changed. A rule change with no output
means the change did not affect the corpus. Non-empty output is the diff a
reader would need to look at.

    python scripts/replay.py                        # print the diff
    python scripts/replay.py --save                 # also persist the new verdicts
    python scripts/replay.py --kind contradiction   # scope to one verdict kind
    python scripts/replay.py --limit 500            # cap the sample

Fully deterministic. No API calls, no PDF re-reading. About 2 seconds on the
full starter corpus.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from time import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fkl.compare import compare_claims  # noqa: E402
from fkl.store import Store  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    ap.add_argument("--kind", default=None,
                    help="Only replay relations of this kind.")
    ap.add_argument("--limit", type=int, default=100000,
                    help="Cap on how many relations to check.")
    ap.add_argument("--save", action="store_true",
                    help="Persist the recomputed verdicts to the store.")
    args = ap.parse_args()

    store = Store(args.db)
    started = time()
    rels = store.query_relations(kind=args.kind, cross_document_only=False,
                                 exclude_unrelated=False, limit=args.limit)
    if not rels:
        print("No relations to replay.")
        return 0

    ids = {r["claim_a_id"] for r in rels} | {r["claim_b_id"] for r in rels}
    claims = store.get_claims(sorted(ids))

    changed = []
    unchanged = 0
    missing = 0
    for r in rels:
        a = claims.get(r["claim_a_id"])
        b = claims.get(r["claim_b_id"])
        if not a or not b:
            missing += 1
            continue
        fresh, _ = compare_claims(a, b, similarity=r["similarity"])
        drift = {}
        if fresh.kind != r["kind"]:
            drift["kind"] = (r["kind"], fresh.kind)
        if (fresh.rule_id or None) != (r.get("rule_id") or None):
            drift["rule_id"] = (r.get("rule_id"), fresh.rule_id)
        if fresh.explanation != r["explanation"]:
            drift["explanation_changed"] = True
        if drift:
            changed.append((r, fresh, drift))
        else:
            unchanged += 1

    elapsed = time() - started
    print(f"Replayed {len(rels)} relations in {elapsed:.2f}s. "
          f"{unchanged} unchanged, {len(changed)} drifted, {missing} skipped "
          f"(claim deleted).")
    if not changed:
        print("No drift. Every verdict reproduces from stored claims.")
        return 0

    # Group and summarise. A single rule change often affects many pairs, and
    # the interesting number is the transition count, not the row count.
    from collections import Counter
    transitions = Counter()
    for old, new, _ in changed:
        transitions[(old["kind"], new.kind)] += 1
    print("\nTransitions:")
    for (old_kind, new_kind), n in sorted(transitions.items(), key=lambda kv: -kv[1]):
        print(f"  {old_kind:22s} -> {new_kind:22s}  {n:>5}")

    # Show a handful of examples of each transition.
    seen = set()
    print("\nExamples:")
    for old, new, drift in changed:
        key = (old["kind"], new.kind)
        if key in seen:
            continue
        seen.add(key)
        a = claims[old["claim_a_id"]]
        b = claims[old["claim_b_id"]]
        print(f"\n  {old['kind']} (rule {old.get('rule_id')}) "
              f"-> {new.kind} (rule {new.rule_id})")
        print(f"    A: {a['subject']} | {a['predicate']} = {a['value']} "
              f"[{a['source_document']} p.{a['grounding_page'] or a['span_page']}]")
        print(f"    B: {b['subject']} | {b['predicate']} = {b['value']} "
              f"[{b['source_document']} p.{b['grounding_page'] or b['span_page']}]")
        print(f"    now: {new.explanation[:160]}")

    if args.save:
        # Insert the recomputed relations; INSERT OR REPLACE by relation_id.
        store.insert_relations([new for _, new, _ in changed])
        print(f"\nSaved {len(changed)} updated verdicts to the store.")
    else:
        print("\nRun with --save to persist these updates.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
