"""A presentation deadline preserves completed effects and exact image authority."""

import unittest
from io import BytesIO
from unittest.mock import patch

import pytest
from PIL import Image

from computer_use.client.host import (
    set_action_handler,
    set_diagnostic_handler,
    set_execution_guard,
    set_observation_handler,
    set_preview_handler,
    set_text_page_handler,
)
from computer_use.client.images import register_image
from computer_use.protocol.png import PNG
from computer_use.session import images
from computer_use.session.events import observation_completed, register_observation_delivery
from computer_use.session.observation import lazy_observation
from computer_use.session.operations import current_operation_budget, operation_budget
from computer_use_mcp.image_delivery import prepare_image
from computer_use_mcp.runtime import Runtime

from regression.compatibility.test_mcp_default_diff_delivery import fixture


@pytest.mark.usefixtures("native_read")
class OutputPreparationBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.addCleanup(setattr, images, "_delivery_required", images.image_delivery_required())
        self.events, self.tree_acks, self.image_acks = [], [], []
        self.runtime = Runtime(self.events.append, computer_factory=object)
        observe, _ = fixture()
        self.captured = observe("Initial", publish=False)
        self.document = self.captured["document"]
        buffer = BytesIO()
        Image.new("RGB", (20, 20), "white").save(buffer, "PNG")
        self.png = PNG(buffer.getvalue())
        register_image(self.png, {"kind": "desktop", "delivered": lambda *_: self.image_acks.append(self.png)})
        self.state = lazy_observation(self.document, self.png)
        register_observation_delivery(self.state, lambda: self.tree_acks.append(self.state))
        self.captured["state"] = self.state
        self.budget = None

        def capture(expired=False, timeout_ms=1000, elapsed=0):
            with operation_budget(timeout_ms) as budget:
                self.budget = budget
                if elapsed:
                    budget._clock = lambda: budget.started + elapsed
                if expired:
                    budget._clock = lambda: budget.deadline + 1
                observation_completed(self.captured)
            return self.state

        self.runtime.task_namespace.update(
            capture=lambda expired=False, timeout_ms=1000, elapsed=0: self.runtime.executor.call(
                capture, expired, timeout_ms, elapsed
            ),
            expire=lambda: setattr(self.budget, "_clock", lambda: self.budget.deadline + 1),
            action=lambda: self.runtime.executor.call(
                self.runtime.action, {"target": "button", "status": "dispatched", "detail": "Sent once."}
            ),
        )

    async def asyncTearDown(self):
        await self.runtime.close()
        for setter in (
            set_action_handler,
            set_diagnostic_handler,
            set_execution_guard,
            set_observation_handler,
            set_preview_handler,
            set_text_page_handler,
        ):
            setter(None)

    async def execute(self, code):
        return await self.runtime.execute({"executionId": 1, "code": code})

    async def test_default_operation_publishes_tree_and_image_after_two_seconds(self):
        result = await self.execute("state = capture(timeout_ms=None, elapsed=3.1)")
        self.assertFalse(result.get("isError"), result)
        self.assertIsNone(self.budget.timeout_ms)
        self.assertFalse(self.budget.expired)
        self.assertTrue(self.budget.target_exceeded)
        self.assertEqual(self.tree_acks, [self.state])
        self.assertEqual(self.image_acks, [self.png])
        self.assertEqual(sum(block["type"] == "image" for block in result["content"]), 1)
        self.assertIn("Form", "\n".join(block["text"] for block in result["content"] if block["type"] == "text"))

    async def test_image_encoding_happens_in_original_budget_and_completed_draft_survives_later_expiry(self):
        budgets = []

        def encode(png):
            budgets.append(current_operation_budget())
            return prepare_image(png)

        with patch("computer_use_mcp.runtime.prepare_image", side_effect=encode):
            result = await self.execute("state = capture()\nexpire()")
        self.assertFalse(result.get("isError"), result)
        self.assertEqual(budgets, [self.budget])
        self.assertTrue(self.budget.expired)
        self.assertEqual(self.tree_acks, [self.state])
        self.assertEqual(self.image_acks, [self.png])
        self.assertEqual(sum(block["type"] == "image" for block in result["content"]), 1)

    async def test_expired_text_preparation_keeps_receipt_and_ready_image_without_acknowledging_tree(self):
        original = self.runtime.presenter.prepare

        def expire_after_image(observation, budget):
            self.assertIs(budget, self.budget)
            budget._clock = lambda: budget.deadline + 1
            return original(observation, budget)

        with patch.object(self.runtime.presenter, "prepare", side_effect=expire_after_image):
            result = await self.execute("action()\ncapture()")
        self.assertTrue(result.get("isError"), result)
        text = "\n".join(block["text"] for block in result["content"] if block["type"] == "text")
        self.assertEqual(text.count("Action [dispatched]"), 1)
        self.assertIn("Observation presentation: partial [original operation deadline]", text)
        self.assertIn("Tree delivery: not acknowledged", text)
        self.assertEqual(self.tree_acks, [])
        self.assertEqual(self.image_acks, [self.png])
        self.assertFalse(self.runtime.presenter._baselines.memory)

    async def test_expiry_before_image_preparation_does_not_start_encoding_during_finish(self):
        with patch("computer_use_mcp.runtime.prepare_image", side_effect=AssertionError("late image encoding")):
            result = await self.execute("capture(True)")
        self.assertTrue(result.get("isError"), result)
        text = "\n".join(block["text"] for block in result["content"] if block["type"] == "text")
        self.assertIn("Prepared image: unavailable", text)
        self.assertEqual(self.tree_acks, [])
        self.assertEqual(self.image_acks, [])
        self.assertFalse(any(block["type"] == "image" for block in result["content"]))

    async def test_oversized_observation_retains_detail_pages_receipt_and_image(self):
        with patch.object(self.runtime.presenter, "text", return_value="表" * 100_000):
            result = await self.execute("capture()\naction()")
        self.assertFalse(result.get("isError"), result)
        text = "\n".join(block["text"] for block in result["content"] if block["type"] == "text")
        self.assertIn("Tree: deferred (MCP text budget)", text)
        self.assertIn("captured_observation_1.read_details()", text)
        self.assertIn("Action [dispatched]", text)
        self.assertLessEqual(len(text.encode()), 256 * 1024)
        self.assertEqual(sum(block["type"] == "image" for block in result["content"]), 1)
        self.assertEqual(self.image_acks, [self.png])
        self.assertEqual(self.tree_acks, [])
        self.assertFalse(self.runtime.presenter._baselines.memory)
        self.assertIs(self.runtime.task_namespace["captured_observation_1"], self.state)

        details = await self.execute("print(captured_observation_1.read_details().text)")
        self.assertFalse(details.get("isError"), details)
        self.assertIn("Form", details["content"][0]["text"])
        self.assertEqual(self.tree_acks, [])

    async def test_observation_does_not_consume_space_for_later_receipt(self):
        # The rendered text by itself fits, but not beside its image and a
        # receipt already accepted while the script ran.
        with patch.object(self.runtime.presenter, "text", return_value="o" * (256 * 1024 - 128)):
            result = await self.execute("capture()\nprint('x' * 128)\naction()")
        self.assertFalse(result.get("isError"), result)
        text = "\n".join(block["text"] for block in result["content"] if block["type"] == "text")
        self.assertIn("Tree: deferred (MCP text budget)", text)
        self.assertIn("Action [dispatched]", text)
        self.assertEqual(self.image_acks, [self.png])
        self.assertEqual(self.tree_acks, [])


