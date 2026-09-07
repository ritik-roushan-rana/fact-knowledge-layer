"""Units, periods and rates normalise before anything is compared."""
from fkl.normalize import compare_periods, compare_values, parse_value


class TestUnitEquivalence:
    def test_crore_million_billion_are_the_same_quantity(self):
        crore = parse_value("₹8,142 crore").number
        million = parse_value("81,420 million").number
        billion = parse_value("81.42 billion").number
        assert crore == million == billion == 8.142e10

    def test_currency_scale_difference_is_not_a_disagreement(self):
        verdict, why, _ = compare_values("₹8,142 crore", "INR 81.42 billion")
        assert verdict in ("equal", "equivalent"), why

    def test_the_assignment_example_crore_vs_million(self):
        # 8,142 crore == 81,415.38 million to within reporting precision
        verdict, why, _ = compare_values("8,142 crore", "81,415.38 million")
        assert verdict in ("equal", "equivalent"), why

    def test_different_currencies_are_not_comparable(self):
        verdict, _why, _ = compare_values("12.4 million euros", "$12.4 million")
        assert verdict == "incomparable"

    def test_parentheses_mean_negative(self):
        assert parse_value("(452)").number == -452.0


class TestPercentages:
    def test_rates_compare_in_percentage_points_not_relative_terms(self):
        verdict, why, detail = compare_values("4.0%", "2.8%")
        assert verdict == "differ"
        assert "percentage point" in why
        gap = abs(detail["a"]["number"] - detail["b"]["number"])
        assert round(gap, 2) == 1.2, why

    def test_same_rate_to_reported_precision(self):
        verdict, _why, _ = compare_values("6.40%", "6.4%")
        assert verdict in ("equal", "equivalent")

    def test_a_tenth_of_a_point_is_a_real_difference(self):
        # A relative tolerance would wrongly call these equal.
        verdict, _why, _ = compare_values("6.4%", "6.5%")
        assert verdict == "differ"


class TestFiscalYears:
    def test_fy24_equals_fy2024(self):
        assert compare_periods("FY24", "FY2024")[0] == "same"

    def test_fy24_equals_written_out_fiscal_span(self):
        assert compare_periods("FY24", "fiscal year 2023-24")[0] == "same"
        assert compare_periods("FY24", "2023-24 financial year")[0] == "same"

    def test_span_resolves_to_its_ending_year(self):
        assert compare_periods("2024-25", "FY25")[0] == "same"

    def test_quarter_is_not_the_full_year(self):
        assert compare_periods("FY24", "Q4 FY24")[0] == "different"

    def test_month_is_not_a_fiscal_year(self):
        assert compare_periods("September 2025", "FY25")[0] == "different"

    def test_different_years_differ(self):
        assert compare_periods("FY24", "FY23")[0] == "different"

    def test_absent_period_is_unknown_not_different(self):
        assert compare_periods("FY24", None)[0] == "unknown"
