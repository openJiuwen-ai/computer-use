"""Native viewport discovery is bounded and never replaces ordinary contents."""

import unittest
from types import SimpleNamespace as NS

from linux_native_provider import NativeError, Node

import backends.linux.test_linux_native_tables as tables
from computer_use.interaction.read.facts.normalize import normalizeFacts
from computer_use.interaction.read.numbering.references import DisplayReferences
from computer_use.native.semantics import followed_relations
from computer_use_linux.contracts.protocol import Failure
from computer_use_linux.runtime.native import CAPABILITIES


class LinuxTableViewportTests(unittest.TestCase):
    for name in ("begin", "inspect", "surfaces", "target", "collect", "tearDown", "node_target", "facts", "relation"):
        locals()[name] = getattr(tables.LinuxNativeTableTests, name)
    del name

    def setUp(self):
        tables.LinuxNativeTableTests.setUp(self)
        self.table.interfaces.append("Component")
        self.table.n_rows, self.table.n_columns = 10000, 5
        self.top, self.left, self.visible_rows, self.visible_columns = 5000, 1, 6, 3
        self.table.children = []
        self.table.selected_children = []
        self.objects, self.lookups = {}, []
        self.hit_override = None
        self.frame = (10, 20, 300, 120)
        ax = self.backend.ax
        ax.A.CoordType = NS(SCREEN="screen")

        def get_cell(obj, row, column):
            self.lookups.append((row, column))
            key = row, column
            if key not in self.objects:
                cell = Node(
                    "table cell", f"R{row + 1}C{column + 1}", interfaces=["TableCell", "Component"], parent=self.table
                )
                cell.table, cell.span = self.table, (row, column, 1, 1)
                cell.states.add("VISIBLE")
                cell.row_headers = cell.column_headers = []
                self.objects[key] = cell
            return self.objects[key]

        self.cell = get_cell
        ax.A.Table.get_accessible_at = get_cell

        def rect(obj, coord):
            if obj is self.table:
                return NS(**dict(zip(("x", "y", "width", "height"), self.frame)))
            row, col, height, width = obj.span
            return NS(
                x=10 + (col - self.left) * 100, y=20 + (row - self.top) * 20, width=width * 100, height=height * 20
            )

        def hit(obj, x, y, coord):
            if self.hit_override is not None:
                return self.hit_override(obj, x, y)
            return self.cell(obj, self.top + (y - 20) // 20, self.left + (x - 10) // 100)

        ax.A.Component.get_extents = rect
        ax.A.Component.get_accessible_at_point = hit

    def edge(self, **options):
        return self.relation(self.table, "Table.viewportCandidates", **options)

    def test_far_viewport_is_discovered_without_scanning_prefix_or_moving_selection(self):
        edge = self.edge()
        self.assertEqual((edge["state"], edge["total"], len(edge["targets"])), ("complete", 18, 18))
        self.assertEqual(
            [self.driver.store.get(ref, self.target()["surface"]).obj.name for ref in edge["targets"]],
            [f"R{r + 1}C{c + 1}" for r in range(5000, 5006) for c in range(1, 4)],
        )
        self.assertLess(len(self.lookups), 40)
        self.assertGreater(min(r for r, c in self.lookups), 4900)
        self.assertIn("coverage and occlusion are unknown", edge["detail"])
        self.assertEqual(self.table.selected_children, [])
        self.assertFalse(self.table.action_history)

    def test_null_corners_extend_from_middle_without_guessing_uniform_table_layout(self):
        self.hit_override = lambda obj, x, y: self.cell(obj, 5003, 2) if (x, y) == (160, 80) else None
        edge = self.edge()
        self.assertEqual(edge["total"], 18)
        self.assertIn("rows 5000..5005, columns 1..3", edge["detail"])

    def test_hit_text_uses_native_parent_cell_and_missing_or_foreign_owner_stays_unknown(self):
        text = Node("text", "R5004C3", parent=self.cell(self.table, 5003, 2))
        self.hit_override = lambda *_: text
        self.assertEqual(self.edge()["total"], 18)
        text.parent.table = Node("table", "Other window")
        edge = self.edge()
        self.assertEqual((edge["state"], edge["certainty"], edge["targets"]), ("absent", "unknown", []))
        self.assertIn("coverage is unknown", edge["detail"])

    def test_no_samples_do_not_mean_empty_table_and_old_content_is_retained(self):
        self.hit_override = lambda *_: None
        batch = self.facts(maxNodes=20, maxDepth=1, followRelations=["Table.viewportCandidates", "Table.cells"])
        edges = {r["property"]: r for r in batch["relations"] if r["source"] == batch["roots"][0]}
        self.assertEqual(edges["Table.viewportCandidates"]["state"], "absent")
        self.assertEqual(edges["Table.cells"]["total"], 50000)
        self.assertTrue(edges["Table.cells"]["targets"])
        self.assertIn("R1C1", normalizeFacts(self.last_graph, DisplayReferences("session")).render()["text"])

    def test_cursor_is_bound_to_observed_viewport(self):
        first = self.edge(limit=4)
        second = self.edge(limit=4, cursor=first["cursor"])
        self.assertEqual(second["offset"], 4)
        self.assertEqual(second["total"], 18)
        self.top += 1
        with self.assertRaisesRegex(Failure, "changed since"):
            self.edge(limit=4, cursor=first["cursor"])

    def test_viewport_continues_when_provider_recreates_cell_objects(self):
        first = self.edge(limit=4)
        original = self.driver.store.get(first["targets"][0], self.target()["surface"]).obj
        self.objects.clear()
        second = self.edge(limit=4, cursor=first["cursor"])
        self.assertEqual((second["offset"], second["total"]), (4, 18))
        self.assertEqual(
            [self.driver.store.get(ref, self.target()["surface"]).obj.name for ref in second["targets"]],
            ["R5002C3", "R5002C4", "R5003C2", "R5003C3"],
        )
        self.assertIs(self.driver.store.get(first["targets"][0], self.target()["surface"]).obj, original)
        self.assertIsNot(original, self.objects[(5000, 1)])

    def test_same_size_geometry_change_invalidates_old_cursor(self):
        first = self.edge(limit=2)
        self.frame = (10, 21, 300, 119)
        with self.assertRaisesRegex(Failure, "changed since"):
            self.edge(limit=2, cursor=first["cursor"])

    def test_sampling_does_not_require_collection(self):
        self.assertNotIn("Collection", self.table.interfaces)
        self.assertEqual(self.edge()["total"], 18)

    def test_non_visible_native_coordinates_are_not_used_as_viewport_anchors(self):
        cell = self.cell(self.table, 5003, 2)
        cell.states.clear()
        self.hit_override = lambda *_: cell
        self.assertEqual((self.edge()["certainty"], self.edge()["targets"]), ("unknown", []))

    def test_missing_component_is_explicit_and_performs_no_coordinate_lookup(self):
        self.table.interfaces.remove("Component")
        self.assertEqual(self.edge()["state"], "unsupported")
        self.assertEqual(self.lookups, [])

    def test_zero_area_and_malformed_geometry_cannot_claim_empty_viewport(self):
        for frame in ((0, 0, 0, 1), (0, 0, 1, -1), (float("nan"), 0, 1, 1), (True, 0, 1, 1)):
            self.frame = frame
            self.assertEqual(self.edge()["certainty"], "unknown")
            self.assertEqual(self.lookups, [])

    def test_viewport_is_prioritized_without_reparenting_original_cells(self):
        batch = self.facts(maxNodes=2, maxDepth=1, followRelations=["Table.viewportCandidates", "Table.cells"])
        self.assertEqual(batch["nodes"][1]["values"]["name"], "R5001C2")
        result = normalizeFacts(self.last_graph, DisplayReferences("session")).render()
        self.assertIn("viewport_candidates", result["text"])
        self.assertIn("full viewport coverage and occlusion are unknown", result["text"])

    def test_profile_requires_explicit_capability(self):
        self.assertIn("nativeTableViewport", CAPABILITIES)
        self.assertIn("Table.viewportCandidates", followed_relations("linux", CAPABILITIES))
        self.assertNotIn(
            "Table.viewportCandidates", followed_relations("linux", ["nativeFollowRelations", "nativeTableContent"])
        )
        self.assertNotIn("Table.viewportCandidates", followed_relations("linux", CAPABILITIES, content_only=True))

    def test_native_call_errors_do_not_affect_other_relation_facts(self):
        def error(*_):
            raise NativeError("Geometry unavailable")

        self.backend.ax.A.Component.get_extents = error
        edge = self.edge()
        self.assertEqual((edge["state"], edge["certainty"]), ("error", "unknown"))
        self.assertIn("Geometry unavailable", edge["detail"])

    def test_merged_coordinate_slots_keep_the_same_original_object(self):
        merged = self.cell(self.table, 5000, 1)
        merged.span = (5000, 1, 1, 2)
        self.objects[(5000, 2)] = merged
        edge = self.edge()
        self.assertEqual(edge["total"], 18)
        self.assertEqual(edge["targets"][0], edge["targets"][1])
        self.assertEqual(len(set(edge["targets"])), 17)

    def test_large_geometry_remains_bounded_and_admits_incomplete_coverage(self):
        self.frame = (10, 20, 300, 100000)
        edge = self.edge(limit=128)
        self.assertEqual((edge["state"], len(edge["targets"])), ("partial", 128))
        self.assertIn("full viewport coverage and occlusion are unknown", edge["detail"])
        self.assertLessEqual(len(self.lookups), 9 + 64 + 128)
        self.assertIn("cursor", edge)

    def test_incomplete_boundary_sampling_is_explicit_and_keeps_known_candidates(self):
        self.frame = (10, 20, 300, 100000)
        self.hit_override = lambda *_: self.cell(self.table, 5500, 2)
        edge = self.edge(limit=128)
        self.assertTrue(edge["targets"])
        self.assertIn("64-cell budget", edge["detail"])
        self.assertLessEqual(len(self.lookups), 9 + 64 + 128)


if __name__ == "__main__":
    unittest.main()
