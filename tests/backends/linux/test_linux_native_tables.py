"""AT-SPI table facts through the production driver, with OS objects substituted.

This exercises query/fact/cursor contracts, not real desktop acceptance. The
provider extension is local: it does not add table semantics to the SDK seam.
"""

import unittest
from types import SimpleNamespace as NS

from linux_native_provider import NativeError, Node

import backends.linux.test_linux_native as base
from backends.linux.test_linux_native import read_relation_graph
from computer_use_linux.contracts.protocol import Failure
from computer_use_linux.runtime.native import CAPABILITIES, query

TABLE_FIELDS = [
    "role",
    "name",
    "visible",
    "showing",
    "selected",
    "Table.nRows",
    "Table.nColumns",
    "TableCell.rowColumnSpan",
    "Selection.nSelectedChildren",
]
FOLLOW = ["Selection.selectedChildren", "TableCell.rowHeaderCells", "TableCell.columnHeaderCells"]


class LinuxNativeTableTests(unittest.TestCase):
    begin = base.LinuxNativeFactsTests.begin
    inspect = base.LinuxNativeFactsTests.inspect
    surfaces = base.LinuxNativeFactsTests.surfaces
    target = base.LinuxNativeFactsTests.target
    collect = base.LinuxNativeFactsTests.collect
    tearDown = base.LinuxNativeFactsTests.tearDown

    def setUp(self):
        base.LinuxNativeFactsTests.setUp(self)
        self.table = Node("table", "Table", interfaces=["Table", "Selection"])
        self.table.n_rows, self.table.n_columns = 3, 2
        self.cells = [
            Node("table cell", f"R{row + 1}C{column + 1}", interfaces=["TableCell"], parent=self.table)
            for row in range(3)
            for column in range(2)
        ]
        self.row_header = Node("row header", "Third row")
        self.column_header = Node("column header", "Second column")
        for index, cell in enumerate(self.cells):
            cell.span = (index // 2, index % 2, 1, 1)
            cell.table = self.table
            cell.row_headers, cell.column_headers = [self.row_header], [self.column_header]
            cell.states.add("VISIBLE")
        self.table.children = self.cells
        self.table.cells = list(self.cells)
        self.table.selected_children = [self.cells[-1]]
        self.table.parent = self.backend.window
        self.backend.window.children = [self.table]
        ax = self.backend.ax
        ax.A.StateType.VISIBLE = "VISIBLE"

        def native(name, function):
            def invoke(obj, *args):
                ax.calls.append((name, obj, args))
                if name in ax.hooks:
                    ax.hooks[name](obj, *args)
                if obj.dead:
                    raise NativeError("Native object was destroyed")
                if name in obj.errors:
                    result = obj.errors[name]
                    if isinstance(result, BaseException):
                        raise result
                    return result
                return function(obj, *args)

            return invoke

        ax.A.Table = NS(
            get_n_rows=native("get_n_rows", lambda obj: obj.n_rows),
            get_n_columns=native("get_n_columns", lambda obj: obj.n_columns),
            get_accessible_at=native(
                "get_accessible_at", lambda obj, row, column: obj.cells[row * obj.n_columns + column]
            ),
        )
        ax.A.TableCell = NS(
            get_row_column_span=native("get_row_column_span", lambda obj: obj.span),
            get_table=native("get_table", lambda obj: obj.table),
            get_row_header_cells=native("get_row_header_cells", lambda obj: obj.row_headers),
            get_column_header_cells=native("get_column_header_cells", lambda obj: obj.column_headers),
        )
        ax.A.Selection = NS(
            get_n_selected_children=native("get_n_selected_children", lambda obj: len(obj.selected_children)),
            get_selected_child=native("get_selected_child", lambda obj, index: obj.selected_children[index]),
        )

    def node_target(self, obj):
        target = self.target()
        return dict(target, node=self.driver.store.keep(target["surface"], obj))

    def facts(self, obj=None, **changes):
        return self.collect(
            self.node_target(obj or self.table), fields=TABLE_FIELDS, advertisedFields=TABLE_FIELDS[5:], **changes
        )

    def relation(self, obj, property, limit=128, cursor=None):
        return read_relation_graph(
            self.inspect(self.node_target(obj), dict(kind="relation", property=property, limit=limit, cursor=cursor))
        )["relation"]

    def test_native_contract_preserves_raw_counts_spans_and_independent_states(self):
        self.cells[-1].span = (2, 1, 2, 3)
        self.cells[-1].states.discard("SHOWING")
        result = self.facts(followRelations=FOLLOW)
        table = result["nodes"][0]
        self.assertEqual(table["values"]["Table.nRows"], 3)
        self.assertEqual(table["values"]["Table.nColumns"], 2)
        self.assertEqual(table["values"]["Selection.nSelectedChildren"], 1)
        cell = next(n for n in result["nodes"] if n["values"]["name"] == "R3C2")
        self.assertEqual(cell["values"]["TableCell.rowColumnSpan"], dict(row=2, column=1, rowSpan=2, columnSpan=3))
        self.assertTrue(cell["values"]["visible"])
        self.assertFalse(cell["values"]["showing"])
        self.assertFalse(cell["values"]["selected"], "Container selection cannot overwrite native cell state")
        self.assertIn("Third row", [n["values"]["name"] for n in result["nodes"]])
        self.assertIn("Second column", [n["values"]["name"] for n in result["nodes"]])
        self.assertEqual(self.backend.leases, 0)
        self.assertFalse(any(n.action_history for n in self.cells))

    def test_missing_interface_does_not_invoke_successful_default_getters(self):
        plain = Node("push button", "No table")
        plain.errors.update(
            get_n_rows=0, get_n_columns=0, get_row_column_span=(0, 0, 0, 0), get_n_selected_children=0, get_table=None
        )
        result = self.facts(plain, maxNodes=1, maxDepth=0, followRelations=FOLLOW)
        node = result["nodes"][0]
        self.assertTrue(all(node["fieldStatus"][field]["state"] == "unsupported" for field in TABLE_FIELDS[5:]))
        self.assertFalse(
            any(
                name.startswith(
                    ("get_n_rows", "get_n_columns", "get_row_column_span", "get_n_selected_children", "get_table")
                )
                and obj is plain
                for name, obj, args in self.backend.ax.calls
            )
        )
        for edge in result["relations"]:
            if edge["property"].startswith(("TableCell.", "Selection.")):
                self.assertEqual(edge["state"], "unsupported")

    def test_declared_zero_counts_are_real_values(self):
        self.table.n_rows = self.table.n_columns = 0
        self.table.selected_children = []
        result = self.facts(maxNodes=1, maxDepth=0)
        self.assertEqual(
            {
                key: value
                for key, value in result["nodes"][0]["values"].items()
                if key.startswith(("Table.", "Selection."))
            },
            {"Table.nRows": 0, "Table.nColumns": 0, "Selection.nSelectedChildren": 0},
        )

    def test_raw_sentinel_positions_are_not_fabricated_into_valid_cells(self):
        self.cells[0].span = (-1, -1, 0, 0)
        node = self.facts(self.cells[0], maxNodes=1, maxDepth=0)["nodes"][0]
        self.assertEqual(node["values"]["TableCell.rowColumnSpan"], dict(row=-1, column=-1, rowSpan=0, columnSpan=0))

    def test_malformed_fields_are_local_errors_and_declaration_failure_survives(self):
        self.table.errors["get_n_rows"] = -1
        self.table.errors["get_n_columns"] = True
        node = self.facts(maxNodes=1, maxDepth=0)["nodes"][0]
        for field in ("Table.nRows", "Table.nColumns"):
            self.assertEqual(node["fieldStatus"][field]["state"], "error")
            self.assertNotIn(field, node["values"])
        self.assertEqual(node["values"]["Selection.nSelectedChildren"], 1)
        for invalid in ((True, 1, 1, 1), (1, 2, 3), None):
            with self.subTest(span=invalid):
                self.cells[0].errors["get_row_column_span"] = invalid
                node = self.facts(self.cells[0], maxNodes=1, maxDepth=0)["nodes"][0]
                self.assertEqual(node["fieldStatus"]["TableCell.rowColumnSpan"]["state"], "error")
        self.table.errors["get_interfaces"] = NativeError("interface declaration failed")
        node = self.facts(maxNodes=1, maxDepth=0)["nodes"][0]
        self.assertEqual(node["fieldStatus"]["Table.nRows"]["state"], "error")
        self.assertIn("interface declaration failed", node["fieldStatus"]["Table.nRows"]["detail"])

    def test_selection_is_prioritized_over_children_prefix_without_invented_parentage(self):
        outside = Node("table cell", "Far selected", interfaces=["TableCell"], parent=self.table)
        outside.table, outside.span = self.table, (5000, 10, 1, 1)
        outside.row_headers, outside.column_headers = [], []
        self.table.selected_children = [outside]
        result = self.facts(maxNodes=2, maxDepth=1, followRelations=FOLLOW)
        self.assertEqual([n["values"]["name"] for n in result["nodes"]], ["Table", "Far selected"])
        child_edge = next(
            r for r in result["relations"] if r["source"] == result["roots"][0] and r["property"] == "children"
        )
        self.assertNotIn(result["nodes"][1]["node"], child_edge["targets"])
        selection = next(
            r
            for r in result["relations"]
            if r["source"] == result["roots"][0] and r["property"] == "Selection.selectedChildren"
        )
        self.assertEqual(selection["targets"], [result["nodes"][1]["node"]])

    def test_cell_read_records_table_ownership_without_expanding_table(self):
        result = self.facts(self.cells[0], maxNodes=50, followRelations=FOLLOW)
        self.assertEqual({n["values"]["name"] for n in result["nodes"]}, {"R1C1", "Third row", "Second column"})
        edge = next(
            r for r in result["relations"] if r["source"] == result["roots"][0] and r["property"] == "TableCell.table"
        )
        self.assertEqual(edge["state"], "complete")
        self.assertEqual(len(edge["targets"]), 1)
        self.assertIn(edge["targets"][0], result["unread"])
        self.assertNotIn("Selection.selectedChildren", self.facts(self.cells[0])["requestedRelations"])

    def test_selection_pages_validate_old_prefix_and_count(self):
        self.table.selected_children = self.cells[:3]
        first = self.relation(self.table, "Selection.selectedChildren", limit=1)
        second = self.relation(self.table, "Selection.selectedChildren", limit=1, cursor=first["cursor"])
        self.assertEqual((first["state"], first["total"]), ("partial", 3))
        self.assertEqual(second["offset"], 1)
        self.assertNotEqual(first["targets"], second["targets"])
        self.table.selected_children[:2] = reversed(self.table.selected_children[:2])
        with self.assertRaisesRegex(Failure, "changed since"):
            self.relation(self.table, "Selection.selectedChildren", limit=1, cursor=first["cursor"])
        self.table.selected_children = self.cells[:2]
        with self.assertRaisesRegex(Failure, "changed since"):
            self.relation(self.table, "Selection.selectedChildren", limit=1, cursor=first["cursor"])

    def test_selection_hole_stops_prefix_and_never_skips_with_cursor(self):
        self.table.selected_children = [self.cells[0], None, self.cells[2]]
        edge = self.relation(self.table, "Selection.selectedChildren")
        self.assertEqual((edge["state"], edge["total"], len(edge["targets"])), ("partial", 3, 1))
        self.assertNotIn("cursor", edge)
        self.assertIn("unavailable", edge["detail"])

    def test_selection_count_changes_and_recheck_errors_preserve_partial_prefix(self):
        for mode in ("change", "error"):
            with self.subTest(mode=mode):
                count = [0]
                self.table.selected_children = self.cells[:2]
                self.table.errors.clear()

                def hook(obj, *, count=count, mode=mode):
                    count[0] += 1
                    if count[0] == 2:
                        if mode == "change":
                            obj.selected_children.append(self.cells[2])
                        else:
                            obj.errors["get_n_selected_children"] = NativeError("count recheck failed")

                self.backend.ax.hooks["get_n_selected_children"] = hook
                edge = self.relation(self.table, "Selection.selectedChildren")
                self.assertEqual((edge["state"], edge["total"], len(edge["targets"])), ("partial", 2, 2))
                self.assertNotIn("cursor", edge)

    def test_headers_preserve_empty_absent_holes_errors_and_cursor_changes(self):
        cell = self.cells[0]
        cell.row_headers = []
        self.assertEqual(self.relation(cell, "TableCell.rowHeaderCells")["state"], "complete")
        cell.errors["get_row_header_cells"] = None
        self.assertEqual(self.relation(cell, "TableCell.rowHeaderCells")["state"], "absent")
        cell.errors.clear()
        cell.row_headers = [self.row_header, None, self.column_header]
        edge = self.relation(cell, "TableCell.rowHeaderCells")
        self.assertEqual((edge["state"], edge["total"], len(edge["targets"])), ("partial", 3, 1))
        self.assertNotIn("cursor", edge)
        cell.row_headers = [self.row_header, self.column_header]
        first = self.relation(cell, "TableCell.rowHeaderCells", limit=1)
        cell.row_headers.reverse()
        with self.assertRaisesRegex(Failure, "changed since"):
            self.relation(cell, "TableCell.rowHeaderCells", limit=1, cursor=first["cursor"])
        cell.errors["get_column_header_cells"] = NativeError("header timeout")
        edge = self.relation(cell, "TableCell.columnHeaderCells")
        self.assertEqual((edge["state"], edge["certainty"]), ("error", "unknown"))
        cell.table = None
        self.assertEqual(self.relation(cell, "TableCell.table")["state"], "absent")

    def test_advertised_and_follow_queries_are_strict_and_capabilities_are_declared(self):
        self.assertTrue({"nativeTableFacts", "nativeAdvertisedFields", "nativeFollowRelations"} <= set(CAPABILITIES))
        request = dict(
            kind="nodes",
            fields=TABLE_FIELDS,
            maxNodes=10,
            maxDepth=2,
            advertisedFields=TABLE_FIELDS[5:],
            followRelations=FOLLOW,
        )
        self.assertEqual(query(request), request)
        for changes in (
            dict(advertisedFields=["notRequested"]),
            dict(advertisedFields=[]),
            dict(advertisedFields=["Table.nRows"] * 2),
            dict(followRelations=[]),
            dict(followRelations=["TableCell.table"]),
            dict(followRelations=["parent"]),
            dict(followRelations=["madeUp"]),
            dict(followRelations=[FOLLOW[0]] * 2),
        ):
            with self.subTest(changes=changes), self.assertRaises(Failure):
                query({**request, **changes})


if __name__ == "__main__":
    unittest.main()
