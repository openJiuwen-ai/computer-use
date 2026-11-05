# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""One SDK operation clock survives nested/native work and preserves effects."""

import asyncio
import threading
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import patch

from computer_use.client.execution import ExecutionCancellation, SessionExecutor, execution_scope
from computer_use.identity import FrozenRecord, Record
from computer_use.interaction.controller import SessionController
from computer_use.native.lifecycle import CallLifecycle
from computer_use.session.observation_data import retain_observation_data
from computer_use.session.operations import (
    OperationBudget,
    OperationDeadlineExceeded,
    cancellation_scope,
    current_operation_budget,
    operation_budget,
)


class Clock:
    now = 0.0

    def __call__(self):
        return self.now


class OperationBudgetTests(TestCase):
    def test_default_operation_keeps_working_past_latency_target_and_two_seconds(self):
        clock = Clock()
        budget = OperationBudget(clock=clock)
        self.assertIsNone(budget.timeout_ms)
        self.assertIsNone(budget.deadline)
        self.assertEqual(budget.target_ms, 1000)
        for elapsed in (1.1, 2.1, 30.1):
            clock.now = elapsed
            self.assertTrue(budget.target_exceeded)
            self.assertFalse(budget.expired)
            self.assertEqual(budget.target_available_ms(), 0)
            self.assertIsNone(budget.remaining())
            budget.checkpoint()

    def test_single_absolute_deadline_and_original_stop_predicate(self):
        clock, stop = Clock(), threading.Event()
        budget = OperationBudget(1000, clock=clock, cancelled=stop.is_set)
        self.assertEqual(budget.remaining(), 1000)
        clock.now = 0.4
        self.assertEqual(budget.remaining(), 600)
        clock.now = 1.1
        self.assertEqual(budget.available_ms(), 0)
        self.assertTrue(budget.expired)
        with self.assertRaises(OperationDeadlineExceeded):
            budget.checkpoint()
        stop.set()
        with self.assertRaises(asyncio.CancelledError):
            budget.available_ms()

    def test_synchronous_executor_carries_original_event_into_busy_python_work(self):
        executor, cancellation = SessionExecutor(), ExecutionCancellation()
        task_cancelling = []

        async def work():
            with operation_budget() as budget:
                cancellation.cancelled.set()
                task_cancelling.append(asyncio.current_task().cancelling())
                budget.checkpoint()

        try:
            with execution_scope(executor, cancellation), self.assertRaises(asyncio.CancelledError):
                executor.run(work)
            self.assertEqual(task_cancelling, [0])
        finally:
            executor.close()


