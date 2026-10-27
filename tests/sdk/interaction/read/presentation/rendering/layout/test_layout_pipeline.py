"""Layout is an explicit pure child graph, including subtree branch choice."""

import unittest

from computer_use.interaction.read.presentation.rendering.formatting import field
from computer_use.interaction.read.presentation.rendering.layout.model import LayoutFrame
from computer_use.interaction.read.presentation.rendering.layout.pipeline import LayoutPipeline
from computer_use.pipeline import Control, Effect


class LayoutPipelineTests(unittest.TestCase):
    def test_full_and_subtree_execute_only_their_registered_stages(self):
        for context, expected in (
            ({}, ["group_empty_values", "join_container_chains", "join_text_parts"]),
            ({"subtreeRef": "0"}, ["group_empty_values"]),
            ({"compact": True}, ["group_empty_values"]),
            ({"compact": True, "subtreeRef": "0"}, ["group_empty_values"]),
            ({"compact": False}, ["group_empty_values", "join_container_chains", "join_text_parts"]),
        ):
            with self.subTest(context=context):
                events = []
                frame = LayoutFrame(
                    ["0 window"],
                    [dict(ref="0", role="window", children=[], native=dict(children=[]))],
                    {**context, "recordStages": True},
                    field,
                )
                output = LayoutPipeline().run(frame, Control(diagnostic=events.append))
                self.assertEqual([record["name"] for record in output.records], expected)
                entered = [event.path[-1] for event in events if event.phase == "enter"]
                self.assertEqual([name for name in entered if name in expected], expected)
                if expected == ["group_empty_values"]:
                    self.assertNotIn("join_text_parts", entered)
                    self.assertNotIn("join_container_chains", entered)
                self.assertTrue(all(event.effects <= {Effect.PURE} for event in events))
                self.assertEqual(output.lines, frame.lines)
                self.assertEqual(frame.records, ())
