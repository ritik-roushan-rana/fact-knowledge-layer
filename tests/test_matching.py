"""Entity and predicate matching are independent gates.

The failure these prevent: two unrelated properties of the same company being
compared and reported as a confident contradiction.
"""
from fkl.entity import normalize_entity, same_entity
from fkl.predicate import same_predicate


class TestEntityMatching:
    def test_legal_suffix_variants_are_one_entity(self):
        assert normalize_entity("Delhivery Limited") == normalize_entity("Delhivery Ltd.")
        assert same_entity("Delhivery Limited", "Delhivery")[0]
        assert same_entity("Delhivery Ltd.", "Delhivery Limited")[0]

    def test_containment_matches(self):
        assert same_entity("India", "Republic of India")[0]

    def test_acronyms_match_their_expansion(self):
        assert same_entity("Reserve Bank of India", "RBI")[0]
        assert same_entity("International Monetary Fund", "IMF")[0]

    def test_different_organisations_never_merge(self):
        # The whole point of the gate: short names that share letters are not
        # the same company.
        assert not same_entity("Delhivery", "DHL")[0]

    def test_similar_country_names_do_not_merge(self):
        assert not same_entity("India", "Indonesia")[0]

    def test_unrelated_subjects_do_not_merge(self):
        assert not same_entity("Acme Corp", "Republic of India")[0]


class TestPredicateMatching:
    def test_identical_property_matches(self):
        assert same_predicate("revenue from operations", "revenue from operations")[0] == "same"

    def test_acronym_and_expansion_are_the_same_property(self):
        assert same_predicate("CPI inflation", "consumer price inflation")[0] == "same"
        assert same_predicate("GDP growth", "gross domestic product growth")[0] == "same"

    def test_single_extra_qualifier_is_still_the_same_measurement(self):
        assert same_predicate("cash", "cash balance")[0] == "same"

    def test_unrelated_properties_must_not_match(self):
        # Regression: "employee count" and "revenue" once compared as the same
        # property because they shared a subject.
        assert same_predicate("employee count", "revenue")[0] == "different"

    def test_deep_refinement_is_related_not_identical(self):
        # Regression: token_set_ratio scored a subset as a perfect match, so
        # "cash" matched "cash generated from operations".
        assert same_predicate("cash", "cash generated from operations")[0] == "related"
        assert same_predicate("profit", "profit / (loss) after tax")[0] == "related"

    def test_opposed_qualifiers_are_never_the_same_measurement(self):
        for a, b in [("net profit", "gross profit"),
                     ("real GDP growth", "nominal GDP growth"),
                     ("standalone revenue", "consolidated revenue")]:
            assert same_predicate(a, b)[0] == "related", (a, b)


class TestBlockingRejectsRateVersusAmount:
    def test_rate_and_amount_are_different_kinds(self):
        from fkl.match import measure_kind
        from tests.conftest import claim_row
        rate = claim_row(value="7.46%", ctx_unit="percent")
        amount = claim_row(value="8,142", ctx_unit="INR crore")
        assert measure_kind(rate) != measure_kind(amount)


class TestPredicateSpecificity:
    """Rules that keep `same` from being handed out on string overlap alone.

    Both were added after they produced false contradictions on the starter
    corpus -- the RBI and IMF banking tables against each other.
    """

    def test_different_denominators_are_not_the_same_ratio(self):
        # 0.70 on token_sort_ratio, but 'deposit' and 'gdp' are different
        # things to divide credit by.
        assert same_predicate("Credit-Deposit Ratio", "Credit-GDP Ratio")[0] == "related"
        assert same_predicate("currency-deposit ratio",
                              "credit-to-deposit ratio")[0] == "related"

    def test_hyphen_and_preposition_spellings_of_one_ratio_still_match(self):
        assert same_predicate("Credit-Deposit Ratio", "Credit-to-deposit ratio")[0] == "same"

    def test_word_variants_are_not_a_symmetric_difference(self):
        # 'operation' vs 'operating' is one word spelled two ways.
        assert same_predicate("revenue from operations", "operating revenue")[0] == "same"

    def test_a_deficit_is_not_the_thing_it_is_a_deficit_in(self):
        assert same_predicate("revenue", "revenue deficit")[0] == "related"
        assert same_predicate("trade", "trade surplus")[0] == "related"

    def test_a_plain_qualifier_still_means_the_same_measurement(self):
        # The rule above must not swallow ordinary qualifiers.
        assert same_predicate("cash", "cash balance")[0] == "same"
