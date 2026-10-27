"""Retain geometry, range facts and failures in the unified graph without changing bindings."""

import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

from observation_fixture import facts_fixture as decodeFacts, normalize_fixture as normalizeFacts
from sdk.interaction.read.facts.test_native_facts import batch, edge, group_relation_sources, node

from computer_use.interaction.read.acquisition.facts.helpers import plan_fact_profile, resolve_fact_scope
from computer_use.interaction.read.acquisition.facts.model import FactReadRequest, NativeReadCapabilities
from computer_use.interaction.read.delivery.diff.formatting import plan_document_diff
from computer_use.interaction.read.numbering.references import DisplayReferences
from computer_use.interaction.read.presentation.document import DisplayDocument
from computer_use.interaction.read.presentation.preparation import prepareSnapshot, renderPreparedSnapshot
from computer_use.native.contracts import Binding
from computer_use.native.semantics import RAW_NODE_METADATA_FIELDS

FEATURE = "nativeRawNodeMetadata"


def profile(platform, features=(), fields=None):
    read = SimpleNamespace(target=Binding("session", "instance", "root", "surface", "window"))
    scope = resolve_fact_scope(FactReadRequest(read, fields=fields), None)
    return plan_fact_profile(scope, NativeReadCapabilities(platform, features))


def original(platform):
    if platform == "macos":
        nodes = [
            node("window", AXRole="AXWindow"),
            node("group", AXRole="AXGroup"),
            node("button", AXRole="AXButton", AXTitle="Open"),
        ]
        relation = "AXChildren"
    else:
        nodes = [
            node("window", role="frame"),
            node("group", role="panel"),
            node("button", role="push button", name="Open"),
        ]
        relation = "children"
    result = batch(nodes, [edge("window", "group", property=relation), edge("group", "button", property=relation)])
    result["platform"] = platform
    return result


def with_metadata(raw, *, offset=0):
    result = deepcopy(raw)
    names = RAW_NODE_METADATA_FIELDS[raw["platform"]]
    values = ({"x": -25 + offset, "y": 16}, {"width": 640, "height": 480}, 0, 100, 1, "50 percent")
    if raw["platform"] == "linux":
        values = ({"x": -25 + offset, "y": 16, "width": 640, "height": 480}, True, 0, 100, 1, "50 percent")
    for source in result["nodes"]:
        source["requestedFields"].extend(names)
        source["values"].update(zip(names, values))
    # A failed ancillary getter remains distinguishable from a missing value.
    result["nodes"][1]["values"].pop(names[-1])
    result["nodes"][1]["fieldStatus"][names[-1]] = dict(state="timeout", detail="native getter deadline")
    return result


