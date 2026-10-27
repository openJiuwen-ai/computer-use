"""Rejected AT-SPI values remain observable without becoming valid input facts."""

import math
import unittest
from types import SimpleNamespace

import backends.linux.test_linux_native as base
import backends.linux.test_linux_native_tables as tables
from backends.linux.test_linux_native_observation import untyped


def status_payload(prop):
    """Read retained diagnostics directly from the native carrier."""
    return untyped(prop["read"]["issues"][0]["nativeStatus"])["rawResult"]


def root_properties(result):
    return next(node["properties"] for node in result["nodes"] if node["id"] == result["roots"][0])


def named_properties(result, name):
    return next(
        node["properties"]
        for node in result["nodes"]
        if node["properties"] is not None
        and any(
            prop["key"] == ["linux.atspi", "name"] and prop["value"] is not None and untyped(prop["value"]) == name
            for prop in node["properties"]
        )
    )


class RejectedNativeResultsTests(unittest.TestCase):
    setUp = base.LinuxNativeFactsTests.setUp
    tearDown = base.LinuxNativeFactsTests.tearDown
    begin = base.LinuxNativeFactsTests.begin
    inspect = base.LinuxNativeFactsTests.inspect
    surfaces = base.LinuxNativeFactsTests.surfaces
    target = base.LinuxNativeFactsTests.target

    def observed(self, name):
        result = self.inspect(self.target(), dict(kind="nodes", fields=["name", name], maxNodes=30, maxDepth=3))
        return result, next(prop for prop in named_properties(result, "Input") if prop["key"] == ["linux.atspi", name])

    def test_invalid_geometry_keeps_fractional_special_zero_and_signed_values(self):
        self.backend.editor.interfaces.append("Component")
        self.backend.ax.A.CoordType = SimpleNamespace(SCREEN="screen")
        self.backend.ax.A.Component.get_extents = lambda obj, coordinate: SimpleNamespace(
            x=-1920.5, y=-80, width=math.inf, height=0
        )
        _, prop = self.observed("Component.extents")
        self.assertEqual(prop["state"], "error")
        self.assertIsNone(prop["value"])
        raw = status_payload(prop)
        self.assertEqual((raw["x"], raw["y"], raw["height"]), (-1920.5, -80, 0))
        self.assertTrue(math.isinf(raw["width"]))

    def test_invalid_numeric_and_span_results_preserve_original_scalar_types(self):
        self.backend.editor.interfaces += ["Value", "TableCell"]
        self.backend.ax.A.Value.get_minimum_value = lambda obj: False
        _, numeric = self.observed("Value.minimumValue")
        self.assertEqual(numeric["state"], "error")
        self.assertIs(status_payload(numeric), False)
        self.backend.ax.A.TableCell = SimpleNamespace(get_row_column_span=lambda obj: (0, "column", 1, 1))
        _, span = self.observed("TableCell.rowColumnSpan")
        raw = status_payload(span)
        encoded = dict(span["read"]["issues"][0]["nativeStatus"]["data"]["fields"])["rawResult"]
        self.assertEqual(encoded["nativeType"], "python.tuple")
        self.assertEqual(raw, [0, "column", 1, 1])

    def test_absent_native_null_and_unexecuted_unsupported_are_distinct(self):
        self.backend.editor.interfaces.append("Value")
        self.backend.ax.A.Value.get_text = lambda obj: None
        _, absent = self.observed("Value.text")
        self.assertEqual(absent["state"], "absent")
        self.assertIsNone(absent["value"])
        self.assertIsNone(status_payload(absent))
        _, unsupported = self.observed("Vendor.not_collected")
        self.assertEqual(unsupported["state"], "unsupported")
        self.assertNotIn("rawResult", untyped(unsupported["read"]["issues"][0]["nativeStatus"]))

    def test_unencodable_slot_keeps_neighboring_values_and_reports_its_original_path(self):
        self.backend.editor.errors["get_description"] = {"opaque": object(), "retained": 0, "bytes": b"\x00\xff"}
        _, prop = self.observed("description")
        raw = status_payload(prop)
        self.assertEqual(prop["state"], "error")
        self.assertEqual(raw["opaque"]["issue"]["reason"], "unencodable_native_result")
        self.assertEqual(raw["opaque"]["issue"]["path"], ["opaque"])
        self.assertEqual(raw["retained"], 0)
        self.assertEqual(raw["bytes"], b"\x00\xff")

    def test_invalid_selection_preserves_prior_ranges_and_rejected_offsets(self):
        self.backend.editor.selections = [(1, 2), (-1, 8)]
        _, prop = self.observed("Text.selections")
        raw = status_payload(prop)
        self.assertEqual(prop["state"], "error")
        self.assertEqual(raw["index"], 1)
        previous = raw["selections"][0]
        self.assertEqual((previous["startOffset"], previous["endOffset"]), (1, 2))
        current = raw["result"]
        self.assertEqual((current["start_offset"], current["end_offset"]), (-1, 8))

    def test_missing_relation_slot_keeps_null_and_its_original_index(self):
        self.backend.window.children.insert(1, None)
        result = self.inspect(self.target(), dict(kind="relation", property="children", cursor=None, limit=10))
        prop = root_properties(result)[0]
        self.assertEqual(len(untyped(prop["value"])), 1, "The original collected prefix must survive the failed slot")
        self.assertFalse(prop["read"]["complete"])
        statuses = [untyped(issue["nativeStatus"]) for issue in prop["read"]["issues"] if issue.get("nativeStatus")]
        raw = next(status["rawResult"] for status in statuses if "rawResult" in status)
        fields = raw
        self.assertEqual(fields["index"], 1)
        self.assertIsNone(fields["result"])

    def test_action_and_relation_failures_keep_rejected_result_without_authorizing_input(self):
        self.backend.button.errors["get_action_name"] = 0
        result = self.inspect(self.target(), dict(kind="nodes", fields=["name"], maxNodes=30, maxDepth=3))
        action = next(prop for prop in named_properties(result, "确认") if prop["key"] == ["toolkit", "actions"])
        self.assertFalse(action["read"]["complete"])
        self.assertEqual(untyped(action["value"]), [])
        raw = status_payload(action)
        self.assertEqual((raw["index"], raw["name"]), (0, 0))
        self.backend.window.errors["get_child_count"] = -1
        result = self.inspect(self.target(), dict(kind="relation", property="children", cursor=None, limit=10))
        relation = root_properties(result)[0]
        self.assertEqual(relation["state"], "error")
        self.assertIsNone(relation["value"])
        self.assertEqual(status_payload(relation), -1)

    def test_action_count_and_description_errors_keep_typed_original_values(self):
        target = self.target()
        button_ref = self.driver.store.keep(target["surface"], self.backend.button)
        target = dict(target, node=button_ref)
        self.backend.button.errors["get_n_actions"] = True
        result = self.inspect(target, dict(kind="nodes", fields=["name"], maxNodes=1, maxDepth=0))
        action = next(prop for prop in root_properties(result) if prop["key"] == ["toolkit", "actions"])
        self.assertIs(status_payload(action), True)
        self.assertEqual(untyped(action["value"]), [])
        self.backend.button.errors.pop("get_n_actions")
        self.backend.button.errors["get_action_description"] = 7
        result = self.inspect(target, dict(kind="nodes", fields=["name"], maxNodes=1, maxDepth=0))
        action = next(prop for prop in root_properties(result) if prop["key"] == ["toolkit", "actions"])
        (item,) = untyped(action["value"])
        description_status = item["descriptionStatus"]
        self.assertEqual(description_status["rawResult"], 7)
        self.assertEqual(description_status["state"], "error")


