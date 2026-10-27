"""UIA fact fixtures exercise shared presentation, not a Windows GUI provider."""

import copy
import unittest
from types import SimpleNamespace

from observation_fixture import from_records, normalize_fixture as normalizeFacts, reply
from sdk.interaction.read.facts.test_native_facts import batch, edge, node

from computer_use.interaction.read.acquisition.reader import NativeReader
from computer_use.interaction.read.numbering.references import DisplayReferences
from computer_use.interaction.read.presentation.accounting.fields import account_fields
from computer_use.native.contracts import Binding


def windows_batch(nodes, relations=()):
    value = batch(nodes, relations)
    value["platform"] = "windows"
    return value


def cell(**changes):
    return node(
        "cell",
        **{
            "ControlType": "ControlType.DataItem",
            "Name": "Amount",
            "GridItem.Row": 2,
            "GridItem.Column": 3,
            "GridItem.RowSpan": 2,
            "GridItem.ColumnSpan": 1,
            **changes,
        },
    )


class WindowsTableProjectionTests(unittest.TestCase):
    def observe(self, nodes, relations=()):
        raw = windows_batch(nodes, relations)
        before = copy.deepcopy(raw)
        snapshot = normalizeFacts(raw, DisplayReferences("session"))
        output = snapshot.render()
        self.assertEqual(raw, before)
        self.assertEqual(snapshot.native, from_records(before))
        self.assertTrue(all(p.get("presentation") != "uncompressed" for p in output["projections"]))
        return snapshot, output

    def test_logical_coordinates_preserve_identity_role_and_each_source_property(self):
        snapshot, output = self.observe([cell()])
        self.assertIn("Rows: 3–4, Column: 4", output["text"])
        self.assertEqual(snapshot.roots[0]["role"], "row", "GridItem does not rewrite native ControlType")
        self.assertEqual(output["refs"], {"0": "cell"})
        coverage = output["projections"][0]["factAccounting"]["values"]
        for key in ("Row", "RowSpan", "Column", "ColumnSpan"):
            item = next(
                c
                for c in coverage
                if c["property"]
                == "structure."
                + {"Row": "row_index", "Column": "column_index", "RowSpan": "row_span", "ColumnSpan": "column_span"}[
                    key
                ]
            )
            self.assertEqual(item["kind"], "equivalent")
            self.assertEqual(item["projections"][0]["field"], key.removesuffix("Span").lower() + "Range")
        self.assertNotIn("selected", snapshot.roots[0])

    def test_missing_failed_or_invalid_pair_is_retained_without_guessed_defaults(self):
        cases = []
        for key in ("GridItem.Row", "GridItem.RowSpan"):
            missing = cell()
            missing["values"].pop(key)
            missing["requestedFields"].remove(key)
            cases.append(missing)
            failed = cell()
            failed["values"].pop(key)
            failed["fieldStatus"][key] = dict(state="error")
            cases.append(failed)
        for key, values in [
            ("GridItem.Row", (-1, True, 0.5, "2", 9_007_199_254_740_991)),
            ("GridItem.RowSpan", (0, -1, False, 0.5, "2")),
        ]:
            cases.extend(cell(**{key: value}) for value in values)
        for source in cases:
            with self.subTest(source=source):
                snapshot, output = self.observe([source])
                self.assertNotIn("rowRange", snapshot.roots[0])
                self.assertEqual(snapshot.roots[0]["columnRange"], dict(location=3, length=1))
                self.assertTrue("structure.row_" in output["text"] or "GridItem.Row" in output["text"])

    def test_projection_loss_keeps_both_raw_inputs(self):
        snapshot, _ = self.observe([cell()])
        residuals, accounting = account_fields(
            ["0"], snapshot.refs, snapshot.fields, {"cell": snapshot.facts["nodes"][0]}, {"fieldCoverage": []}
        )
        for key in ("structure.row_index", "structure.row_span", "structure.column_index", "structure.column_span"):
            self.assertEqual(next(c for c in accounting["values"] if c["property"] == key)["kind"], "supplement")
            self.assertTrue(any(key in label for label in residuals))

    def test_headers_selection_and_owner_do_not_become_containment_or_input_authority(self):
        snapshot, output = self.observe(
            [
                node("table", ControlType="ControlType.DataGrid", **{"Grid.RowCount": 80, "Grid.ColumnCount": 3}),
                cell(**{"SelectionItem.IsSelected": False}),
                node("header", ControlType="ControlType.HeaderItem", Name="Price"),
            ],
            [
                edge("table", "cell", property="Selection.Selection", state="partial", total=4, cursor="next"),
                edge("cell", "table", property="GridItem.ContainingGrid"),
                edge("cell", "table", property="SelectionItem.SelectionContainer"),
                edge("cell", "header", property="TableItem.ColumnHeaderItems"),
                edge("table", "header", property="Table.ColumnHeaders"),
            ],
        )
        self.assertEqual(snapshot.roots[0]["children"], [])
        self.assertFalse(snapshot.roots[1]["selected"])
        self.assertIn("Table rows: 80, Table columns: 3", output["text"])
        self.assertIn(
            "Selected children of 0: 1 (partial; 1 listed of 4; node reports selected=false: 1)", output["text"]
        )
        self.assertIn("Related elements (selected_children of 0)", output["text"])
        self.assertIn('"table"', output["text"])
        self.assertIn("selection_container", output["text"])
        self.assertEqual(output["text"].count("2 header item Price"), 1)
        self.assertTrue(all(n["capabilities"]["typeText"] == "unknown" for n in snapshot.roots))

    def test_zero_dimensions_empty_selection_and_unknown_selection_remain_distinct(self):
        _, output = self.observe(
            [node("table", ControlType="ControlType.DataGrid", **{"Grid.RowCount": 0, "Grid.ColumnCount": 0})],
            [edge("table", property="Selection.Selection", total=0)],
        )
        self.assertIn("Table rows: 0, Table columns: 0", output["text"])
        self.assertIn("Selected children of 0: none (0 listed of 0)", output["text"])
        _, output = self.observe(
            [node("table", ControlType="ControlType.DataGrid")],
            [edge("table", property="Selection.Selection", state="error", certainty="unknown")],
        )
        self.assertIn("no confirmed items collected (membership unknown; error)", output["text"])

    def test_unread_and_repeated_members_preserve_order_and_can_be_probed(self):
        raw = windows_batch(
            [node("table", ControlType="ControlType.DataGrid")],
            [edge("table", "cell", "cell", property="Selection.Selection", state="partial", total=3)],
        )
        raw["unread"] = ["cell"]
        output = normalizeFacts(raw, DisplayReferences("session")).render()
        self.assertIn("Selected children of 0: 1, 1 (partial; 2 listed of 3; not expanded: 1, 1)", output["text"])
        self.assertEqual(output["refs"]["1"], "cell")


