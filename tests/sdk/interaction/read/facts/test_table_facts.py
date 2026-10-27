import copy
import unittest

from observation_fixture import normalize_fixture as normalizeFacts
from sdk.interaction.read.facts.test_native_facts import batch, edge, group_relation_sources, node
from sdk.interaction.read.presentation.accounting.test_relation_accounting import relation_records

from computer_use.interaction.read.numbering.references import DisplayReferences
from computer_use.interaction.read.presentation.rendering.formatting import renderTree


class TableFactsTests(unittest.TestCase):
    def observe(self, nodes, relations=()):
        raw = batch(nodes, relations)
        before = copy.deepcopy(raw)
        semantic = normalizeFacts(raw, DisplayReferences("session"))
        output = semantic.render()
        self.assertEqual(raw, before)
        self.assertEqual(group_relation_sources(semantic.facts), group_relation_sources(before))
        return semantic, output

    def test_related_cells_are_grouped_without_inventing_children_or_losing_bindings(self):
        semantic, output = self.observe(
            [
                node("table", AXRole="AXTable", AXTitle="Grid"),
                node("a", AXRole="AXCell", AXValue="one"),
                node("b", AXRole="AXCell", AXValue="two"),
            ],
            [edge("table", "a", "b", property="AXVisibleCells")],
        )
        self.assertEqual(semantic.roots[0]["children"], [])
        self.assertEqual(len(semantic.roots), 3)
        self.assertEqual(output["text"].count("Related elements (visible_cells of 0):"), 1)
        self.assertIn("\t1 cell", output["text"])
        self.assertIn("\t2 cell", output["text"])
        self.assertNotIn("\n\n\t2 cell", output["text"])
        self.assertEqual(relation_records(output)[("0", "visible_cells")]["targets"], ["1", "2"])
        self.assertEqual(output["refs"], {"0": "table", "1": "a", "2": "b"})

    def test_a_cell_on_both_children_and_visible_relations_is_rendered_once(self):
        semantic, output = self.observe(
            [node("table", AXRole="AXTable"), node("cell", AXRole="AXCell", AXValue="one")],
            [edge("table", "cell"), edge("table", "cell", property="AXVisibleCells")],
        )
        self.assertEqual(len(semantic.roots), 1)
        self.assertEqual(output["text"].count("1 cell"), 1)
        self.assertNotIn("Related elements", output["text"])
        self.assertEqual(relation_records(output)[("0", "visible_cells")]["targets"], ["1"])

    def test_membership_does_not_suppress_conflicting_parent_or_a_second_table(self):
        semantic, output = self.observe(
            [
                node("first", AXRole="AXTable"),
                node("second", AXRole="AXTable"),
                node("cell", AXRole="AXCell", AXValue="one"),
            ],
            [
                edge("first", "cell", property="AXVisibleCells"),
                edge("second", "cell", property="AXRows"),
                edge("cell", "second", property="AXParent"),
            ],
        )
        self.assertIn("Related elements (visible_cells of 0; rows of 1):", output["text"])
        self.assertEqual(relation_records(output)[("2", "parent")]["targets"], ["1"])
        self.assertEqual(semantic.roots[0]["children"], [])
        self.assertEqual(semantic.roots[1]["children"], [])

    def test_unconfirmed_membership_stays_an_annotation_not_a_grouping(self):
        semantic, output = self.observe(
            [node("table", AXRole="AXTable"), node("cell", AXRole="AXCell")],
            [edge("table", "cell", property="AXVisibleCells", certainty="unknown", state="partial")],
        )
        self.assertNotIn("Related elements", output["text"])
        relation = relation_records(output)[("0", "visible_cells")]
        self.assertEqual((relation["state"], relation["certainty"]), ("partial", "unknown"))
        self.assertIn("1 has no expanded containment path", output["text"])

    def test_unsupported_optional_relation_preserves_status_without_poisoning_layout(self):
        semantic, output = self.observe(
            [node("layout", AXRole="AXGroup"), node("label", AXRole="AXStaticText", AXValue="Name")],
            [edge("layout", "label"), edge("layout", property="AXRows", state="unsupported", certainty="unknown")],
        )
        self.assertNotIn("preserveGroup", semantic.roots[0])
        relation = relation_records(output)[("0", "rows")]
        self.assertEqual((relation["state"], relation["certainty"]), ("unsupported", "unknown"))
        self.assertIn("Name", output["text"])

    def test_cells_keep_positions_values_and_original_names_on_the_same_line(self):
        semantic, output = self.observe(
            [
                node("table", AXRole="AXTable", AXTitle="Inventory", AXRowCount=30, AXColumnCount=4),
                node(
                    "cell",
                    AXRole="AXCell",
                    AXTitle="Price",
                    AXValue="7.50",
                    AXRowIndexRange=dict(location=1, length=1),
                    AXColumnIndexRange=dict(location=2, length=1),
                ),
            ],
            [edge("table", "cell")],
        )
        self.assertIn("Table rows: 30, Table columns: 4", output["text"])
        line = next(line for line in output["text"].splitlines() if "Price" in line)
        self.assertIn("Row: 2, Column: 3", line)
        self.assertIn("7.50", line)
        self.assertEqual(semantic.fields[("1", "structure.row_index")]["representation"], "projection")
        self.assertEqual(output["refs"]["1"], "cell")

    def test_spanning_cells_preserve_both_ranges_without_guessing_an_address(self):
        _, output = self.observe(
            [
                node(
                    "cell",
                    AXRole="AXCell",
                    AXValue="Spanning",
                    AXRowIndexRange=dict(location=2, length=3),
                    AXColumnIndexRange=dict(location=1, length=2),
                )
            ]
        )
        self.assertIn("Rows: 3–5, Columns: 2–3", output["text"])
        self.assertNotIn("B3", output["text"], "Native grids do not necessarily use spreadsheet addresses")

    def test_empty_cells_and_repeated_values_remain_distinct_targets(self):
        _, output = self.observe(
            [
                node("table", AXRole="AXTable"),
                node(
                    "empty",
                    AXRole="AXCell",
                    AXValue="",
                    AXRowIndexRange=dict(location=0, length=1),
                    AXColumnIndexRange=dict(location=0, length=1),
                ),
                node(
                    "other",
                    AXRole="AXCell",
                    AXValue="",
                    AXRowIndexRange=dict(location=0, length=1),
                    AXColumnIndexRange=dict(location=1, length=1),
                ),
            ],
            [edge("table", "empty", "other")],
        )
        self.assertRegex(output["text"], r'1 cell.*Row: 1, Column: 1.*Value: ""')
        self.assertRegex(output["text"], r'2 cell.*Row: 1, Column: 2.*Value: ""')
        self.assertEqual(output["refs"], {"0": "table", "1": "empty", "2": "other"})

    def test_zero_counts_empty_ranges_and_unknown_indices_are_not_omitted(self):
        _, output = self.observe(
            [
                node(
                    "cell",
                    AXRole="AXCell",
                    AXRowCount=0,
                    AXColumnCount=0,
                    AXRowIndexRange=dict(location=4, length=0),
                    AXIndex=0,
                )
            ]
        )
        self.assertIn("Row range (0-based): 4:0", output["text"])
        self.assertIn("Table rows: 0, Table columns: 0", output["text"])
        self.assertIn("Index (0-based): 0", output["text"])
        self.assertNotIn("Row: 5", output["text"])

    def test_invalid_positions_are_raw_facts_not_invented_valid_positions(self):
        for value in [True, -1, 1.5, 9_007_199_254_740_992, "two"]:
            with self.subTest(value=value):
                semantic, output = self.observe([node("table", AXRole="AXTable", AXRowCount=value)])
                self.assertNotIn("rowCount", semantic.roots[0])
                self.assertTrue(
                    "provider.macos.AXRowCount" in output["text"] or "structure.row_count:" in output["text"]
                )
                self.assertNotIn("Table rows:", output["text"])
        for value in [
            dict(location=-1, length=1),
            dict(location=0, length=True),
            dict(location=9_007_199_254_740_991, length=1),
            dict(location=0, length=1, extra=0),
        ]:
            with self.subTest(value=value):
                semantic, output = self.observe([node("cell", AXRole="AXCell", AXRowIndexRange=value)])
                self.assertNotIn("rowRange", semantic.roots[0])
                self.assertTrue(
                    "provider.macos.AXRowIndexRange" in output["text"] or "structure.row_index:" in output["text"]
                )

    def test_missing_failed_and_unrequested_table_fields_remain_distinct(self):
        source = node("cell", AXRole="AXCell")
        source["requestedFields"].extend(["AXRowIndexRange", "AXColumnIndexRange"])
        source["fieldStatus"] = dict(
            AXRowIndexRange=dict(state="unsupported"), AXColumnIndexRange=dict(state="timeout", detail="provider busy")
        )
        semantic, output = self.observe([source])
        self.assertNotIn("rowRange", semantic.roots[0])
        self.assertIn("AXRowIndexRange: unsupported", output["text"])
        self.assertIn("AXColumnIndexRange: timeout", output["text"])
        self.assertNotIn("AXRowCount", output["text"])

    def test_position_protects_a_wrapper_even_outside_a_table(self):
        _, output = self.observe(
            [
                node("root", AXRole="AXWindow"),
                node("part", AXRole="AXGroup", AXIndex=0),
                node("label", AXRole="AXStaticText", AXValue="Content"),
            ],
            [edge("root", "part"), edge("part", "label")],
        )
        self.assertRegex(output["text"], r"1 container.*Index \(0-based\): 0")

    def test_editor_compression_does_not_drop_table_metadata(self):
        caps = dict(click="unavailable", typeText="available", replaceText="available", focus="available")
        tree = dict(
            ref="0",
            role="textField",
            value="same",
            capabilities=caps,
            control=dict(kind="textInput", members=[dict(ref="1", part="editor")]),
            children=[
                dict(
                    ref="1",
                    role="textField",
                    value="same",
                    capabilities=caps,
                    rowRange=dict(location=2, length=1),
                    children=[],
                )
            ],
        )
        output = renderTree(tree)
        self.assertRegex(output["text"], r"1 text field.*Row: 3")


if __name__ == "__main__":
    unittest.main()