class ControllerBudgetTests(IsolatedAsyncioTestCase):
    async def test_partial_collection_does_not_qualify_as_complete_under_the_deadline(self):
        controller = SessionController.__new__(SessionController)
        captured = Record(text="already captured")
        retain_observation_data(
            captured,
            (),
            (),
            partial=True,
            gaps=(SimpleNamespace(code="observation_timeout"),),
        )
        receipt = FrozenRecord(
            {
                "status": "dispatched",
                "observation": captured,
                "observation_status": "partial",
                "observation_error": FrozenRecord({"code": "observation_timeout"}),
            }
        )
        events = []

        async def run(request, control):
            return receipt

        controller.pipeline = SimpleNamespace(arun=run)
        with patch("computer_use.interaction.controller.diagnostic", events.append):
            result = await controller.run(object())
        self.assertIs(result, receipt)
        self.assertEqual(events[-1]["outcome"], "dispatched")
        self.assertEqual(events[-1]["observationStatus"], "partial")
        self.assertEqual(events[-1]["observationErrorCode"], "observation_timeout")
        self.assertEqual(events[-1]["observationGapCodes"], ("observation_timeout",))
        self.assertFalse(events[-1]["observationComplete"])
        self.assertFalse(events[-1]["overrun"])

    async def test_diagnostic_summary_failure_cannot_replace_a_completed_receipt(self):
        controller = SessionController.__new__(SessionController)
        receipt = FrozenRecord({"status": "dispatched"})
        events = []

        async def run(request, control):
            return receipt

        controller.pipeline = SimpleNamespace(arun=run)
        with (
            patch("computer_use.interaction.controller.diagnostic", events.append),
            patch(
                "computer_use.interaction.controller._observation_completion", side_effect=ValueError("bad metadata")
            ),
        ):
            result = await controller.run(object())
        self.assertIs(result, receipt)
        self.assertEqual(events[-1]["observationDiagnosticError"], "ValueError")

    async def test_nested_controller_work_shares_clock_and_late_receipt_remains_successful(self):
        controller = SessionController.__new__(SessionController)
        outer, inner = object(), object()
        clock = Clock()
        budget = OperationBudget(1000, clock=clock)
        seen, events = [], []
        receipt = FrozenRecord({"status": "dispatched", "detail": "input already sent"})

        async def run(request, control):
            seen.append((current_operation_budget(), control))
            if request is outer:
                await controller.run(inner)
                clock.now = 1.1
            return receipt

        controller.pipeline = SimpleNamespace(arun=run)
        with (
            patch("computer_use.session.operations.OperationBudget", return_value=budget),
            patch("computer_use.interaction.controller.diagnostic", events.append),
        ):
            result = await controller.run(outer)
        self.assertIs(result, receipt)
        self.assertIsNone(current_operation_budget())
        self.assertTrue(all(found is budget for found, _ in seen))
        self.assertTrue(all(control.call_id == budget.operation_id for _, control in seen))
        self.assertEqual([event["kind"] for event in events], ["sdk_operation_start", "sdk_operation_end"])
        self.assertEqual(events[-1]["outcome"], "dispatched")
        self.assertEqual(events[-1]["status"], "ok")
        self.assertTrue(events[-1]["overrun"])
        self.assertAlmostEqual(events[-1]["durationMs"], 1100)

    async def test_component_limits_cannot_renew_root_and_stop_without_loop_callback(self):
        calls = CallLifecycle(SimpleNamespace())
        stop = threading.Event()
        with cancellation_scope(stop.is_set), operation_budget(2000) as budget, calls.limit(5000):
            with calls.claim(30000) as remaining:
                self.assertLessEqual(remaining(), 2000)
                self.assertLessEqual(calls._limit[1], budget.deadline)
                self.assertEqual(calls.control(remaining).call_id, budget.operation_id)
                stop.set()
                with self.assertRaises(asyncio.CancelledError):
                    remaining()
            self.assertFalse(calls.busy)
        self.assertIsNone(current_operation_budget())

    async def test_default_operation_preserves_native_timeout_and_cancellation_after_two_seconds(self):
        calls = CallLifecycle(SimpleNamespace())
        clock, stop = Clock(), threading.Event()
        budget = OperationBudget(clock=clock, cancelled=stop.is_set)
        with (
            patch("computer_use.session.operations.OperationBudget", return_value=budget),
            patch("computer_use.native.lifecycle.time.monotonic", clock),
            operation_budget(),
            calls.limit(5000),
            calls.claim(30000) as remaining,
        ):
            clock.now = 2.1
            self.assertAlmostEqual(remaining(), 2900, delta=1)
            self.assertEqual(calls.control(remaining).call_id, budget.operation_id)
            stop.set()
            with self.assertRaises(asyncio.CancelledError):
                remaining()
            stop.clear()
            clock.now = 5.1
            with self.assertRaisesRegex(TimeoutError, "Native call budget exhausted"):
                remaining()
        self.assertFalse(calls.busy)

    async def test_expired_root_is_distinct_from_shorter_component_timeout(self):
        calls = CallLifecycle(SimpleNamespace())
        with operation_budget(1000) as budget, calls.claim(30000) as remaining:
            budget.deadline = budget.started - 1
            with self.assertRaises(OperationDeadlineExceeded):
                remaining()
