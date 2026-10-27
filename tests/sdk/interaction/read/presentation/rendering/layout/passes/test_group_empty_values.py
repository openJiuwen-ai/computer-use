"""Each full projection pass has an input/output contract independent of I/O."""

import unittest

from sdk.interaction.read.presentation.fixtures import n, state

from computer_use.interaction.read.presentation.rendering.formatting import field
from computer_use.interaction.read.presentation.rendering.layout.passes.group_empty_values import group_empty_values


class LineLayoutPassTests(unittest.TestCase):
    def test_empty_values_keep_all_cell_refs_and_addresses(self):
        projection = state(n(0, "table", *(n(i, "cell", value="") for i in (1, 2, 3))))
        lines = ["0 table", '\t1 cell A1, Value: ""', '\t2 cell A2, Value: ""', '\t3 cell A3, Value: ""']
        packed = group_empty_values(lines, projection.roots, {}, field)
        self.assertEqual(packed[-1], '\tEmpty values [cell; Value: ""]: 1 A1, 2 A2, 3 A3')
        self.assertEqual(group_empty_values(lines, projection.roots, dict(focusedRef="2"), field), lines)
