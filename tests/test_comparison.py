"""Context-aware comparison: the verdict matrix.

Each required case from the brief is pinned here, together with the false
positives the design exists to prevent.
"""
from tests.conftest import claim_row, relation_of, verdict


class TestCorroboration:
    def test_same_quantity_written_at_different_scales(self):
        """₹8,142 crore and 81,415.38 million are the same figure."""
        a = claim_row(cid="a", doc="deck.pdf", predicate="revenue from operations",
                      value="8,142", ctx_unit="INR crore", ctx_period="FY24")
        b = claim_row(cid="b", doc="annual.pdf", predicate="revenue from operations",
                      value="81,415.38", ctx_unit="INR million", ctx_period="FY24")
        rel = relation_of(a, b)
        assert rel.kind == "corroboration", rel.explanation
        assert rel.value_agreement in ("equal", "equivalent")

    def test_corroboration_across_fiscal_year_spellings(self):
        a = claim_row(cid="a", predicate="revenue", value="8,142",
                      ctx_unit="INR crore", ctx_period="FY24")
        b = claim_row(cid="b", doc="b.pdf", predicate="revenue", value="8,142",
                      ctx_unit="INR crore", ctx_period="fiscal year 2023-24")
        assert verdict(a, b) == "corroboration"

    def test_agreeing_values_with_no_scope_stated_is_corroboration(self):
        a = claim_row(cid="a", predicate="headcount", value="12,500", ctx_period="FY24")
        b = claim_row(cid="b", doc="b.pdf", predicate="headcount", value="12,500",
                      ctx_period="FY24")
        assert verdict(a, b) == "corroboration"


class TestContradiction:
    def test_two_reported_rates_that_cannot_both_hold(self):
        """RBI 4.0% vs IMF 2.8% CPI inflation for the same period."""
        a = claim_row(cid="a", doc="rbi.pdf", subject="Republic of India",
                      predicate="CPI inflation", value="4.0%", ctx_unit="percent",
                      ctx_period="FY2025-26")
        b = claim_row(cid="b", doc="imf.pdf", subject="India",
                      predicate="consumer price inflation", value="2.8%",
                      ctx_unit="percent", ctx_period="FY2025-26")
        rel = relation_of(a, b)
        assert rel.kind == "contradiction", rel.explanation
        assert "percentage point" in rel.explanation
        assert round(rel.value_delta["percentage_point_difference"], 2) == 1.2
        # the trace must record the rules that actually fired
        joined = " ".join(rel.reasoning_trace)
        assert "entity gate" in joined and "predicate gate" in joined
        assert "period" in joined

    def test_differing_amounts_under_identical_context(self):
        a = claim_row(cid="a", predicate="headcount", value="12,500", ctx_period="FY24")
        b = claim_row(cid="b", doc="b.pdf", predicate="headcount", value="14,900",
                      ctx_period="FY24")
        assert verdict(a, b) == "contradiction"


class TestReconciliation:
    def test_standalone_versus_consolidated(self):
        a = claim_row(cid="a", doc="a.pdf", predicate="revenue", value="74,540.82",
                      ctx_unit="INR million", ctx_period="FY24", ctx_scope="standalone")
        b = claim_row(cid="b", doc="b.pdf", predicate="revenue", value="81,415.38",
                      ctx_unit="INR million", ctx_period="FY24", ctx_scope="consolidated")
        rel = relation_of(a, b)
        assert rel.kind == "reconciled", rel.explanation
        assert "scope" in rel.context_diff
        assert "standalone" in rel.explanation and "consolidated" in rel.explanation

    def test_different_period_explains_the_gap(self):
        a = claim_row(cid="a", predicate="revenue", value="8,142",
                      ctx_unit="INR crore", ctx_period="FY24")
        b = claim_row(cid="b", doc="b.pdf", predicate="revenue", value="2,076",
                      ctx_unit="INR crore", ctx_period="Q4 FY24")
        rel = relation_of(a, b)
        assert rel.kind == "reconciled"
        assert "period" in rel.context_diff


class TestSupersedes:
    def test_advance_estimate_versus_later_reported_figure(self):
        a = claim_row(cid="a", doc="a.pdf", subject="India", predicate="real GDP growth",
                      value="6.4%", ctx_unit="percent", ctx_period="FY25",
                      modality="estimate")
        b = claim_row(cid="b", doc="b.pdf", subject="India", predicate="real GDP growth",
                      value="6.5%", ctx_unit="percent", ctx_period="FY25",
                      modality="revised")
        rel = relation_of(a, b)
        assert rel.kind == "supersedes", rel.explanation
        assert "revision is not a contradiction" in rel.explanation


