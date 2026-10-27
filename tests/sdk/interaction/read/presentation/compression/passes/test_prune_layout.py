"""Each full projection pass has an input/output contract independent of I/O."""

import unittest

from sdk.interaction.read.presentation.fixtures import n, state

from computer_use.interaction.read.presentation.compression.model import Scope
from computer_use.interaction.read.presentation.compression.passes.prune_layout import prune_layout


class StructurePassTests(unittest.TestCase):
    def test_layout_removes_only_unambiguous_wrappers(self):
        projection = state(n(0, "group", n(1, "button", name="Save")))
        projection.edit.roots(prune_layout(projection, projection.edit, projection.view, Scope()))
        self.assertEqual([c["ref"] for c in projection.roots], ["1"])
        projection.assert_integrity()
        variants = [
            dict(name="Region"),
            dict(preserveGroup=True),
            dict(modal=True),
            dict(focused=True),
            dict(selected=True),
            dict(help="Hint"),
            dict(partialReason="Timeout"),
            dict(value="content"),
            dict(actions=[dict(name="Custom action")]),
            dict(subrole="popover"),
        ]
        for properties in variants:
            with self.subTest(properties=properties):
                projection = state(n(0, "group", n(1, "button"), **properties))
                self.assertIs(prune_layout(projection, projection.edit, projection.view, Scope())[0], projection.view)
        # Two visible cards must not become one undifferentiated list.
        projection = state(
            n(
                0,
                "group",
                n(1, "group", n(2, "staticText", value="Card"), n(3, "button")),
                n(4, "group", n(5, "staticText", value="Card"), n(6, "button")),
            )
        )
        self.assertEqual(
            prune_layout(projection, projection.edit, projection.nodes["1"], Scope(projection.view)),
            [projection.nodes["1"]],
        )
