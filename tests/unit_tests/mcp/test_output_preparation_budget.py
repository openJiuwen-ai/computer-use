"""A presentation deadline preserves completed effects and exact image authority."""

import unittest
from io import BytesIO
from unittest.mock import patch

from computer_use.client.host import (
    set_action_handler,
    set_diagnostic_handler,
    set_execution_guard,
    set_observation_handler,
    set_preview_handler,
    set_text_page_handler,
)
from computer_use.client.images import register_image
from computer_use.identity import Record
from computer_use.interaction.read.facts.normalize import normalize_facts
from computer_use.interaction.read.numbering.references import DisplayReferences
from computer_use.interaction.read.presentation.document import DisplayDocument
from computer_use.interaction.read.presentation.preparation import prepare_snapshot
from computer_use.protocol.observation import NativeNode, NativeObservation
from computer_use.protocol.png import PNG
from computer_use.session import images as images_state
from computer_use.session.events import observation_completed, register_observation_delivery
from computer_use.session.observation import lazy_observation
from computer_use.session.operations import current_operation_budget, operation_budget
from computer_use_mcp.image_delivery import prepare_image
from computer_use_mcp.runtime import Runtime
from PIL import Image


class OutputPreparationBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.previous_delivery = images_state._delivery_required
        self.events, self.tree_acks, self.image_acks = [], [], []
        self.runtime = Runtime(self.events.append, computer_factory=object)
        native = NativeObservation(
            "macos", "budget", "capture", (1, 2), ("window",), {"window": NativeNode(role="window", name="Form")}
        )
        snapshot = normalize_facts(native, DisplayReferences("budget"))
        self.document = DisplayDocument.tree(prepare_snapshot(snapshot)).scoped(("window",))
        buffer = BytesIO()
        Image.new("RGB", (20, 20), "white").save(buffer, "PNG")
        self.png = PNG(buffer.getvalue())
        register_image(self.png, {"kind": "desktop", "delivered": lambda *_: self.image_acks.append(self.png)})
        self.state = lazy_observation(self.document, self.png)
        register_observation_delivery(self.state, lambda: self.tree_acks.append(self.state))
        self.captured = {
            "source": Record(), "scope": "window", "view": "window", "document": self.document,
            "state": self.state, "refs": dict(snapshot.refs), "full": False, "publicApi": True,
        }
        self.budget = None

        def capture(expired=False):
            with operation_budget() as budget:
                self.budget = budget
                if expired:
                    budget._clock = lambda: budget.deadline + 1
                observation_completed(self.captured)
            return self.state

        self.runtime.task_namespace.update(
            capture=lambda expired=False: self.runtime.executor.call(capture, expired),
            expire=lambda: setattr(self.budget, "_clock", lambda: self.budget.deadline + 1),
            action=lambda: self.runtime.executor.call(
                self.runtime.action, {"target": "button", "status": "dispatched", "detail": "Sent once."}
            ),
        )

    async def asyncTearDown(self):
        await self.runtime.close()
        for setter in (
            set_action_handler, set_diagnostic_handler, set_execution_guard,
            set_observation_handler, set_preview_handler, set_text_page_handler,
        ):
            setter(None)
        images_state._delivery_required = self.previous_delivery

    async def execute(self, code):
        return await self.runtime.execute({"executionId": 1, "code": code})

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
        self.assertIn("Observation presentation incomplete", text)
        self.assertIn("tree was not delivered or acknowledged", text)
        self.assertEqual(self.tree_acks, [])
        self.assertEqual(self.image_acks, [self.png])
        self.assertFalse(self.runtime.presenter._baselines.memory)

    async def test_expiry_before_image_preparation_does_not_start_encoding_during_finish(self):
        with patch("computer_use_mcp.runtime.prepare_image", side_effect=AssertionError("late image encoding")):
            result = await self.execute("capture(True)")
        self.assertTrue(result.get("isError"), result)
        text = "\n".join(block["text"] for block in result["content"] if block["type"] == "text")
        self.assertIn("No prepared image is available", text)
        self.assertEqual(self.tree_acks, [])
        self.assertEqual(self.image_acks, [])
        self.assertFalse(any(block["type"] == "image" for block in result["content"]))


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

    def test_signature_and_size_loops_yield_inside_large_collections(self):
        from computer_use.interaction.read.delivery.diff.baseline import _Signatures
        from computer_use.interaction.read.presentation.sizing import DocumentSize
        from computer_use.session.operations import OperationDeadlineExceeded

        for create in (lambda check: _Signatures(check).of, lambda check: DocumentSize(checkpoint=check).data):
            visits, checkpoint = self.deadline_after_two_batches()
            source = {"large": tuple(range(100_000))}
            with self.assertRaises(OperationDeadlineExceeded):
                create(checkpoint)(source)
            self.assertEqual(len(visits), 2)
            self.assertEqual(source["large"][-1], 99_999)

    def test_existing_interface_writer_checks_inside_node_and_slot_batches(self):
        from computer_use.interaction.read.presentation.interface.text.formatting import format_interface_blocks
        from computer_use.interaction.read.presentation.interface.text.model import InterfaceTextRequest
        from computer_use.protocol.observation import Relation
        from computer_use.session.operations import OperationDeadlineExceeded

        children = tuple(f"button-{i}" for i in range(512))
        nodes = {"window": NativeNode(role="window", relations={"children": Relation("children", children)})}
        nodes.update({token: NativeNode(role="button", name=token) for token in children})
        native = NativeObservation("macos", "checkpoints", "capture", (1, 2), ("window",), nodes)
        snapshot = normalize_facts(native, DisplayReferences("checkpoints"))
        prepared = prepare_snapshot(snapshot)
        root = prepared.roots[0]
        request = InterfaceTextRequest(
            root.interface, root.atoms, root.materialize(), root.disclosure,
            root.collections, root.fields, root.sharing, root.coverage,
            root.text_slices, root.groups, prepared.snapshot, root.readable,
        )
        visits, checkpoint = self.deadline_after_two_batches()
        with self.assertRaises(OperationDeadlineExceeded):
            format_interface_blocks(request, checkpoint=checkpoint)
        self.assertEqual(len(visits), 2)
        self.assertEqual(prepared.snapshot.native.nodes[children[-1]].name, children[-1])


class PresentationBudgetBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_cpu_stage_uses_sdk_clock_and_keeps_original_control_metadata(self):
        from computer_use.interaction.read.pipeline import _PrepareCapturedObservation
        from computer_use.pipeline.core import Control
        from computer_use.session.operations import OperationDeadlineExceeded

        flow = _PrepareCapturedObservation(object())
        cancelled = lambda: False
        original = Control(
            remaining=lambda: (_ for _ in ()).throw(AssertionError("native component clock reused")),
            cancelled=cancelled, call_id="original-call", path=("parent",),
        )
        marker = object()

        def inspect(value, control):
            self.assertIs(value, marker)
            self.assertIs(control.cancelled, cancelled)
            self.assertEqual(control.call_id, "original-call")
            self.assertTrue(control.path[:1] == ("parent",))
            control.remaining()
            return value

        with patch.object(flow.presentation, "run", side_effect=inspect), operation_budget() as budget:
            self.assertIs(flow.run(marker, original), marker)
            budget._clock = lambda: budget.deadline + 1
            with self.assertRaises(OperationDeadlineExceeded):
                flow.run(marker, original)

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