class RejectedTableResultsTests(unittest.TestCase):
    for name in ("begin", "inspect", "surfaces", "target", "collect", "setUp", "tearDown", "node_target"):
        locals()[name] = getattr(tables.LinuxNativeTableTests, name)
    del name

    def test_invalid_recheck_dimensions_keep_original_cells_and_rejected_native_tuple(self):
        get_rows = self.backend.ax.A.Table.get_n_rows
        calls = []

        def rows(obj):
            calls.append(obj)
            return get_rows(obj) if len(calls) == 1 else -1

        self.backend.ax.A.Table.get_n_rows = rows
        result = self.inspect(
            self.node_target(self.table), dict(kind="relation", property="Table.cells", cursor=None, limit=10)
        )
        prop = root_properties(result)[0]
        self.assertEqual(len(untyped(prop["value"])), 6)
        self.assertEqual(len(calls), 2, "Diagnostic retention must not issue another native getter")
        self.assertFalse(prop["read"]["complete"])
        statuses = [untyped(issue["nativeStatus"]) for issue in prop["read"]["issues"] if issue.get("nativeStatus")]
        raw = next(status["rawResult"] for status in statuses if "rawResult" in status)
        self.assertEqual(tuple(raw), (-1, 2))


if __name__ == "__main__":
    unittest.main()
