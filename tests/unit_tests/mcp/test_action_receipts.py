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
from computer_use.interaction.write.engine import Engine
from computer_use.native.contracts import Binding, HostPolicy, Intent
from computer_use.native.transport import ConnectionPort
from computer_use.session.events import action_observation_completed, observation_completed
from computer_use_mcp.runtime import Runtime
from unit_tests.test_input_context_timeout import SilentStageConnection


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
        self.assertIn("window=7 Another document", text)

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

    async def test_failure_distinguishes_complete_preparation_from_requested_input(self):
        preparation = {
            "stage": "preparation", "device": "element", "transport": "accessibility",
            "mechanism": "AXPress", "state": "invoked",
        }
        cleanup = {**preparation, "stage": "cleanup", "mechanism": "AXCancel"}
        actual = {"coverage": "complete", "omitted_effects": 0, "effects": (preparation, cleanup)}
        attempt = {
            "dispatch": "notDispatched", "cleanup": "complete", "actual": actual,
            "preparation": ("refresh selected menu branch", "close prepared menu"),
        }
        possible = {**preparation, "stage": "input", "state": "possible"}
        requested = "Requested input was not dispatched."
        for case, attempts, scope_cleanup, effect, distinguished in (
            ("confirmed", (attempt,), None, "possible", True),
            ("no attempts", (), None, "possible", False),
            ("previous input", (dict(attempt, dispatch="dispatched"), attempt), None, "possible", False),
            ("partial dispatch", (dict(attempt, dispatch="partial"),), None, "possible", False),
            ("unknown dispatch", (dict(attempt, dispatch="unknown"),), None, "possible", False),
            ("possible input", (dict(attempt, actual=dict(actual, effects=(preparation, possible))),),
             None, "possible", False),
            ("missing impact", (dict(attempt, actual=None),), None, "possible", False),
            ("partial coverage", (dict(attempt, actual=dict(actual, coverage="partial")),), None, "possible", False),
            ("omitted effects", (dict(attempt, actual=dict(actual, omitted_effects=1)),), None, "possible", False),
            ("unknown attempt cleanup", (dict(attempt, cleanup="unknown"),), None, "possible", False),
            ("unknown scope cleanup", (attempt,), "unknown", "possible", False),
            ("no preparation", (dict(attempt, actual=dict(actual, effects=())),), None, "possible", False),
            ("partial effect", (attempt,), None, "partial", False),
        ):
            with self.subTest(case=case):
                failure = ComputerUseError({
                    "code": "input_unavailable", "target": "New Terminal", "effect": effect,
                    "detail": "Native menu tracking requires the physical pointer.",
                    "impact": {"attempts": attempts},
                })
                failure.scope_cleanup = scope_cleanup
                self.runtime.task_namespace["failure"] = failure
                result = await self.execute("emit_receipt(receipts[0])\nraise failure")

                self.assertTrue(result["isError"], result)
                self.assertIn("Action [dispatched]: #1 Fixture.", result["content"][0]["text"])
                text = result["content"][-1]["text"]
                self.assertEqual(requested in text, distinguished)
                self.assertIn(f"Effect: {effect}", text)
                self.assertEqual("Effect: possible (preparation or cleanup)" in text, distinguished)
                self.assertEqual(failure.effect, effect)
                if distinguished:
                    self.assertLess(text.index(requested), text.index("Effect: possible"))
                    self.assertIn("Attempt 1: input=notDispatched; cleanup=complete.", text)
                    self.assertIn("Preparation: refresh selected menu branch; close prepared menu", text)

    async def test_closed_window_reports_proven_no_input_and_preserves_preparation(self):
        actual = {
            "coverage": "complete", "omitted_effects": 0,
            "effects": ({"stage": "preparation", "device": "focus", "transport": "activation", "state": "invoked"},),
            "foreground_changed": "yes", "foreground_restoration": "restored",
        }
        attempt = {
            "dispatch": "notDispatched", "cleanup": "complete", "actual": actual,
            "preparation": ("foreground window activation",),
        }
        possible_input = {"stage": "input", "device": "mouse", "transport": "process", "state": "possible"}
        observation = {
            "source": Record(), "scope": "fixture", "view": "window",
            "state": Record(text="Related window still available"), "refs": {}, "full": False,
        }
        self.runtime.task_namespace["capture"] = partial(self.runtime.executor.call, observation_completed, observation)

        for case, lifecycle, attempts, confirmed in (
            ("complete evidence", "closed", (attempt,), True),
            ("unknown window", "unknown", (attempt,), False),
            ("no attempts", "closed", (), False),
            ("unknown input", "closed", (dict(attempt, dispatch="unknown"),), False),
            ("earlier partial dispatch", "closed", (dict(attempt, dispatch="partial"), attempt), False),
            ("missing impact", "closed", (dict(attempt, actual=None),), False),
            ("incomplete impact", "closed", (dict(attempt, actual=dict(actual, coverage="partial")),), False),
            ("possible input", "closed", (dict(attempt, actual=dict(actual, effects=(possible_input,))),), False),
        ):
            with self.subTest(case=case):
                failure = ComputerUseError({
                    "code": "stale_target", "target": "Working window", "effect": "possible",
                    "detail": "Observed native window closed or its process changed",
                    "impact": {"attempts": attempts},
                })
                failure.observation_status = "available"
                failure.window_lifecycle = lifecycle
                self.runtime.task_namespace["failure"] = failure

                result = await self.execute("capture()\nraise failure")

                self.assertTrue(result["isError"], result)
                text = "\n".join(block["text"] for block in result["content"] if block["type"] == "text")
                self.assertIn("Related window still available", text)
                self.assertIn("Effect: possible", text)
                self.assertEqual("Requested input: not dispatched." in text, confirmed)
                self.assertEqual("Original window: closed." in text, confirmed)
                if confirmed:
                    self.assertIn("Preparation: foreground window activation", text)
                    self.assertIn("Foreground changed: yes", text)
                    self.assertIn("Foreground restoration: restored", text)

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

    async def test_lost_next_admission_preserves_completed_inputs_in_the_same_batch(self):
        connection = SilentStageConnection("lost_begin")
        port = ConnectionPort(connection, HostPolicy())
        target = Binding("session", "app", "root", "surface")
        with patch("computer_use.native.connection.requestCleanupGraceMs", 5), self.assertRaises(ComputerUseError) as e:
            await Engine(port).execute(Intent("key", {"key": "t"}), target)

        self.runtime.task_namespace["failure"] = e.exception
        result = await self.execute("emit_receipt(receipts[0])\nemit_receipt(receipts[1])\nraise failure")

        self.assertTrue(result["isError"], result)
        *previous, terminal = [block["text"] for block in result["content"]]
        self.assertEqual(re.findall(r"Action \[dispatched\]: #(\d+) ", "\n".join(previous)), ["1", "2"])
        self.assertIn("requested action was not dispatched", terminal)
        self.assertIn("Effect: none", terminal)
        self.assertIn("Scope cleanup: unknown.", terminal)
        self.assertNotIn("Action [", terminal)

    async def test_menu_hint_requires_delivery_of_the_same_observation(self):
        state = Record(text="Window 7\n8 menu item Apply")
        observation = {
            "source": Record(), "scope": "fixture", "view": "window",
            "state": state, "refs": {}, "full": False,
        }
        failure = ComputerUseError({
            "code": "execution_unknown", "target": "Window 7 / Element 3", "effect": "possible",
            "detail": "native action returned AX error -25204; effect unknown, not retried",
        })
        failure.observed_menu_window = 7
        self.runtime.task_namespace.update(
            failure=failure, capture=partial(self.runtime.executor.call, observation_completed, observation),
        )
        for delivered_state in (state, Record(text=state.text)):
            failure.observation = delivered_state
            result = await self.execute("capture()\nraise failure")
            self.assertTrue(result["isError"], result)
            text = "\n".join(block["text"] for block in result["content"])
            self.assertIn("8 menu item Apply", text)
            self.assertIn("AX error -25204", text)
            self.assertIn("Effect: possible", text)
            self.assertEqual("Use its menu elements in Window 7." in text, delivered_state is state)
