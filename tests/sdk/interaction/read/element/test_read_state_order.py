"""Element publication borrows one App state at the original sampling point."""

import unittest
from unittest.mock import patch

from observation_fixture import normalize_fixture as normalizeFacts
from sdk.interaction.read.facts.test_native_facts import batch, node

from computer_use import Computer
from computer_use.interaction.read.element.model import ElementReadPlan
from computer_use.interaction.read.element.pipeline import ControllerElementReadPipeline
from computer_use.interaction.read.numbering.references import DisplayReferences
from computer_use.interaction.read.presentation.preparation import prepareSnapshot
from computer_use.interaction.requests import ReadElement
from computer_use.native.contracts import Binding


class ElementReadStateOrderTests(unittest.TestCase):
    def test_remember_samples_once_and_captures_title_before_preparation(self):
        order = []

        class State:
            @property
            def directory(self):
                order.append("directory")
                return dict(surfaces=[])

            @property
            def name(self):
                order.append("name")
                return "fixture"

        state = State()
        controller = Computer()._controller
        self.assertIsNone(controller.transport._child)

        def state_of(owner):
            order.append("state")
            return state

        def remember(*args):
            order.append("remember")

        controller.state, controller._remember = state_of, remember
        pipeline = ControllerElementReadPipeline(controller)
        names = [step.name for step in pipeline.root.steps]
        begin = names.index("remember_element_observation")
        self.assertEqual(
            names[begin : begin + 3],
            [
                "remember_element_observation",
                "format_element_observation",
                "PrepareElementDocument",
            ],
        )
        target = Binding("s", "instance", "window", "surface", "node")
        plan = ElementReadPlan(ReadElement(object(), None, None, 0), dict(binding=target), tree=object())

        snapshot = normalizeFacts(
            batch([node("node", AXRole="AXStaticText", AXValue="tree")]), DisplayReferences("session")
        )
        prepared = prepareSnapshot(snapshot, compact=True, public_api=False)

        def prepare(*args):
            order.append("prepare")
            return prepared

        with (
            patch.object(pipeline.root.steps[begin + 2].preparation, "run", side_effect=prepare),
            patch(
                "computer_use.interaction.read.presentation.rendering.document.render_document",
                side_effect=AssertionError("Text generated before publication"),
            ),
        ):
            for step in pipeline.root.steps[begin : begin + 3]:
                plan = step.run(plan)

        self.assertEqual(order, ["state", "remember", "directory", "name", "prepare"])
        self.assertIs(plan.publication.state, state)
        self.assertEqual(
            plan.publication.document.text,
            'Application UI root, App: "fixture".\nTree: full (element subtree)\n0 static text tree',
        )
        self.assertIsNone(controller.transport._child)
