"""Unified observation presentation contracts without native platform fixtures."""

import unittest

from computer_use.interaction.read.facts.index import observation_index
from computer_use.interaction.read.facts.normalize import normalizeFacts
from computer_use.protocol.observation import (
    Action,
    ControlValue,
    CoordinateSpace,
    ElementValue,
    Geometry,
    NativeNode,
    NativeObservation,
    NumericValue,
    ReadIssue,
    ReadRef,
    Relation,
    States,
    Supplement,
    TextInfo,
    TextSpan,
)


class References:
    session = "session"

    def __init__(self):
        self.values = {}

    def reference(self, token):
        return self.values.setdefault(token, str(len(self.values)))


def observation(nodes, *, backend="macos", issues=(), spaces=None):
    return NativeObservation(
        backend, "session", "capture", (0, 10), ("root",), nodes, coordinate_spaces=spaces or {}, issues=issues
    )


class NormalizedObservationPresentationTests(unittest.TestCase):
    def render(self, value, *, compact=False):
        snapshot = normalizeFacts(value, References())
        rendered = snapshot.render(compact=compact)
        self.assertTrue(all(part.get("presentation") != "uncompressed" for part in rendered["projections"]))
        return snapshot, rendered

    def test_backend_does_not_change_common_output(self):
        node = NativeNode(
            role="button",
            name="Save",
            actions=(Action("invoke", "button-action"),),
            relations={"children": Relation("children", ())},
        )
        outputs = [
            self.render(observation({"root": node}, backend=backend), compact=True)[1]["text"]
            for backend in ("macos", "windows", "linux")
        ]
        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual(outputs[1], outputs[2])

    def test_independent_value_channels_are_retained(self):
        node = NativeNode(
            role="document",
            name="Invoice",
            control_value=ControlValue("caption"),
            numeric_value=NumericValue(42, 0, 100),
            text=TextInfo("doc", unit="unicode_codepoint", content="document body"),
            actions=(),
        )
        snapshot, rendered = self.render(observation({"root": node}))
        values = snapshot.facts["nodes"][0]["values"]
        self.assertEqual(values["control_value.value"], "caption")
        self.assertEqual(values["numeric_value.value"], 42)
        self.assertEqual(values["text.content"], "document body")
        self.assertEqual(snapshot.value_fields["0"], "text.content")
        for content in ("caption", "document body", "42"):
            self.assertIn(content, rendered["details"])

    def test_same_kind_views_do_not_claim_combined_containment(self):
        root = NativeNode(
            role="list",
            relations={
                "raw": Relation("children", ("first",), view="raw", positions=(4,), total=20),
                "sample": Relation("children", ("second",), view="viewport", enumeration="sampled"),
            },
        )
        value = observation(
            {
                "root": root,
                "first": NativeNode(role="list_item", name="A"),
                "second": NativeNode(role="list_item", name="B"),
            },
            issues=(
                ReadIssue(
                    ("nodes", "root", "relations", "raw"), "partial", continuation=ReadRef("page", "relation", "live")
                ),
            ),
        )
        snapshot, rendered = self.render(value)
        self.assertEqual(snapshot.roots[0]["children"], [])
        self.assertEqual([edge["recordKey"] for edge in snapshot.facts["relations"]], ["raw", "sample"])
        self.assertEqual(snapshot.facts["relations"][0]["offset"], 4)
        self.assertIn("viewport", rendered["details"])

    def test_geometry_and_units_survive_details_without_platform_rules(self):
        node = NativeNode(
            role="button",
            geometry={"bounds": Geometry("desktop", -20, 3, 80, 10), "client": Geometry("window", 0, 0, 70, 7)},
        )
        spaces = {
            "desktop": CoordinateSpace("provider", "desktop_top_left", "down"),
            "window": CoordinateSpace("logical_pixel", "window_top_left", "down", owner="root"),
        }
        snapshot, rendered = self.render(observation({"root": node}, spaces=spaces))
        self.assertEqual(snapshot.native.nodes["root"].geometry["bounds"].x, -20)
        self.assertIn("geometry.bounds", rendered["details"])
        self.assertIn("logical_pixel", rendered["details"])

    def test_text_capture_and_native_selection_have_independent_units(self):
        node = NativeNode(
            role="text_field",
            text=TextInfo(
                "provider-space", unit="provider", content="😀", character_count=1, selections=(TextSpan(0, 1, "😀"),)
            ),
            actions=(),
        )
        continuation = ReadRef("text-token", "text", "captured", "utf16_code_unit", 12, (0, 2))
        value = observation(
            {"root": node},
            issues=(ReadIssue(("nodes", "root", "text", "content"), "partial", continuation=continuation),),
        )
        snapshot, rendered = self.render(value)
        self.assertNotIn("selectionRange", snapshot.roots[0])
        self.assertEqual(snapshot.text_refs[("0", "text.content")]["unit"], "utf16_code_unit")
        self.assertIn("provider", rendered["details"])
        self.assertIn("😀", rendered["text"])

    def test_noncallable_declaration_is_not_a_secondary_action(self):
        node = NativeNode(
            role="text_field",
            actions=(
                Action("set_value", target=("control_value", "value")),
                Action("custom", "native-key", label="Clear field"),
            ),
        )
        snapshot, rendered = self.render(observation({"root": node}), compact=True)
        self.assertEqual(snapshot.roots[0]["capabilities"]["replaceText"], "unknown")
        self.assertEqual(
            snapshot.roots[0]["actions"], [{"name": "native-key", "kind": "custom", "description": "Clear field"}]
        )
        self.assertIn("Clear field", rendered["text"])
        self.assertNotIn("Secondary Actions: set_value", rendered["text"])

    def test_selected_and_selectable_are_independent(self):
        node = NativeNode(role="list_item", states=States(selected=False, selectable=True), actions=())
        snapshot, _ = self.render(observation({"root": node}))
        self.assertIs(snapshot.roots[0]["selected"], False)
        self.assertIs(snapshot.roots[0]["selectable"], True)
        self.assertEqual(snapshot.roots[0]["capabilities"]["click"], "unknown")

    def test_unknown_role_user_fact_keeps_its_node(self):
        root = NativeNode(role="group", relations={"children": Relation("children", ("unknown",))}, actions=())
        unknown = NativeNode(
            role="unknown", supplements=(Supplement("provider.widget_kind", "Sketch Surface"),), actions=()
        )
        snapshot, rendered = self.render(observation({"root": root, "unknown": unknown}))
        self.assertTrue(snapshot.roots[0]["children"][0]["preserveGroup"])
        self.assertIn("Sketch Surface", rendered["details"])
        self.assertIn("1", rendered["refs"])

    def test_element_value_and_cycle_keep_exact_references(self):
        root = NativeNode(
            role="group",
            control_value=ControlValue(ElementValue("child")),
            relations={"children": Relation("children", ("child",))},
            actions=(),
        )
        child = NativeNode(role="button", relations={"children": Relation("children", ("root",))}, actions=())
        snapshot, rendered = self.render(observation({"root": root, "child": child}))
        self.assertEqual(snapshot.roots[0]["value"], "element 1")
        self.assertIn("contains 0 (cycle)", rendered["details"])

    def test_false_empty_and_partial_facts_do_not_collapse(self):
        node = NativeNode(
            role="text_field", name="", control_value=ControlValue(""), states=States(enabled=False), actions=()
        )
        value = observation({"root": node}, issues=(ReadIssue(("nodes", "root", "name"), "partial"),))
        index = observation_index(value)
        self.assertEqual(index["nodes"][0]["values"]["name"], "")
        self.assertIs(index["nodes"][0]["values"]["states.enabled"], False)
        self.assertEqual(index["nodes"][0]["fieldStatus"]["name"]["state"], "partial")
        self.assertEqual(index["nodes"][0]["actions"], {"state": "complete", "items": []})

    def test_duplicate_supplement_keys_and_dotted_geometry_keep_exact_paths(self):
        node = NativeNode(
            role="group",
            supplements=(Supplement("provider.kind", "first"), Supplement("provider.kind", "second")),
            geometry={"bounds.layer": Geometry("desktop", 0, 0, 1, 1)},
        )
        value = observation(
            {"root": node},
            spaces={"desktop": CoordinateSpace("logical_pixel", "desktop_top_left", "down")},
            issues=(ReadIssue(("nodes", "root", "supplements", 1, "value"), "partial"),),
        )
        snapshot, rendered = self.render(value)
        row = snapshot.facts["nodes"][0]
        self.assertEqual(row["values"]["supplements[0]"]["value"], "first")
        self.assertEqual(row["values"]["supplements[1]"]["value"], "second")
        self.assertEqual(snapshot.fields[("0", "supplements[1]")]["path"], ("supplements", 1))
        self.assertEqual(snapshot.fields[("0", 'geometry["bounds.layer"]')]["path"], ("geometry", "bounds.layer"))
        self.assertEqual(row["fieldStatus"]["supplements[1].value"]["state"], "partial")
        self.assertIn("first", rendered["details"])
        self.assertIn("second", rendered["details"])

    def test_structural_text_metadata_is_retained_without_redundant_annotations(self):
        node = NativeNode(role="document", text=TextInfo("native-text-store", unit="utf16_code_unit", content="Hello"))
        snapshot, rendered = self.render(observation({"root": node}))
        self.assertEqual(snapshot.native.nodes["root"].text.source, "native-text-store")
        self.assertEqual(snapshot.fields[("0", "text.source")]["representation"], "raw")
        self.assertNotIn("native-text-store", rendered["details"])
        self.assertNotIn("text.unit:", rendered["details"])

    def test_role_detail_cannot_overwrite_common_item_identity(self):
        for role in ("list_item", "tree_item", "dialog", "alert"):
            with self.subTest(role=role):
                snapshot, rendered = self.render(
                    observation({"root": NativeNode(role=role, role_detail="provider_detail")})
                )
                self.assertEqual(snapshot.roots[0]["subrole"], role)
                self.assertIn("provider_detail", rendered["details"])

    def test_whole_node_error_remains_visible_without_collected_properties(self):
        snapshot, rendered = self.render(
            observation(
                {"root": NativeNode()}, issues=(ReadIssue(("nodes", "root"), "timeout", "provider did not respond"),)
            )
        )
        self.assertEqual(snapshot.facts["nodes"][0]["fieldStatus"]["node"]["state"], "timeout")
        self.assertIn("provider did not respond", rendered["details"])
        self.assertTrue(snapshot.roots[0]["preserveGroup"])

    def test_partial_graph_does_not_invalidate_collected_field_or_action(self):
        node = NativeNode(role="text_field", text=TextInfo("body", content="retained"), actions=())
        value = observation({"root": node}, issues=(ReadIssue(("nodes",), "partial", "unexpanded related nodes"),))
        row = observation_index(value)["nodes"][0]
        self.assertEqual(row["fieldStatus"], {})
        self.assertEqual(row["actions"]["state"], "complete")
        self.assertEqual(row["values"]["text.content"], "retained")

    def test_unexpanded_stub_keeps_reference_after_observed_nodes_without_fake_tree_item(self):
        value = observation(
            {
                "root": NativeNode(
                    role="menu", relations={"children": Relation("children", ("first", "stub", "second"))}
                ),
                "first": NativeNode(role="menu_item", name="Open"),
                "stub": NativeNode(),
                "second": NativeNode(role="menu_item", name="Edit"),
            },
            issues=(ReadIssue(("nodes", "stub"), "not_read"),),
        )
        snapshot, rendered = self.render(value)
        self.assertEqual(rendered["refs"], {"0": "root", "1": "first", "2": "second", "3": "stub"})
        self.assertEqual([node["ref"] for node in snapshot.roots[0]["children"]], ["1", "2"])
        self.assertIn("0 contains 3 (unread)", rendered["details"])
        self.assertNotIn("3 unknown", rendered["text"])

    def test_action_human_description_does_not_get_replaced_by_native_key_label(self):
        node = NativeNode(
            role="button", actions=(Action("custom", "opaque-key", label="OpaqueAction", description="Copy link"),)
        )
        snapshot, rendered = self.render(observation({"root": node}), compact=True)
        self.assertEqual(snapshot.roots[0]["actions"][0]["description"], "Copy link")
        self.assertIn("Copy link", rendered["text"])
        self.assertEqual(snapshot.native.nodes["root"].actions[0].key, "opaque-key")


if __name__ == "__main__":
    unittest.main()