class PurePresentationCheckpointTests(unittest.TestCase):
    def deadline_after_two_batches(self):
        from computer_use.session.operations import OperationDeadlineExceeded

        visits = []

        def checkpoint():
            visits.append(True)
            if len(visits) == 2:
                raise OperationDeadlineExceeded("injected original deadline")
            return 1

        return visits, checkpoint

    def test_size_loop_yields_inside_large_collections(self):
        from computer_use.interaction.read.presentation.sizing import DocumentSize
        from computer_use.session.operations import OperationDeadlineExceeded

        visits, checkpoint = self.deadline_after_two_batches()
        source = {"large": tuple(range(100_000))}
        with self.assertRaises(OperationDeadlineExceeded):
            DocumentSize(checkpoint=checkpoint).data(source)
        self.assertEqual(len(visits), 2)
        self.assertEqual(source["large"][-1], 99_999)


class PresentationBudgetBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_cpu_stage_uses_sdk_clock_and_keeps_original_control_metadata(self):
        from computer_use.interaction.read.acquisition.model import AcquiredObservation, Components
        from computer_use.interaction.read.pipeline import _PrepareCapturedObservation
        from computer_use.pipeline.core import Control

        flow = _PrepareCapturedObservation(object())

        def cancelled():
            return False

        original = Control(
            remaining=lambda: (_ for _ in ()).throw(AssertionError("native component clock reused")),
            cancelled=cancelled,
            call_id="original-call",
            path=("parent",),
        )
        marker = AcquiredObservation(None, None, None, Components(None), (), False)

        def inspect(value, control):
            self.assertIs(value, marker)
            self.assertIs(control.cancelled, cancelled)
            self.assertEqual(control.call_id, "original-call")
            self.assertTrue(control.path[:1] == ("parent",))
            control.remaining()
            return value

        with patch.object(flow.presentation, "run", side_effect=inspect), operation_budget(1000) as budget:
            self.assertIs(flow.run(marker, original), marker)
            budget._clock = lambda: budget.deadline + 1
            retained = flow.run(marker, original)
            self.assertFalse(retained.snapshots_numbered)
            self.assertIs(retained.acquired.components, marker.components)
            self.assertEqual(retained.acquired.gaps[-1].component, "presentation")

    async def test_private_reader_component_expiry_does_not_invent_a_cpu_deadline(self):
        from computer_use.interaction.read.pipeline import _PrepareCapturedObservation
        from computer_use.pipeline.core import Control

        flow = _PrepareCapturedObservation(object())
        original = Control(remaining=lambda: (_ for _ in ()).throw(TimeoutError("native component exhausted")))
        seen = []
        with patch.object(flow.presentation, "run", side_effect=lambda value, control: seen.append(control)):
            flow.run(object(), original)
        self.assertEqual(len(seen), 1)
        self.assertIsNone(seen[0].remaining)