class TestPartialCoverage:
    def test_segment_at_plausible_share_is_a_component_of_total(self):
        """Named segment at 81% of the total must not read as a conflict, and the
        pairwise upgrade should label it component_of_total rather than the
        weaker partial_cover -- the ratio does fit a component pattern."""
        total = claim_row(cid="a", doc="a.pdf", predicate="revenue",
                          value="8,142", ctx_unit="INR crore", ctx_period="FY24")
        segment = claim_row(cid="b", doc="b.pdf",
                            predicate="revenue from express parcel segment",
                            value="6,595", ctx_unit="INR crore", ctx_period="FY24")
        rel = relation_of(total, segment)
        assert rel.kind == "component_of_total", rel.explanation
        assert rel.kind != "contradiction"

    def test_tiny_segment_of_a_total_falls_back_to_partial_cover(self):
        """A narrower value that is only 2% of the broader one is too small to
        confirm as a component; the pair should keep the weaker partial_cover
        label, but must never be reported as a contradiction."""
        total = claim_row(cid="a", doc="a.pdf", predicate="revenue",
                          value="8,142", ctx_unit="INR crore", ctx_period="FY24")
        tiny = claim_row(cid="b", doc="b.pdf",
                         predicate="revenue from spot logistics services",
                         value="160", ctx_unit="INR crore", ctx_period="FY24")
        rel = relation_of(total, tiny)
        assert rel.kind == "partial_cover", rel.explanation
        assert rel.kind != "contradiction"


class TestPrecisionTolerance:
    """Tolerance from the value's own precision, not a flat epsilon.

    `8,142 crore` is written to the nearest whole crore (±0.5 Cr, or ±5 Mn).
    `81,415.38 million` is written to two decimals of a million (±0.005 Mn).
    The coarser precision is ±5 Mn, and the actual gap is 3.38 Mn, well
    inside. Two figures written to different precisions agree when they
    agree at the coarser one's rounding boundary.
    """

    def test_same_value_at_different_precisions_agrees(self):
        coarse = claim_row(cid="a", predicate="revenue",
                           value="8,142", ctx_unit="INR crore", ctx_period="FY24")
        fine = claim_row(cid="b", doc="b.pdf", predicate="revenue",
                         value="81,415.38", ctx_unit="INR million", ctx_period="FY24")
        rel = relation_of(coarse, fine)
        assert rel.kind == "corroboration", rel.explanation
        assert "precision" in rel.explanation.lower() or "coarser" in rel.explanation.lower()

    def test_a_rate_written_to_one_decimal_matches_that_decimal(self):
        a = claim_row(cid="a", predicate="inflation rate", value="3.5%",
                      ctx_unit="percent", ctx_period="FY25")
        b = claim_row(cid="b", doc="b.pdf", predicate="inflation rate",
                      value="3.51%", ctx_unit="percent", ctx_period="FY25")
        rel = relation_of(a, b)
        assert rel.kind == "corroboration", rel.explanation

    def test_a_precision_wide_gap_still_contradicts(self):
        """Precision tolerance doesn't hide a real difference: 78.1 vs 80.3
        (both to one decimal, precision ±0.05) still disagree by 2.2, which
        is 44x their precision. The rule must fire."""
        a = claim_row(cid="a", doc="rbi.pdf", subject="India",
                      predicate="Credit-Deposit Ratio", value="78.1",
                      origin="table", ctx_period="2023-24", ctx_scope="Combined")
        b = claim_row(cid="b", doc="imf.pdf", subject="India",
                      predicate="Credit-to-deposit ratio", value="80.3",
                      origin="table", ctx_period="2023/24", ctx_scope=None)
        assert relation_of(a, b).kind == "contradiction"


class TestFalsePositivesRejected:
    def test_unrelated_predicates_are_never_compared(self):
        a = claim_row(cid="a", predicate="employee count", value="12,500", ctx_period="FY24")
        b = claim_row(cid="b", doc="b.pdf", predicate="revenue", value="8,142",
                      ctx_unit="INR crore", ctx_period="FY24")
        assert verdict(a, b) == "unrelated"

    def test_same_predicate_different_entity_is_never_compared(self):
        a = claim_row(cid="a", subject="Republic of India", predicate="CPI inflation",
                      value="4.0%", ctx_unit="percent", ctx_period="FY25")
        b = claim_row(cid="b", doc="b.pdf", subject="Acme Corp", predicate="CPI inflation",
                      value="2.8%", ctx_unit="percent", ctx_period="FY25")
        assert verdict(a, b) == "unrelated"

    def test_bare_percentages_of_unknown_bases_are_not_contradictions(self):
        """"revenue = 33%" and "revenue = 7.46%" are shares of different bases."""
        a = claim_row(cid="a", predicate="revenue", value="33.0%", ctx_unit="percent",
                      ctx_period="FY24")
        b = claim_row(cid="b", doc="b.pdf", predicate="revenue", value="7.46%",
                      ctx_unit="percent", ctx_period="FY24")
        assert verdict(a, b) == "underspecified"


