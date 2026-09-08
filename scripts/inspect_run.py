"""One-shot inspection helper used from the CLI. Prints the four cases + status claims."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fkl.store import Store


def show(store, kind, limit=5):
    print(f"\n== {kind} (up to {limit}) ==")
    for r in store.query_relations(kind=kind, limit=limit):
        claims = store.get_claims([r["claim_a_id"], r["claim_b_id"]])
        a = claims[r["claim_a_id"]]
        b = claims[r["claim_b_id"]]
        print(f"  A: {a['subject']} | {a['predicate']} = {a['value']} "
              f"(period={a['ctx_period']!r}, scope={a['ctx_scope']!r})")
        print(f"     from {a['source_document']} p.{a['grounding_page'] or a['span_page']}")
        print(f"  B: {b['subject']} | {b['predicate']} = {b['value']} "
              f"(period={b['ctx_period']!r}, scope={b['ctx_scope']!r})")
        print(f"     from {b['source_document']} p.{b['grounding_page'] or b['span_page']}")
        print(f"  -> conf={r['confidence']}  decided_by={r['decided_by']}")
        print(f"     {r['explanation'][:220]}")
        print()


def main():
    s = Store()
    show(s, "contradiction", limit=10)
    show(s, "corroboration", limit=3)
    show(s, "component_of_total", limit=3)
    show(s, "reconciled", limit=2)
    show(s, "supersedes", limit=2)

    all_claims = s.query_claims(limit=10 ** 9)
    status = [c for c in all_claims if c["predicate"] == "status"]
    print(f"\n== status claims: {len(status)} total ==")
    for c in status[:10]:
        print(f"  {c['subject']} -> {c['value']}  "
              f"(period={c['ctx_period']!r}, doc={c['source_document']})")

    print(f"\n== totals ==")
    print(f"  claims:    {len(all_claims)}")
    print(f"  by origin: sentence={sum(1 for c in all_claims if c['origin']=='sentence')}, "
          f"table={sum(1 for c in all_claims if c['origin']=='table')}")
    print(f"  relations: {s.relation_counts()}")


if __name__ == "__main__":
    main()
