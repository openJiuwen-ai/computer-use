# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Action receipts share text capacity while retaining image boundaries."""

import re
import unittest
from functools import partial
from types import SimpleNamespace
from unittest.mock import patch

from computer_use.client.host import (
    set_action_handler,
    set_diagnostic_handler,
    set_execution_guard,
    set_observation_handler,
    set_preview_handler,
    set_text_page_handler,
)
from computer_use.session import images as images_state
from computer_use.session.results import action_result
from computer_use.errors import ComputerUseError
from computer_use.identity import Record
from computer_use.session.events import action_observation_completed, observation_completed
from computer_use_mcp.runtime import Runtime


class ActionReceiptTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.previous_delivery = images_state._delivery_required
        self.events = []
        self.runtime = Runtime(self.events.append)
        self.runtime.task_namespace.update(
            receipts=[dict(target="Fixture", status="dispatched", detail="Outcome unconfirmed.") for _ in range(150)],
            emit_receipt=partial(self.runtime.executor.call, self.runtime.action),
            emit_image=lambda: self.runtime.executor.call(
                self.runtime.emit,
                dict(
                    type="image",
                    mimeType="image/png",
                    data="iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j7l0AAAAASUVORK5CYII=",
                ),
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
        images_state._delivery_required = self.previous_delivery

    async def execute(self, code):
        return await self.runtime.execute(
            dict(
                code=code,
                executionId=1,
            )
        )

    async def test_many_receipts_fit_without_losing_actions_or_stopping_the_suffix(self):
        result = await self.execute("for receipt in receipts:\n    emit_receipt(receipt)\nprint('suffix reached')")
        self.assertFalse(result.get("isError"), result)
        self.assertEqual(len(result["content"]), 1)
        text = result["content"][0]["text"]
        self.assertEqual(re.findall(r"Action \[dispatched\]: #(\d+) ", text), [str(i) for i in range(1, 151)])
        self.assertTrue(text.endswith("suffix reached"))

    async def test_image_separates_two_groups_of_merged_action_text(self):
        result = await self.execute(
            "for receipt in receipts[:2]:\n    emit_receipt(receipt)\n"
            "emit_image()\n"
            "for receipt in receipts[2:4]:\n    emit_receipt(receipt)"
        )
        self.assertFalse(result.get("isError"), result)
        self.assertEqual([block["type"] for block in result["content"]], ["text", "image", "text"])
        self.assertEqual(result["content"][0]["text"].count("Action [dispatched]"), 2)
        self.assertEqual(result["content"][2]["text"].count("Action [dispatched]"), 2)

    async def test_unprinted_suffix_failure_preserves_receipt_and_exposes_choices(self):
        receipt = dict(target="Original document", status="dispatched", detail="Close input sent once.")
        outcome = SimpleNamespace(
            observation=None,
            observation_status="unavailable",
            observation_error=dict(
                code="window_closed",
                detail="Original window ended.",
                next="Choose a current instance for further work.",
                session_usable=True,
            ),
            window_lifecycle="closed",
            choices=(dict(id=7, title="Another document"),),
        )
        self.runtime.task_namespace.update(
            receipt=receipt,
            outcome=outcome,
            materialize=partial(self.runtime.executor.call, action_result),
        )
        result = await self.execute("emit_receipt(receipt)\nresult = materialize(receipt, observation=outcome)")
        self.assertFalse(result.get("isError"), result)
        text = "\n".join(block["text"] for block in result["content"])
        self.assertEqual(text.count("Action [dispatched]"), 1)
        self.assertIn("Related observation [unavailable]", text)
        self.assertIn("Action: dispatched", text)
        self.assertNotIn("do not replay", text)
        self.assertNotIn("Choose a current instance", text)
        self.assertIn("Original window: closed", text)
        self.assertIn("computer.window(7) Another document", text)

    async def test_suffix_delivery_failure_does_not_replace_returned_receipt(self):
        receipt = dict(target="Original document", status="dispatched", detail="Input sent once.")
        outcome = SimpleNamespace(
            observation=None,
            observation_status="unavailable",
            observation_error=dict(code="observation_timeout", detail="Read deadline elapsed."),
            window_lifecycle="unknown",
            choices=(),
        )

        def broken_handler(event):
            raise ValueError("host delivery failed")

        set_observation_handler(broken_handler)
        try:
            result = action_result(receipt, observation=outcome)
        finally:
            set_observation_handler(self.runtime.observation)
        self.assertEqual(result.status, "dispatched")
        self.assertEqual(result.observation_error["code"], "observation_timeout")

    async def test_uncertain_write_gap_does_not_invent_a_success_receipt(self):
        failure = ComputerUseError(
            dict(
                code="execution_unknown",
                target="Original field",
                effect="partial",
                detail="Only some input may have arrived.",
                next="Observe before continuing.",
            )
        )
        failure.observation_status = "unavailable"
        failure.observation_error = dict(
            code="observation_timeout", detail="Read budget expired.", next="Do not replay."
        )
        self.runtime.task_namespace.update(
            failure=failure, publish_gap=partial(self.runtime.executor.call, action_observation_completed)
        )
        result = await self.execute("publish_gap(failure)\nraise failure")
        self.assertTrue(result.get("isError"), result)
        text = "\n".join(block["text"] for block in result["content"] if block["type"] == "text")
        self.assertIn("Effect: partial", text)
        self.assertIn("Related observation [unavailable] for Original field", text)
        self.assertNotIn("Action [dispatched]", text)
        self.assertNotIn("Action [verified]", text)
        self.assertIn("Only some input may have arrived", text)

    async def test_final_observation_render_failure_preserves_later_completed_inputs(self):
        observation = {
            "source": Record(), "scope": "fixture", "view": "window",
            "state": Record(text="Captured before the next input"), "refs": {}, "full": False,
        }
        self.runtime.task_namespace["capture"] = partial(
            self.runtime.executor.call, observation_completed, observation,
        )
        with patch.object(self.runtime.presenter, "text", side_effect=ValueError("Observation rendering failed")):
            result = await self.execute("emit_receipt(receipts[0])\ncapture()\nemit_receipt(receipts[1])")
        self.assertTrue(result["isError"], result)
        text = "\n".join(block["text"] for block in result["content"] if block["type"] == "text")
        self.assertEqual(re.findall(r"Action \[dispatched\]: #(\d+) ", text), ["1", "2"])
        self.assertIn("Observation rendering failed", text)
