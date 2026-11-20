# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Behavior contracts at the public adaptation / execution boundary."""

import asyncio
import sys
from collections import OrderedDict
from dataclasses import replace
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch
from weakref import WeakSet, ref

from computer_use.client.factory import session_controller
from computer_use.diagnostics import local_failure
from computer_use.errors import ComputerUseError
from computer_use.interaction.agent.requests import ActOnElement, ResolveElement
from computer_use.interaction.query.pipeline import FindElementPipeline
from computer_use.native.contracts import Binding, Knowledge
from computer_use.native.lifecycle import CallLifecycle
from computer_use.pipeline import Contract, Control, Effect, Pass, Sequence
from computer_use.session.events import set_action_handler, set_observation_handler
from computer_use.session.interfaces import LookupContext, SurfaceOwner, WindowEvidence, WindowIdentity
from computer_use.session.state import AppState


class UIStub(SimpleNamespace):
    """Weak-referenceable handle for session registration."""


class PipelineRuntimeTests(IsolatedAsyncioTestCase):
    def test_control_emit_isolates_low_stack_at_the_native_diagnostic_boundary(self):
        control = Control(diagnostic=lambda _event: None)
        with patch("computer_use.pipeline.core.sdk_core", side_effect=RecursionError("low stack"), create=True):
            control.emit("enter", frozenset({Effect.PURE}))

    async def test_node_reports_the_active_child_error_and_keeps_its_context(self):
        for asynchronous in (False, True):
            with self.subTest(asynchronous=asynchronous):
                await self.assert_node_error_context(asynchronous)

    async def assert_node_error_context(self, asynchronous):
        original = ValueError("child failed")
        replacement = RuntimeError("observer failed")
        observations = []

        class ReplacingControl:
            def child(self, name):
                self.name = name
                return self

            def emit(self, phase, _effects, error=None):
                observations.append((self.name, phase, sys.exception(), error))
                if phase == "error":
                    raise replacement

        def fail(_value, _control):
            raise original

        stage = Pass("stage", fail, Contract(object, object))
        try:
            raise LookupError("caller was handling this")
        except LookupError as ambient:
            with self.assertRaises(RuntimeError) as caught:
                if asynchronous:
                    await stage.arun(None, ReplacingControl())
                else:
                    stage.run(None, ReplacingControl())
            self.assertIs(sys.exception(), ambient)
            self.assertIs(original.__context__, ambient)
            self.assertIs(caught.exception, replacement)
            self.assertIs(replacement.__context__, original)
            self.assertEqual(observations, [("stage", "enter", ambient, None), ("stage", "error", original, original)])

    async def test_sequence_keeps_the_child_in_the_original_task_until_cancelled(self):
        entered = asyncio.Event()
        release = asyncio.Event()
        events = []
        child = None

        async def hold():
            entered.set()
            try:
                await release.wait()
            finally:
                events.append(("cleanup", asyncio.current_task(), child() is not None))

        class SuspendedStep:
            def arun(self, _value, _control):
                nonlocal child
                child = ref(self)
                return hold()

        def transient_steps():
            yield SuspendedStep()
            raise AssertionError("a cancelled child must stop the sequence")

        def observe(event):
            events.append((event.phase, asyncio.current_task(), sys.exception()))

        sequence = Sequence("sequence")
        sequence.steps = transient_steps()
        task = asyncio.create_task(sequence.arun(None, Control(diagnostic=observe)))
        await asyncio.wait_for(entered.wait(), 2)
        self.assertIsNotNone(child())
        task.cancel("stop sequence")
        with self.assertRaises(asyncio.CancelledError) as cancelled:
            await task
        self.assertEqual(events, [("enter", task, None), ("cleanup", task, True), ("error", task, cancelled.exception)])