class TestMissingContext:
    def test_a_qualifier_stated_by_only_one_side_is_not_a_disagreement(self):
        """Missing context must never manufacture a contradiction."""
        a = claim_row(cid="a", predicate="revenue", value="74,540.82",
                      ctx_unit="INR million", ctx_period="FY24", ctx_scope="standalone")
        b = claim_row(cid="b", doc="b.pdf", predicate="revenue", value="81,415.38",
                      ctx_unit="INR million", ctx_period="FY24", ctx_scope=None)
        rel = relation_of(a, b)
        assert rel.kind == "underspecified", rel.explanation
        assert rel.kind != "contradiction"

    def test_aggregate_scope_on_one_side_equates_to_unstated_on_the_other(self):
        """Combined/Total/Aggregate mean 'over all groups', which is exactly what
        an unstated scope implicitly means. Forcing this pair into underspecified
        would hide a real disagreement between two aggregates."""
        # Same shape as the RBI Combined 78.1 vs IMF 80.3 credit-deposit pair,
        # after the spanning-header fix labelled the RBI column "Combined".
        a = claim_row(cid="a", doc="rbi.pdf", subject="India",
                      predicate="Credit-Deposit Ratio", value="78.1",
                      origin="table", ctx_period="2023-24", ctx_scope="Combined")
        b = claim_row(cid="b", doc="imf.pdf", subject="India",
                      predicate="Credit-to-deposit ratio", value="80.3",
                      origin="table", ctx_period="2023/24", ctx_scope=None)
        rel = relation_of(a, b)
        assert rel.kind == "contradiction", rel.explanation
        assert any("aggregate over all groups" in t for t in rel.reasoning_trace)

    def test_weak_extraction_cannot_produce_a_confident_contradiction(self):
        a = claim_row(cid="a", predicate="headcount", value="12,500",
                      ctx_period="FY24", final_confidence=0.2)
        b = claim_row(cid="b", doc="b.pdf", predicate="headcount", value="14,900",
                      ctx_period="FY24", final_confidence=0.95)
        rel = relation_of(a, b)
        assert rel.kind == "contradiction"
        assert rel.confidence <= 0.2, "verdict confidence must be bounded by the weaker claim"


class TestContradictionPreconditions:
    """`contradiction` is the only accusatory verdict, so it needs the numbers
    to be pinned down. These are the two ways the starter corpus showed they
    were not."""

    def test_a_unit_on_one_side_only_is_not_a_scale_difference(self):
        # "US$3.9 trillion" against a bare table cell "9.2" was being written
        # off as crore-versus-million and reported as a contradiction.
        a = claim_row(cid="a", doc="ar.pdf", subject="India", predicate="GDP",
                      value="US$3.9 trillion", ctx_period="2024")
        b = claim_row(cid="b", doc="rbi.pdf", subject="India", predicate="GDP",
                      value="9.2", origin="table", ctx_period="2024")
        rel = relation_of(a, b)
        assert rel.kind != "contradiction", rel.explanation
        assert any("only one claim states a unit" in t for t in rel.reasoning_trace)

    def test_two_unitless_table_cells_cannot_contradict(self):
        # "Deposits" is per-cent growth in one table and a share of GDP in
        # another. Same label, same period, different measurements.
        a = claim_row(cid="a", doc="rbi.pdf", subject="India",
                      predicate="deposits", value="3.5", origin="table",
                      ctx_period="2021-22")
        b = claim_row(cid="b", doc="imf.pdf", subject="India",
                      predicate="deposits", value="-2.4", origin="table",
                      ctx_period="2021/22")
        rel = relation_of(a, b)
        assert rel.kind == "underspecified", rel.explanation

    def test_a_named_ratio_survives_the_unitless_table_guard(self):
        # The guard must not silence the real finding: a ratio is
        # self-dimensioning, so two unitless ratio cells stay comparable.
        a = claim_row(cid="a", doc="rbi.pdf", subject="India",
                      predicate="Credit-Deposit Ratio", value="72.9",
                      origin="table", ctx_period="2022-23")
        b = claim_row(cid="b", doc="imf.pdf", subject="India",
                      predicate="Credit-to-deposit ratio", value="75.8",
                      origin="table", ctx_period="2022/23")
        rel = relation_of(a, b)
        assert rel.kind == "contradiction", rel.explanation

    def test_a_unitless_sentence_claim_is_exempt(self):
        # A sentence predicate arrives with the sentence that qualified it, so
        # the table-label guard does not apply to it.
        a = claim_row(cid="a", predicate="headcount", value="12,500", ctx_period="FY24")
        b = claim_row(cid="b", doc="b.pdf", predicate="headcount", value="14,900",
                      ctx_period="FY24")
        assert verdict(a, b) == "contradiction"
