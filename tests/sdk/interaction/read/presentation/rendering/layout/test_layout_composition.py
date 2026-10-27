"""Each full projection pass has an input/output contract independent of I/O."""

import unittest

from sdk.interaction.read.presentation.fixtures import n, state

from computer_use.interaction.read.presentation.rendering.formatting import field
from computer_use.interaction.read.presentation.rendering.layout.chains import expandTreeChains
from computer_use.interaction.read.presentation.rendering.layout.passes.join_container_chains import (
    join_container_chains,
)
from computer_use.interaction.read.presentation.rendering.layout.passes.join_text_parts import join_text_parts
from computer_use.interaction.read.presentation.rendering.layout.pipeline import layout_lines
from computer_use.interaction.read.presentation.rendering.layout.text_parts import expandTextParts


class LineLayoutPassTests(unittest.TestCase):
    def test_compact_and_subtree_keep_natural_lines_while_diagnostics_pack_them(self):
        cases = (
            (
                state(n(0, "group", n(1, "group", n(2, "button")))),
                ["0 container", "\t1 container", "\t\t2 button"],
                " > ",
            ),
            (
                state(n(0, "group", n(1, "staticText", value="Upload"), n(2, "staticText", value="1.2 MB/s"))),
                ["0 container", "\t1 text Value: Upload", "\t2 text Value: 1.2 MB/s"],
                "Text parts",
            ),
        )
        for projection, lines, marker in cases:
            with self.subTest(marker=marker):
                for context in ({"compact": True}, {"subtreeRef": "0"}, {"compact": True, "subtreeRef": "0"}):
                    output, _ = layout_lines(lines, projection.roots, context, field)
                    self.assertEqual(output, lines)
                    self.assertNotIn(marker, "\n".join(output))
                diagnostic, _ = layout_lines(lines, projection.roots, {"compact": False}, field)
                self.assertIn(marker, "\n".join(diagnostic))

    def test_chain_layout_and_text_parts_are_separately_reversible(self):
        projection = state(n(0, "group", n(1, "group", n(2, "button"))))
        lines = ["0 container", "\t1 container", "\t\t2 button"]
        packed = join_container_chains(lines, projection.roots, {}, field)
        self.assertEqual(expandTreeChains(packed), lines)
        projection = state(n(0, "group", n(1, "staticText", value="上传速度"), n(2, "staticText", value="1.2 MB/s")))
        lines = ["0 container", "\t1 text Value: 上传速度", "\t2 text Value: 1.2 MB/s"]
        packed = join_text_parts(lines, projection.roots, {}, field)
        self.assertEqual(expandTextParts(packed, field), lines)
        self.assertLess(len(packed), len(lines))
        combined, stages = layout_lines(lines, projection.roots, dict(recordStages=True), field)
        self.assertEqual(combined, packed)
        self.assertEqual(
            [s["name"] for s in stages], ["group_empty_values", "join_container_chains", "join_text_parts"]
        )