class InteractionBehaviorTests(IsolatedAsyncioTestCase):
    def setUp(self):
        port = SimpleNamespace(
            connection=SimpleNamespace(session="test", capabilities=(), session_identity=(1, "test")),
        )
        port.calls = CallLifecycle(port)
        self.controller = session_controller(port)
        self.target = Binding("test", "app", "window", "surface", "node")
        self.snapshot = {"binding": self.target}
        instance = SimpleNamespace(
            session=None,
            evidence={
                WindowIdentity("window", "surface", "app"): WindowEvidence(
                    WindowIdentity("window", "surface", "app"), Knowledge.YES
                )
            },
            anchor=None,
            delivered_lookup_windows=set(),
            delivered_observation_generation=0,
            lookup_observations=OrderedDict(),
            lookup_results=OrderedDict(),
            lookup_captures=WeakSet(),
            refs={"1": self.snapshot},
            owners={},
            members={"window": {"surface": "surface", "window": "window", "instance": "app"}},
            surface_evidence=lambda *_: None,
        )
        self.ui = UIStub(_instance=instance, _anchor=None, _menu_only=True, _lookup_scopes=self.lookup_scopes(1))
        instance.lookup_context = LookupContext(None, True, self.ui._lookup_scopes)
        self.controller.register(self.ui, {"selector": "test-app"})
        self.receipt = {"status": "dispatched", "target": "test element", "detail": "sent once"}
        self.engine = AsyncMock(return_value=self.receipt)
        self.controller.engine.execute = self.engine
        self.events = []
        set_action_handler(lambda value: self.events.append(("action", value)))
        set_observation_handler(lambda value: self.events.append((value["kind"], value)))

    def lookup_scopes(self, count):
        # Real lookup owners are registered scope handles even when they have
        # no captured query root. Keep that contract while mocking live lookup.
        return tuple(SurfaceOwner(self.controller, AppState("test-app")) for _ in range(count))

    def tearDown(self):
        set_action_handler(None)
        set_observation_handler(None)

    async def test_receipt_delivery_failure_does_not_replay_action(self):
        def fail(_):
            raise ValueError("delivery failed")

        set_action_handler(fail)
        with self.assertRaisesRegex(RuntimeError, "Action completed, but its receipt could not be displayed") as caught:
            await self.controller.run(ActOnElement(self.ui, self.snapshot, "click", {}))
        self.assertIn("Effect: test element: sent once", str(caught.exception))
        self.engine.assert_awaited_once()

    async def test_lookup_unique_missing_and_ambiguous(self):
        missing = local_failure("not found", "element_not_found")
        self.ui._lookup_scopes = self.lookup_scopes(2)
        self.ui._instance.lookup_context = LookupContext(None, True, self.ui._lookup_scopes)
        second = {"binding": replace(self.target, node="other")}
        for results, error in (
            ([missing, self.snapshot], None),
            ([missing, missing], "element_not_found"),
            ([self.snapshot, second], "ambiguous_target"),
        ):
            with (
                self.subTest(error=error),
                patch.object(FindElementPipeline, "arun", new_callable=AsyncMock) as lookup,
            ):
                lookup.side_effect = results
                if error:
                    with self.assertRaises(ComputerUseError) as caught:
                        await self.controller.run(ResolveElement(self.ui, "button", 0))
                    self.assertEqual(caught.exception.code, error)
                else:
                    self.assertIs(await self.controller.run(ResolveElement(self.ui, "button", 0)), self.snapshot)
                requests = [call.args[0] for call in lookup.call_args_list]
                self.assertEqual([request.owner for request in requests], list(self.ui._lookup_scopes))
                self.assertEqual([request.timeout_ms for request in requests], [0, 0])
                self.assertEqual(tuple(dict(segment) for segment in requests[0].path), ({"name": "button"},))
        self.engine.assert_not_awaited()

    async def test_incomplete_scope_cannot_claim_unique_match(self):
        self.ui._menu_only = False
        self.ui._instance.observed = set()
        self.ui._context = "unknown"
        self.ui._instance.directory = {"surfaces": []}
        self.ui._instance.lookup_context = LookupContext(
            WindowIdentity("window", "surface", "app"), False, self.ui._lookup_scopes
        )
        with (
            patch("computer_use.interaction.agent.pipeline.allowed", return_value=dict(self.ui._instance.members)),
            patch.object(FindElementPipeline, "arun", new_callable=AsyncMock, return_value=self.snapshot),
        ):
            with self.assertRaises(ComputerUseError) as caught:
                await self.controller.run(ResolveElement(self.ui, "button", 0))
        self.assertEqual(caught.exception.code, "observation_incomplete")
        self.engine.assert_not_awaited()

    async def test_write_boundary_limits_native_claims_and_releases_budget_after_failure(self):
        failure = local_failure("not dispatched", "unsupported_operation")

        async def execute(*_args, **_kwargs):
            with self.controller.port.calls.claim(120000) as remaining:
                self.assertGreater(remaining(), 0)
                self.assertLessEqual(remaining(), 30000)
            raise failure

        self.engine.side_effect = execute
        with self.assertRaises(ComputerUseError) as caught:
            await self.controller.run(ActOnElement(self.ui, self.snapshot, "click", {}))
        self.assertIs(caught.exception, failure)
        self.assertIsNone(self.controller.port.calls._limit)
        self.assertFalse(self.controller.port.calls.busy)
        self.engine.assert_awaited_once()