class WindowsTableReadProfileTests(unittest.IsolatedAsyncioTestCase):
    async def test_capabilities_gate_defaults_while_explicit_fields_remain_raw(self):
        class Port:
            async def nodes(self, *args, **options):
                self.fields, self.options = args[2], options
                return reply(windows_batch([node("table", ControlType="ControlType.DataGrid")]))

        target = Binding("session", "instance", "root", "surface", "table")
        read = SimpleNamespace(target=target, token="operation", remaining=lambda: 3000)
        for features in (
            set(),
            {"nativeTableFacts"},
            {"nativeAdvertisedFields"},
            {"nativeFollowRelations"},
            {"nativeTableFacts", "nativeAdvertisedFields"},
            {"nativeTableFacts", "nativeAdvertisedFields", "nativeFollowRelations"},
        ):
            with self.subTest(features=features):
                port = Port()
                port.connection = SimpleNamespace(platform="windows", capabilities=features)
                await NativeReader(port).collect(read)
                supported = {"nativeTableFacts", "nativeAdvertisedFields"} <= features
                self.assertEqual("GridItem.Row" in port.fields, supported)
                self.assertEqual("Grid.RowCount" in port.fields, supported)
                self.assertEqual("follow_relations" in port.options, "nativeFollowRelations" in features)
                if "nativeFollowRelations" in features:
                    self.assertEqual(
                        port.options["follow_relations"],
                        [
                            "Selection.Selection",
                            "Table.RowHeaders",
                            "Table.ColumnHeaders",
                            "TableItem.RowHeaderItems",
                            "TableItem.ColumnHeaderItems",
                        ],
                    )
                    self.assertNotIn("GridItem.ContainingGrid", port.options["follow_relations"])
                if supported:
                    self.assertIn("GridItem.Row", port.options["advertised_fields"])
                await NativeReader(port).collect(read, fields=["GridItem.Row"])
                self.assertEqual(port.fields, ["GridItem.Row"])
                self.assertEqual(port.options, {"scope_kind": None})


if __name__ == "__main__":
    unittest.main()
