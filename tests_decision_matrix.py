"""Exercise the deterministic decision matrix on synthetic pairs.

No API and no PDFs: this is purely the comparison logic, so it runs offline and
documents exactly which combinations produce which verdict.
"""
from fkl.compare import compare_claims


def claim(**kw):
    base = dict(claim_id=kw.get("cid", "x"), subject="Acme Corp", predicate="annual revenue",
                value="100", ctx_period="FY24", ctx_unit="USD million", ctx_scope=None,
                ctx_other=None, final_confidence=0.9, source_document="d.pdf",
                span_page=1, grounding_page=1, span_text="", matched_text="")
    base.update(kw)
    return base


CASES = [
    ("same period, same value, different phrasing",
     claim(cid="a", value="USD 100 million"), claim(cid="b", value="$0.1 billion")),
    ("same period, different value",
     claim(cid="a", value="100"), claim(cid="b", value="145")),
    ("different period explains the gap",
     claim(cid="a", value="100", ctx_period="FY24"),
     claim(cid="b", value="145", ctx_period="FY23")),
    ("different scope explains the gap",
     claim(cid="a", value="100", ctx_scope="consolidated"),
     claim(cid="b", value="145", ctx_scope="standalone entity only")),
    ("unit scale differs but quantity is identical",
     claim(cid="a", value="8142", ctx_unit="Rs. crore"),
     claim(cid="b", value="81.42", ctx_unit="Rs. billion")),
    ("rate vs absolute -> incomparable",
     claim(cid="a", value="6.4%", ctx_unit="percent"),
     claim(cid="b", value="6.4", ctx_unit="USD million")),
    ("rates that genuinely differ",
     claim(cid="a", value="6.4%", ctx_unit="percent"),
     claim(cid="b", value="7.2%", ctx_unit="percent")),
    ("one side omits the period -> ambiguous, escalate",
     claim(cid="a", value="100", ctx_period="FY24"),
     claim(cid="b", value="145", ctx_period=None)),
    ("semantic values, same context",
     claim(cid="a", predicate="board status", value="resigned", ctx_unit=None),
     claim(cid="b", predicate="board status", value="has resigned", ctx_unit=None)),
    ("semantic contradiction",
     claim(cid="a", predicate="board status", value="continues as director", ctx_unit=None),
     claim(cid="b", predicate="board status", value="resigned", ctx_unit=None)),
]

if __name__ == "__main__":
    width = max(len(n) for n, _, _ in CASES)
    for name, a, b in CASES:
        rel, escalate = compare_claims(a, b, similarity=0.95)
        flag = "  [-> LLM]" if escalate else ""
        print(f"{name:<{width}}  {rel.kind:<14}{flag}")
        print(f"{'':<{width}}  {rel.explanation}")
        print()
