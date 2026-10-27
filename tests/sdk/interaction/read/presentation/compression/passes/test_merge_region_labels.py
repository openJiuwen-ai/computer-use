"""Each full projection pass has an input/output contract independent of I/O."""

import unittest

from sdk.interaction.read.presentation.fixtures import n, state

from computer_use.interaction.read.presentation.compression.model import Scope
from computer_use.interaction.read.presentation.compression.passes.merge_region_labels import merge_region_labels


class LabelPassTests(unittest.TestCase):
    def test_region_labels_require_exact_equivalence_and_keep_independent_actions(self):
        root = n(
            0,
            "group",
            n(1, "heading", name="Network"),
            n(2, "staticText", value="Network"),
            n(3, "staticText", value="Net"),
            n(4, "staticText", value="Network", actions=[dict(name="Delete")]),
        )
        projection = state(root)
        merge_region_labels(projection, projection.edit, projection.view, Scope())
        self.assertEqual(projection.view["name"], "Network")
        self.assertEqual([c["ref"] for c in projection.view["children"]], ["3", "4"])
        self.assertEqual(projection.foldedInto, {"1": "0", "2": "0"})
        projection.assert_integrity()
