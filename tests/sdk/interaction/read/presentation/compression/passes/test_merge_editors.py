"""Each full projection pass has an input/output contract independent of I/O."""

import copy
import unittest

from sdk.interaction.read.presentation.fixtures import n, state

from computer_use.interaction.read.presentation.compression.model import Scope
from computer_use.interaction.read.presentation.compression.passes.merge_editors import merge_editors


class ControlPassTests(unittest.TestCase):
    def test_editor_composition_requires_explicit_members_and_equal_values(self):
        root = n(
            0,
            "comboBox",
            n(1, "textField", value="abc", selectionRange=dict(location=3, length=0)),
            value="abc",
            control=dict(kind="textInput", members=[dict(ref="1", part="editor")]),
        )
        projection = state(root)
        merge_editors(projection, projection.edit, projection.view, Scope())
        self.assertEqual(projection.view["children"], ())
        self.assertEqual(projection.view["selectionRange"], dict(location=3, length=0))
        self.assertEqual(projection.foldedInto, {"1": "0"})
        projection.assert_integrity()
        for difference in (
            {"value": "different"},
            {"actions": [dict(name="Delete")]},
            {"capabilities": dict(typeText="available")},
            {"partialReason": "not read"},
        ):
            with self.subTest(difference=difference):
                altered = copy.deepcopy(root)
                altered["children"][0].update(difference)
                projection = state(altered)
                merge_editors(projection, projection.edit, projection.view, Scope())
                self.assertEqual([c["ref"] for c in projection.view["children"]], ["1"])
                self.assertEqual(projection.foldedInto, {})
