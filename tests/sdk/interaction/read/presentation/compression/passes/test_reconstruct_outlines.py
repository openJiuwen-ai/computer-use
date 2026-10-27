"""Each full projection pass has an input/output contract independent of I/O."""

import unittest

from sdk.interaction.read.presentation.fixtures import n, state

from computer_use.interaction.read.presentation.compression.passes.reconstruct_outlines import reconstruct_outlines


class StructurePassTests(unittest.TestCase):
    def test_outline_requires_consistent_order_levels_and_acyclic_relations(self):
        root = n(
            0,
            "outline",
            n(1, "row", expanded=True, disclosureLevel=0),
            n(2, "row", disclosedBy="1", disclosureLevel=1),
            n(3, "row", disclosedBy="1", disclosureLevel=4),
        )
        projection = state(root)
        reconstruct_outlines(projection, projection.edit)
        self.assertEqual([c["ref"] for c in projection.view["children"]], ["1", "3"])
        self.assertEqual([c["ref"] for c in projection.nodes["1"]["children"]], ["2"])
        projection.assert_integrity()
        cyclic = state(n(0, "outline", n(1, "row", disclosedBy="2"), n(2, "row", disclosedBy="1")))
        reconstruct_outlines(cyclic, cyclic.edit)
        self.assertEqual([c["ref"] for c in cyclic.view["children"]], ["1", "2"])
