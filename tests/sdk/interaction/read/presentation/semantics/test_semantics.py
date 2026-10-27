"""Common control composition: native evidence in, lossless model view out."""

import copy
import unittest

from computer_use.interaction.read.presentation.rendering.formatting import renderTree
from computer_use.interaction.read.presentation.semantics.controls import composeControls
from computer_use.protocol.presentation import decodeObservation


def node(ref, role, *children, **facts):
    return dict(ref=str(ref), role=role, children=list(children), **facts)


def caps(click="unavailable", typeText="unavailable", replaceText="unavailable", focus="unknown"):
    return dict(click=click, typeText=typeText, replaceText=replaceText, focus=focus)


def row():
    return node(
        100,
        "row",
        node(101, "cell", node(102, "staticText", value="Wi-Fi", actions=[])),
        selectable=True,
        capabilities=caps(click="available"),
    )


def input_control():
    return node(
        200,
        "comboBox",
        node(
            201,
            "unknown",
            node(202, "textField", value="before", capabilities=caps("unknown", "unknown", "unknown", "unknown")),
            value="before",
        ),
        value="before",
        capabilities=caps("available", "available", "available", "available"),
    )


def refs(tree):
    return [tree["ref"], *(ref for child in tree["children"] for ref in refs(child))]


class SemanticControls(unittest.TestCase):
    def test_single_label_item_composition_preserves_native_tree(self):
        source = row()
        before = copy.deepcopy(source)
        result = composeControls(source)
        self.assertEqual(
            result["control"],
            dict(kind="item", members=[dict(ref="101", part="layout"), dict(ref="102", part="label")]),
        )
        self.assertEqual(source, before)
        self.assertEqual(refs(result), refs(source))
        rendered = renderTree(result)
        self.assertIn("Wi-Fi", rendered["text"])
        self.assertEqual(rendered["projection"]["foldedInto"].get("102"), "100")

    def test_tabular_rows_are_not_flattened_into_buttons(self):
        self.assertNotIn("control", composeControls(node(99, "table", row()))["children"][0])
        source = row()
        source["children"].append(node(103, "cell", value="Other column"))
        self.assertNotIn("control", composeControls(source))

    def test_shared_label_and_independent_button_keep_their_identity(self):
        source = row()
        source["children"][0]["children"][0]["labelTargets"] = ["999"]
        source["children"][0]["children"].append(
            node(104, "button", name="Delete", capabilities=caps(click="available"))
        )
        result = composeControls(source)
        self.assertEqual(result["control"]["members"], [dict(ref="101", part="layout")])
        self.assertIn("104", renderTree(result)["refs"])
        self.assertIn("102", renderTree(result)["refs"])

    def test_secondary_actions_selection_and_unknown_are_not_lost(self):
        for facts in (
            dict(actions=[dict(name="Connect")]),
            dict(actions=[dict(name="AXShowMenu")]),
            dict(actionsUnavailable=True),
            dict(selectionRange=dict(location=0, length=2)),
            dict(focused=True),
            dict(help="Independent explanation"),
            dict(url="https://example.invalid/"),
        ):
            with self.subTest(facts=facts):
                source = row()
                source["children"][0]["children"][0].update(facts)
                result = composeControls(source)
                self.assertIn("102", renderTree(result)["refs"])
                self.assertEqual(result["children"][0]["children"][0]["value"], "Wi-Fi")

    def test_weak_metadata_cannot_erase_a_declared_action(self):
        for capabilities in (None, caps(), caps(focus="unavailable")):
            for action in ("AXShowMenu", "AXShowDefaultUI", "AXShowAlternateUI", "unknown-action"):
                source = row()
                source.pop("capabilities")
                source["actions"] = [dict(name=action)]
                leaf = source["children"][0]["children"][0]
                leaf["actions"] = [dict(name=action)]
                if capabilities is not None:
                    leaf["capabilities"] = capabilities
                before = copy.deepcopy(source)
                rendered = renderTree(composeControls(source))
                with self.subTest(capabilities=capabilities, action=action):
                    self.assertIn("100", rendered["refs"])
                    self.assertIn("102", rendered["refs"])
                    self.assertGreaterEqual(rendered["text"].count(action), 2)
                    self.assertEqual(source, before)

    def test_editor_needs_an_explicit_native_relationship(self):
        source = input_control()
        self.assertNotIn("control", composeControls(source))
        source["editorRef"] = "202"
        before = copy.deepcopy(source)
        result = composeControls(source)
        self.assertEqual(result["control"]["members"], [dict(ref="201", part="layout"), dict(ref="202", part="editor")])
        editor = result["children"][0]["children"][0]
        self.assertEqual(editor["capabilities"], source["capabilities"])
        self.assertNotIn("focusable", editor)
        self.assertEqual(source, before)
        self.assertEqual(refs(result), refs(source))

    def test_editor_proof_never_crosses_a_semantic_boundary(self):
        for boundary in ("scrollArea", "table", "document", "window", "webArea", "textField"):
            with self.subTest(boundary=boundary):
                source = input_control()
                source["editorRef"] = "202"
                source["children"][0]["role"] = boundary
                self.assertNotIn("control", composeControls(source))
        source = input_control()
        source["editorRef"] = "999"
        self.assertNotIn("control", composeControls(source))

    def test_disabled_editor_does_not_inherit_available_routes(self):
        source = input_control()
        source["editorRef"] = "202"
        source["children"][0]["children"][0].update(enabled=False, capabilities=caps(focus="unavailable"))
        self.assertEqual(
            composeControls(source)["children"][0]["children"][0]["capabilities"], caps(focus="unavailable")
        )

    def test_reported_editor_does_not_turn_a_window_or_button_into_an_input(self):
        for role in ("window", "sheet", "document", "table", "webArea", "button"):
            with self.subTest(role=role):
                source = input_control()
                source["editorRef"] = "202"
                source["children"][0]["children"][0]["role"] = role
                self.assertNotIn("control", composeControls(source))

    def test_partial_relationships_and_bounded_search_remain_uncompressed(self):
        source = input_control()
        source["editorRef"] = "202"
        source["children"][0]["nextChildOffset"] = 1
        self.assertNotIn("control", composeControls(source))
        source = node(
            1,
            "button",
            *(node(i, "staticText", value=str(i)) for i in range(2, 68)),
            capabilities=caps(click="available"),
        )
        self.assertNotIn("control", composeControls(source))
        self.assertEqual(refs(composeControls(source)), refs(source))

    def test_nested_modal_masks_only_the_blocked_scope(self):
        source = node(1, "window", node(2, "group", row()), node(5, "group", input_control(), modal=True))
        result = composeControls(source)
        self.assertEqual(result["blockedBy"], ["5"])
        self.assertEqual(result["children"][0]["children"][0]["capabilities"]["click"], "unavailable")
        self.assertEqual(result["children"][1]["blockedBy"], [])
        self.assertEqual(result["children"][1]["children"][0]["capabilities"]["typeText"], "available")

    def test_wire_retains_editor_edge_separately_from_display_composition(self):
        root = input_control()
        root["editorRef"] = "202"
        observation = dict(root=root, context={}, windows=[], current=None)
        decoded = decodeObservation(observation)
        self.assertEqual(decoded["root"]["editorRef"], "202")
        self.assertNotIn("control", decoded["root"])
        self.assertEqual(composeControls(decoded["root"])["control"]["kind"], "textInput")


if __name__ == "__main__":
    unittest.main()