class RawNodeMetadataTests(unittest.TestCase):
    def test_only_default_negotiated_profiles_add_native_fields(self):
        for platform, names in RAW_NODE_METADATA_FIELDS.items():
            with self.subTest(platform=platform):
                previous = profile(platform)
                current = profile(platform, (FEATURE,))
                self.assertEqual(current.fields, [*previous.fields, *names])
                self.assertEqual(current.options, previous.options)
                self.assertTrue(set(names).isdisjoint(previous.fields))
                explicit = list(names[:1]) or ["BoundingRectangle"]
                requested = profile(platform, (FEATURE,), explicit)
                self.assertIs(requested.fields, explicit)
                self.assertEqual(requested.options, {})

    def test_values_and_failures_normalize_without_changing_operation_bindings(self):
        for platform in ("macos", "linux"):
            for compact in (False, True):
                with self.subTest(platform=platform, compact=compact):
                    before = normalizeFacts(original(platform), DisplayReferences("session"))
                    raw = with_metadata(original(platform))
                    saved = deepcopy(raw)
                    after = normalizeFacts(raw, DisplayReferences("session"), driver_features=(FEATURE,))
                    self.assertEqual(group_relation_sources(decodeFacts(raw)), group_relation_sources(saved))
                    self.assertEqual(group_relation_sources(after.facts), group_relation_sources(saved))
                    self.assertEqual(raw, saved)
                    self.assertEqual(after.refs, before.refs)
                    self.assert_metadata(after)
                    rendered = after.render(compact=compact)
                    combined = rendered["text"] + rendered["details"]
                    self.assertIn("Open", rendered["text"])
                    self.assertIn("native getter deadline", combined)
                    self.assertIn("numeric_value.minimum", combined)
                    self.assertIn("geometry.bounds", combined)

    def assert_metadata(self, snapshot):
        for identity, native_node in snapshot.native.nodes.items():
            bounds = native_node.geometry["bounds"]
            self.assertEqual((bounds.x, bounds.y, bounds.width, bounds.height), (-25, 16, 640, 480))
            self.assertIn(bounds.space, snapshot.native.coordinate_spaces)
            self.assertEqual(
                (
                    native_node.numeric_value.minimum,
                    native_node.numeric_value.maximum,
                    native_node.numeric_value.small_step,
                ),
                (0, 100, 1),
            )
            if identity != "group":
                self.assertEqual(native_node.numeric_value.description, "50 percent")
        self.assertTrue(
            any(
                issue.state == "timeout" and issue.detail == "native getter deadline"
                for issue in snapshot.native.issues
            )
        )

    def test_unrelated_native_fields_keep_existing_value_and_failure_annotations(self):
        raw = with_metadata(original("macos"))
        raw["nodes"][1]["requestedFields"].extend(("VendorState", "VendorFailure"))
        raw["nodes"][1]["values"]["VendorState"] = {"value": False}
        raw["nodes"][1]["fieldStatus"]["VendorFailure"] = dict(state="error", detail="provider failure")
        result = normalizeFacts(raw, DisplayReferences("session")).render()
        self.assertIn("provider.macos.VendorState", result["text"])
        self.assertIn("VendorFailure: error", result["text"])
        self.assertIn("provider failure", result["text"])

    def test_metadata_changes_do_not_change_structured_delivery(self):
        for platform in ("macos", "linux"):
            with self.subTest(platform=platform):
                before = normalizeFacts(with_metadata(original(platform)), DisplayReferences("session"))
                after = normalizeFacts(with_metadata(original(platform), offset=200), DisplayReferences("session"))
                first, second = prepareSnapshot(before), prepareSnapshot(after)
                self.assertEqual(first.nodes, second.nodes)
                plan = plan_document_diff(
                    DisplayDocument.tree(first), DisplayDocument.tree(second), before.refs, after.refs
                )
                self.assertEqual(plan.mode, "unchanged")
                self.assertTrue(all(not tree.changes for tree in plan.trees))

    def test_compression_fallback_preserves_normalized_metadata_and_all_references(self):
        for platform in ("macos", "linux"):
            with self.subTest(platform=platform):
                before = normalizeFacts(original(platform), DisplayReferences("session"))
                after = normalizeFacts(with_metadata(original(platform)), DisplayReferences("session"))
                with patch(
                    "computer_use.interaction.read.presentation.ir.validate_projection",
                    side_effect=ValueError("fixture projection failure"),
                ):
                    first, second = prepareSnapshot(before), prepareSnapshot(after)
                self.assertTrue(first.roots[0].failure)
                self.assertTrue(second.roots[0].failure)
                self.assertEqual(after.refs, before.refs)
                self.assert_metadata(after)
                rendered = renderPreparedSnapshot(second)
                self.assertIn("geometry.bounds", rendered["text"])
                self.assertIn("numeric_value.minimum", rendered["text"])
                self.assertIn("Open", rendered["text"])

    def test_windows_existing_geometry_and_range_profile_are_unchanged(self):
        features = ("nativeUiaReadV2", FEATURE)
        requested = profile("windows", features)
        for name in ("BoundingRectangle", "RangeValue.Minimum", "RangeValue.Maximum", "RangeValue.SmallChange"):
            self.assertIn(name, requested.fields)
        self.assertEqual(requested.fields, profile("windows", features[:-1]).fields)


if __name__ == "__main__":
    unittest.main()
