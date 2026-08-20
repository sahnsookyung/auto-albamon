from html.parser import HTMLParser
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
VOID_ELEMENTS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


class LayoutParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []
        self.ancestors_by_id = {}
        self.attributes_by_id = {}
        self.tags_by_id = {}
        self.classes = set()

    def handle_starttag(self, tag, attributes):
        values = dict(attributes)
        classes = set(values.get("class", "").split())
        self.classes.update(classes)
        element_id = values.get("id")
        if element_id:
            self.ancestors_by_id[element_id] = tuple(self.stack)
            self.attributes_by_id[element_id] = values
            self.tags_by_id[element_id] = tag
        if tag not in VOID_ELEMENTS:
            self.stack.append((tag, element_id, classes))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                return


def has_ancestor(parser, element_id, *, element_id_value=None, class_name=None):
    return any(
        (element_id_value is None or ancestor_id == element_id_value)
        and (class_name is None or class_name in classes)
        for _, ancestor_id, classes in parser.ancestors_by_id[element_id]
    )


class DashboardLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        cls.styles = (ROOT / "web" / "styles.css").read_text(encoding="utf-8")
        cls.parser = LayoutParser()
        cls.parser.feed(cls.html)

    def test_source_tabs_and_origin_share_the_context_bar(self):
        self.assertTrue(has_ancestor(self.parser, "albamon-desk-tab", class_name="context-bar"))
        self.assertTrue(has_ancestor(self.parser, "hotels-desk-tab", class_name="context-bar"))
        self.assertTrue(has_ancestor(self.parser, "origin-heading", class_name="context-bar"))

    def test_filters_are_nested_with_their_result_tables(self):
        self.assertTrue(has_ancestor(self.parser, "search-filter", element_id_value="results-panel"))
        self.assertTrue(has_ancestor(self.parser, "hotel-search-filter", class_name="hotel-table-card"))
        self.assertTrue(has_ancestor(self.parser, "hotel-date-form", class_name="hotel-table-card"))
        self.assertTrue(has_ancestor(self.parser, "hotel-search-filter", class_name="hotel-controls-disclosure"))
        self.assertTrue(has_ancestor(self.parser, "hotel-date-form", class_name="hotel-controls-disclosure"))
        self.assertIn('grid-template-areas: "table visual"', self.styles)
        self.assertTrue(has_ancestor(self.parser, "schedule-filter", class_name="advanced-filter-disclosure"))
        self.assertTrue(has_ancestor(self.parser, "hotel-shift-start", class_name="advanced-filter-disclosure"))
        self.assertTrue(has_ancestor(self.parser, "hotel-saved-search", class_name="advanced-filter-disclosure"))

    def test_mobile_views_keep_decision_fields_without_wide_tables(self):
        self.assertIn('class="job-results-table"', self.html)
        self.assertIn('class="hotel-results-table"', self.html)
        self.assertIn(".job-results-table .job-pay-cell", self.styles)
        self.assertIn(".job-results-table .job-schedule-cell", self.styles)
        self.assertIn(".hotel-results-table .hotel-commute-cell", self.styles)
        self.assertIn(".hotel-results-table .hotel-options-cell", self.styles)
        self.assertIn(".hotel-visual-heading > div:first-child { flex: 0 1 auto; }", self.styles)
        self.assertIn(".hotel-date-disclosure > summary { display: grid;", self.styles)
        self.assertIn("html { min-width: 320px; overflow-x: clip;", self.styles)
        self.assertNotIn("th:nth-child(3), td:nth-child(3) { display: none; }", self.styles)
        self.assertNotIn("th:nth-child(4), td:nth-child(4) { display: none; }", self.styles)
        self.assertTrue(has_ancestor(self.parser, "hotel-list-tab", class_name="hotel-view-tabs"))
        self.assertTrue(has_ancestor(self.parser, "hotel-visual-tab", class_name="hotel-view-tabs"))

    def test_secondary_reference_and_legends_start_collapsed(self):
        self.assertNotIn("open", self.parser.attributes_by_id["agency-catalog-panel"])
        self.assertTrue(has_ancestor(self.parser, "agency-catalog-heading", class_name="agency-catalog-panel"))
        self.assertTrue(has_ancestor(self.parser, "hotel-chart-legend", class_name="visual-legend-disclosure"))
        self.assertTrue(has_ancestor(self.parser, "map-legend", class_name="visual-legend-disclosure"))

    def test_category_explanations_are_tooltips_instead_of_a_permanent_guide(self):
        self.assertNotIn("queue-guide", self.parser.classes)
        for card_id in ("summary-new-card", "summary-actionable-card", "summary-attention-card"):
            self.assertTrue(self.parser.attributes_by_id[card_id].get("data-help"))
        for description_id in ("summary-new-help", "summary-actionable-detail", "summary-attention-detail"):
            self.assertIn("sr-only", self.parser.attributes_by_id[description_id]["class"].split())
        self.assertTrue(has_ancestor(self.parser, "hotel-visual-description", class_name="visual-copy"))
        self.assertIn(".visual-info-button:hover + .visual-help", self.styles)

    def test_visual_help_and_secondary_details_use_semantic_disclosures(self):
        self.assertEqual("button", self.parser.tags_by_id["hotel-visual-info"])
        self.assertEqual("span", self.parser.tags_by_id["hotel-visual-description"])
        self.assertIn('class="visual-info-button"', self.html)
        self.assertEqual("tooltip", self.parser.attributes_by_id["hotel-visual-description"].get("role"))
        self.assertTrue(has_ancestor(self.parser, "pipeline-note", class_name="pipeline-disclosure"))
        self.assertNotIn("hotel-summary-total", self.parser.attributes_by_id)
        self.assertIn("hotel-summary-commute", self.parser.attributes_by_id)


if __name__ == "__main__":
    unittest.main()
