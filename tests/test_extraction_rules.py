"""Rules that decide what counts as a measurement, a predicate, or content.

Each case here was a real bogus claim produced on a real document before the
rule existed.
"""
import pytest

from fkl.extract_rules import _is_measurement_cell, _plausible_predicate, end_matter_start
from fkl.normalize import compare_periods, period_signature
from fkl.pdf import Line, Page, Document, normalize_with_map
from fkl.tables import score_grid


def _page(number, raw):
    norm, mapping = normalize_with_map(raw)
    lines, cursor = [], 0
    for text in raw.split("\n"):
        lines.append(Line(text=text, bbox=(0, cursor, 500, cursor + 9), size=10.0,
                          bold=False, char_start=cursor, char_end=cursor + len(text),
                          block_no=0))
        cursor += len(text) + 1
    return Page(number=number, label=None, raw=raw, norm=norm,
                norm_to_raw=mapping, lines=lines)


class TestMeasurementCells:
    @pytest.mark.parametrize("cell", ["2,113", "0.93%", "5", "(1,008)", "₹1,255.98", "-4.9"])
    def test_real_figures_are_kept(self, cell):
        assert _is_measurement_cell(cell)

    @pytest.mark.parametrize("cell", [
        "1/", "2*",                        # marked footnote references
        "1Q", "1Q24", "FY24", "2023-24",   # periods label a figure, they are not one
        "expiry of 60 days from the date", # prose that merely contains a number
        "",
    ])
    def test_non_measurements_are_rejected(self, cell):
        assert not _is_measurement_cell(cell)


class TestPredicates:
    @pytest.mark.parametrize("text", ["revenue", "net profit", "cpi inflation", "headcount"])
    def test_real_properties_are_kept(self, text):
        assert _plausible_predicate(text)

    @pytest.mark.parametrize("text", ["net", "total", "real", "consolidated", "estimate"])
    def test_a_bare_qualifier_names_no_property(self, text):
        """"net" modifies a property; it is not one. Predicates like these
        compared unrelated figures that merely shared the word."""
        assert not _plausible_predicate(text)


class TestPeriodForms:
    def test_finance_style_quarter(self):
        assert period_signature("1Q24").quarters == {1}
        assert period_signature("3Q").quarters == {3}

    def test_quarter_still_differs_from_its_year(self):
        assert compare_periods("FY24", "Q4 FY24")[0] == "different"


class TestEndMatter:
    def test_references_heading_ends_the_document(self):
        doc = Document(doc_id="d", filename="d.pdf", path=None, n_pages=4, pages=[
            _page(1, "Intro\nThe rate was 4.0 per cent.\n"),
            _page(2, "Results\nGrowth reached 6.5 per cent.\n"),
            _page(3, "Discussion\nMore text here.\n"),
            _page(4, "References\n[1] Someone. arXiv preprint arXiv:1903.02613\n"),
        ])
        found = end_matter_start(doc)
        assert found is not None
        assert found[0] == 4

    def test_no_references_section_means_no_cutoff(self):
        doc = Document(doc_id="d", filename="d.pdf", path=None, n_pages=2, pages=[
            _page(1, "Intro\nThe rate was 4.0 per cent.\n"),
            _page(2, "Results\nGrowth reached 6.5 per cent.\n"),
        ])
        assert end_matter_start(doc) is None


class TestTableGrids:
    def test_prose_columns_are_not_a_table(self):
        """A two-column page layout has a gutter that looks exactly like a
        column separator; the resulting grid is prose, not data."""
        grid = [["Automated tools to detect these malicious packages on",
                 "SpiderScan adopts a graph-based behaviour model that"],
                ["registries become extremely important. In this work we",
                 "captures the semantics of the package under analysis."],
                ["present an approach that identifies malicious behaviour",
                 "We evaluate it against a curated corpus of packages."]]
        assert score_grid(grid) == 0.0

    def test_a_real_data_table_scores(self):
        grid = [["Metric", "FY23", "FY24"],
                ["Revenue", "7,225", "8,142"],
                ["EBITDA", "(452)", "127"]]
        assert score_grid(grid) > 0.34
