"""Each full projection pass has an input/output contract independent of I/O."""

import unittest

from sdk.interaction.read.presentation.fixtures import n, state

from computer_use.interaction.read.presentation.compression.model import Scope
from computer_use.interaction.read.presentation.compression.passes.promote_description import promote_description


class LabelPassTests(unittest.TestCase):
    def test_description_promotion_is_not_capability_promotion(self):
        projection = state(n(0, "button", description="Save", capabilities=dict(click="unknown")))
        promote_description(projection, projection.edit, projection.view, Scope())
        self.assertEqual(projection.view["name"], "Save")
        self.assertEqual(projection.view["description"], "")
        self.assertEqual(projection.view["capabilities"]["click"], "unknown")
        self.assertEqual(projection.view["textSources"][-1]["attribute"], "description")
        projection.assert_integrity()
