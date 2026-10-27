"""Each full projection pass has an input/output contract independent of I/O."""

import unittest

from sdk.interaction.read.presentation.fixtures import n, state

from computer_use.interaction.read.presentation.compression.passes.finalize_sources import finalize_sources


class PipelineIntegrityTests(unittest.TestCase):
    def test_finalization_resolves_display_sources_without_aliasing_input(self):
        root = n(0, "button", n(1, "group", n(2, "staticText", value="Save")), actions=[dict(name="AXPress")])
        projection = state(root)
        projection.edit.fold(projection.nodes["2"], projection.nodes["1"], "label")
        projection.edit.fold(projection.nodes["1"], projection.nodes["0"], "layout")
        projection.edit.children(projection.view, [])
        finalize_sources(projection, projection.edit)
        self.assertEqual(projection.foldedInto, {"2": "0", "1": "0"})
        self.assertEqual(projection.nativeNodes["2"]["value"], "Save")
        self.assertIsNot(projection.nativeNodes["2"], projection.nativeNodes["0"])
        projection.assert_integrity()
        projection.ir.folded_into = {"1": "2", "2": "1"}
        with self.assertRaisesRegex(ValueError, "cycle"):
            finalize_sources(projection, projection.edit)
