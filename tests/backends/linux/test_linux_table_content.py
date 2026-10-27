"""Table coordinates must not be confused with children, selection or visibility."""

import unittest

from linux_native_provider import NativeError, Node

import backends.linux.test_linux_native_tables as tables
from computer_use.interaction.read.facts.normalize import normalizeFacts
from computer_use.interaction.read.numbering.references import DisplayReferences
from computer_use_linux.contracts.protocol import Failure
from computer_use_linux.runtime.native import CAPABILITIES, query


class LinuxTableContentTests(unittest.TestCase):
    for name in (
        "begin",
        "inspect",
        "surfaces",
        "target",
        "collect",
        "setUp",
        "tearDown",
        "node_target",
        "facts",
        "relation",
    ):
        locals()[name] = getattr(tables.LinuxNativeTableTests, name)
    del name

    def test_table_interface_finds_content_omitted_from_children_without_changing_parentage(self):
        self.table.children = []
        result = self.facts(followRelations=["Table.cells"])
        self.assertEqual(len(result["nodes"]), 7)
        table_ref = result["roots"][0]
        cells = next(r for r in result["relations"] if r["source"] == table_ref and r["property"] == "Table.cells")
        self.assertEqual((cells["total"], cells["state"]), (6, "complete"))
        self.assertEqual(
            next(r for r in result["relations"] if r["source"] == table_ref and r["property"] == "children")["targets"],
            [],
        )
        snapshot = normalizeFacts(self.last_graph, DisplayReferences("session"))
        output = snapshot.render()
        self.assertEqual(snapshot.roots[0]["children"], [])
        self.assertIn("Related elements (cells of 0)", output["text"])
        self.assertIn("R3C2", output["text"])
        self.assertEqual(len(output["refs"]), len(result["nodes"]) + len(result["unread"]))
        self.assertTrue(all(not n.action_history for n in self.cells))

    def test_merged_cells_retain_one_slot_per_coordinate_but_one_native_reference(self):
        self.table.cells[1] = self.table.cells[0]
        first = self.relation(self.table, "Table.cells", limit=1)
        second = self.relation(self.table, "Table.cells", limit=2, cursor=first["cursor"])
        self.assertEqual(second["offset"], 1)
        self.assertEqual(second["total"], 6)
        self.assertEqual(second["targets"][0], first["targets"][0])
        self.assertNotEqual(second["targets"][0], second["targets"][1])

    def test_same_total_with_changed_dimensions_invalidates_page(self):
        first = self.relation(self.table, "Table.cells", limit=1)
        self.table.n_rows, self.table.n_columns = 2, 3
        with self.assertRaisesRegex(Failure, "changed since"):
            self.relation(self.table, "Table.cells", limit=1, cursor=first["cursor"])

    def test_recreated_cells_continue_by_coordinate_without_rebinding_old_references(self):
        first = self.relation(self.table, "Table.cells", limit=2)
        self.table.cells = [Node("table cell", cell.name) for cell in self.cells]
        self.backend.ax.calls.clear()
        second = self.relation(self.table, "Table.cells", limit=2, cursor=first["cursor"])
        self.assertEqual((second["offset"], second["total"]), (2, 6))
        self.assertIn("Live coordinate page", second["detail"])
        original = self.driver.store.get(first["targets"][0], self.target()["surface"]).obj
        current = self.driver.store.get(second["targets"][0], self.target()["surface"]).obj
        self.assertIs(original, self.cells[0])
        self.assertIs(current, self.table.cells[2])
        calls = [args for name, _, args in self.backend.ax.calls if name == "get_accessible_at"]
        self.assertEqual(calls, [(1, 0), (1, 1)])

    def test_ordinary_children_still_reject_replaced_prefix(self):
        first = self.relation(self.table, "children", limit=2)
        self.table.children[0] = Node("table cell", "R1C1")
        with self.assertRaisesRegex(Failure, "changed since"):
            self.relation(self.table, "children", limit=1, cursor=first["cursor"])

    def test_coordinate_cursor_cannot_cross_relation_or_source(self):
        first = self.relation(self.table, "Table.cells", limit=2)
        for source, prop in ((self.table, "children"), (Node("table", "Other"), "Table.cells")):
            with self.subTest(property=prop), self.assertRaisesRegex(Failure, "belongs to another target"):
                self.relation(source, prop, limit=2, cursor=first["cursor"])

    def test_hole_in_next_page_never_skips_to_a_later_coordinate(self):
        first = self.relation(self.table, "Table.cells", limit=2)
        self.table.cells[3] = None
        second = self.relation(self.table, "Table.cells", limit=3, cursor=first["cursor"])
        self.assertEqual((second["offset"], len(second["targets"])), (2, 1))
        self.assertNotIn("cursor", second)
        self.assertIn("row 1, column 1", second["detail"])

    def test_large_table_continuation_has_bounded_work_without_a_prefix_ceiling(self):
        self.table.n_rows, self.table.n_columns = 10000, 1
        calls = []

        def cell(obj, row, column):
            calls.append((row, column))
            return Node("table cell", f"R{row + 1}C1")

        self.backend.ax.A.Table.get_accessible_at = cell
        cursor = None
        for offset in range(0, 10000, 2000):
            calls.clear()
            page = self.relation(self.table, "Table.cells", limit=2000, cursor=cursor)
            self.assertEqual(page.get("offset", 0), offset)
            self.assertEqual(calls, [(row, 0) for row in range(offset, offset + 2000)])
            cursor = page.get("cursor")
        self.assertIsNone(cursor)

    def test_hole_or_error_preserves_prefix_and_does_not_skip_to_later_cells(self):
        for missing in (True, False):
            with self.subTest(missing=missing):
                self.table.cells = list(self.cells)
                self.table.errors.clear()
                self.backend.ax.hooks.clear()
                if missing:
                    self.table.cells[2] = None
                else:

                    def fail(obj, row, column):
                        if row == 1:
                            obj.errors["get_accessible_at"] = NativeError("Cell unavailable")

                    self.backend.ax.hooks["get_accessible_at"] = fail
                edge = self.relation(self.table, "Table.cells")
                self.assertEqual((edge["state"], edge["total"], len(edge["targets"])), ("partial", 6, 2))
                self.assertNotIn("cursor", edge)
                self.assertIn("incomplete" if not missing else "unavailable", edge["detail"])

    def test_dimensions_change_during_collection_preserves_partial_prefix(self):
        count = [0]

        def change(obj):
            count[0] += 1
            if count[0] == 2:
                obj.n_rows, obj.n_columns = 2, 3

        self.backend.ax.hooks["get_n_rows"] = change
        edge = self.relation(self.table, "Table.cells")
        self.assertEqual((edge["state"], edge["total"], len(edge["targets"])), ("partial", 6, 6))
        self.assertNotIn("cursor", edge)
        self.assertIn("dimensions changed", edge["detail"])

    def test_invalid_dimension_or_overflow_never_calls_cell_getter(self):
        for shape in ((-1, 2), (True, 2), (1.5, 2), (2147483648, 1), (2147483647, 2147483647)):
            with self.subTest(shape=shape):
                self.table.n_rows, self.table.n_columns = shape
                self.backend.ax.calls.clear()
                edge = self.relation(self.table, "Table.cells")
                self.assertEqual((edge["state"], edge["certainty"]), ("error", "unknown"))
                self.assertFalse(any(name == "get_accessible_at" for name, _, _ in self.backend.ax.calls))

    def test_zero_dimensions_and_missing_interface_are_different(self):
        self.table.n_columns = 0
        edge = self.relation(self.table, "Table.cells")
        self.assertEqual((edge["state"], edge["total"], edge["targets"]), ("complete", 0, []))
        self.table.interfaces.remove("Table")
        self.assertEqual(self.relation(self.table, "Table.cells")["state"], "unsupported")

    def test_prefix_budget_does_not_walk_the_table(self):
        self.table.n_rows = 2000000
        edge = self.relation(self.table, "Table.cells", limit=2)
        self.assertEqual((edge["state"], edge["total"], len(edge["targets"])), ("partial", 4000000, 2))
        self.assertIn("cursor", edge)
        calls = [args for name, _, args in self.backend.ax.calls if name == "get_accessible_at"]
        self.assertEqual(calls, [(0, 0), (0, 1)])

    def test_new_content_profile_is_negotiated_without_following_ownership(self):
        self.assertIn("nativeTableContent", CAPABILITIES)
        request = dict(
            kind="nodes", fields=tables.TABLE_FIELDS, maxNodes=10, maxDepth=2, followRelations=["Table.cells"]
        )
        self.assertEqual(query(request), request)
        with self.assertRaises(Failure):
            query(dict(request, followRelations=["TableCell.table"]))


if __name__ == "__main__":
    unittest.main()
