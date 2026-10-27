"""Each full projection pass has an input/output contract independent of I/O."""

import copy
import unittest

from sdk.interaction.read.presentation.fixtures import n, state

from computer_use.interaction.read.presentation.compression.passes.associate_labels import associate_labels
from computer_use.interaction.read.presentation.ir import thaw


class LabelPassTests(unittest.TestCase):
    def test_explicit_label_association_retains_link_target_and_source_fields(self):
        source = n(
            0,
            "window",
            n(1, "button", titleRef="2"),
            n(2, "staticText", n(3, "link", name="Learn", url="https://example.org/?q=1#part"), value="Enable"),
        )
        before, projection = copy.deepcopy(source), state(source)
        projection.run(associate_labels)
        button = projection.nodes["1"]
        self.assertEqual(button["name"], "Enable")
        self.assertEqual([c["ref"] for c in button["children"]], ["3"])
        self.assertEqual(projection.foldedInto, {"2": "1"})
        self.assertEqual(
            thaw(button["textSources"]), [dict(sourceRef="2", attribute="value", field="name", text="Enable")]
        )
        self.assertEqual(projection.nativeNodes["3"]["url"], "https://example.org/?q=1#part")
        projection.assert_integrity()
        self.assertEqual(source, before)

    def test_shared_cross_window_and_actionable_labels_are_not_consumed(self):
        root = n(
            0,
            "application",
            n(
                1,
                "window",
                n(2, "button", titleRef="4"),
                n(3, "button", titleRef="4"),
                n(4, "staticText", value="Shared"),
            ),
            n(5, "window", n(6, "button", titleRef="4")),
        )
        projection = state(root)
        associate_labels(projection, projection.edit)
        self.assertEqual(projection.foldedInto, {})
        self.assertEqual(projection.nodes["2"]["name"], "Shared")
        self.assertEqual(projection.nodes["3"]["name"], "Shared")
        self.assertNotIn("name", projection.nodes["6"])
        self.assertIn(projection.nodes["4"], projection.nodes["1"]["children"])
        actionable = state(
            n(
                0,
                "window",
                n(1, "button", titleRef="2"),
                n(2, "staticText", value="Label", actions=[dict(name="Delete")]),
            )
        )
        associate_labels(actionable, actionable.edit)
        self.assertEqual(actionable.foldedInto, {})
        self.assertEqual(actionable.nodes["1"]["name"], "Label")
        self.assertEqual(thaw(actionable.nodes["2"]["actions"]), [dict(name="Delete")])
