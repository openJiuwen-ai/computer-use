"""Delivery selection does not publish state before actual output confirmation."""

import unittest
from unittest.mock import patch

from computer_use.interaction.read.delivery.diff.model import DeliverySelection
from computer_use.interaction.read.delivery.diff.pipeline import DeliverySelectionPipeline
from computer_use.pipeline import Control
from computer_use_mcp.presentation import ObservationPresenter


def observation(source, text='Window: "One", App: Test.\nTree: full\n0 button Save', *, full=False):
    return dict(
        source=source, scope="window", view="window", full=full, state=dict(text=text), refs={"0": "native-button"}
    )


class DeliveryPipelineTests(unittest.TestCase):
    def test_text_selection_keeps_delivered_baseline_until_confirmation(self):
        class Source:
            pass

        source, presenter = Source(), ObservationPresenter()
        first = observation(source)
        self.assertEqual(presenter.text(first), first["state"]["text"])
        self.assertEqual(presenter._baselines, {})
        presenter.delivered(first)
        old = dict(presenter._baselines)
        second = observation(source, first["state"]["text"].replace("Save", "Changed"))
        presenter.text(second)
        self.assertEqual(presenter._baselines, old)
        presenter.delivered(second)
        self.assertEqual(next(iter(presenter._baselines.values()))["text"], second["state"]["text"])

    def test_explicit_full_never_enters_diff_branch(self):
        class Source:
            pass

        source, events = Source(), []
        current = observation(source, full=True)
        before = dict(view="window", text="old", refs={"0": "native-button"})
        with patch(
            "computer_use.interaction.read.delivery.diff.passes.plan_observation_diff.plan_document_diff",
            side_effect=AssertionError("wrong branch"),
        ):
            result = DeliverySelectionPipeline().run(
                DeliverySelection(current, before), Control(diagnostic=events.append)
            )
        self.assertEqual(result, current["state"]["text"])
        self.assertTrue(any(event.path[-1] == "plan_full_delivery" for event in events))
        self.assertFalse(any(event.path[-1] == "plan_observation_diff" for event in events))

    def test_oversize_delivery_removes_previous_baseline_without_phantom_bytes(self):
        class Source:
            pass

        source, presenter = Source(), ObservationPresenter(maxBytes=512)
        presenter.delivered(observation(source))
        self.assertTrue(presenter._baselines)
        presenter.delivered(observation(source, "x" * 1000))
        self.assertEqual(presenter._baselines, {})
        self.assertEqual(presenter._bytes, 0)


if __name__ == "__main__":
    unittest.main()
