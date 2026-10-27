"""Each full projection pass has an input/output contract independent of I/O."""

import unittest

from sdk.interaction.read.presentation.fixtures import n, state

from computer_use.interaction.read.presentation.compression.model import Scope
from computer_use.interaction.read.presentation.compression.passes.merge_control_labels import merge_control_labels


class ControlPassTests(unittest.TestCase):
    def test_caption_composition_does_not_promote_child_actions(self):
        root = n(
            0,
            "button",
            n(1, "staticText", value="Save"),
            n(2, "button", name="Delete"),
            capabilities=dict(click="available"),
            control=dict(kind="button", members=[dict(ref="1", part="label")]),
        )
        projection = state(root)
        merge_control_labels(projection, projection.edit, projection.view, Scope())
        self.assertEqual(projection.view["name"], "Save")
        self.assertEqual([c["ref"] for c in projection.view["children"]], ["2"])
        self.assertEqual(projection.foldedInto, {"1": "0"})
        self.assertNotIn("actions", projection.view)
        projection.assert_integrity()

    def test_table_captions_and_explicit_subtree_members_are_preserved(self):
        root = n(
            0,
            "row",
            n(1, "cell", n(2, "staticText", value="Wi-Fi")),
            control=dict(kind="item", members=[dict(ref="1", part="layout"), dict(ref="2", part="label")]),
        )
        for context, scope in (({}, Scope(in_table=True)), ({"subtreeRef": "0"}, Scope())):
            with self.subTest(context=context):
                projection = state(root, **context)
                merge_control_labels(projection, projection.edit, projection.view, scope)
                self.assertEqual([c["ref"] for c in projection.view["children"]], ["1"])
                self.assertEqual(projection.foldedInto, {})
