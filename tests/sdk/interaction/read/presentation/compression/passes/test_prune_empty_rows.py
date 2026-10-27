"""Each full projection pass has an input/output contract independent of I/O."""

import unittest

from sdk.interaction.read.presentation.fixtures import n, state

from computer_use.interaction.read.presentation.compression.model import Scope
from computer_use.interaction.read.presentation.compression.passes.prune_empty_rows import prune_empty_rows


class StructurePassTests(unittest.TestCase):
    def test_empty_rows_are_not_synonymous_with_unimportant_rows(self):
        projection = state(n(0, "row"))
        self.assertEqual(prune_empty_rows(projection, projection.edit, projection.view, Scope()), [])
        for properties in (
            dict(selectable=True),
            dict(selected=True),
            dict(enabled=False),
            dict(value=""),
            dict(disclosureLevel=0),
        ):
            projection = state(n(0, "row", **properties))
            self.assertEqual(prune_empty_rows(projection, projection.edit, projection.view, Scope()), [projection.view])
        self.assertEqual(
            prune_empty_rows(projection, projection.edit, projection.view, Scope(in_table=True)), [projection.view]
        )
