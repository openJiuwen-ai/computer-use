"""Per-call graphs release borrowed state on every exit path."""

import asyncio
import gc
import unittest
import weakref
from types import SimpleNamespace
from unittest.mock import patch

from computer_use.interaction.read.app_read.model import (
    ApplicationSelection,
    ReadCall,
    ReadRequest,
    _StartupReceipt,
)
from computer_use.interaction.read.app_read.pipeline import _LaunchDispatch
from computer_use.interaction.requests import ExecuteAction
from computer_use.interaction.write.call.model import EngineRequest
from computer_use.interaction.write.call.pipeline import EngineCallPipeline
from computer_use.interaction.write.request.model import ActionPlan
from computer_use.interaction.write.request.pipeline import ExecuteNativeWrite
from computer_use.native.contracts import Binding, Intent
from computer_use.pipeline import Control


class CallGraphLifetimeTests(unittest.IsolatedAsyncioTestCase):
    async def outcome(self, invoke, mode):
        if mode == "success":
            return await invoke()
        error = RuntimeError if mode == "error" else asyncio.CancelledError
        with self.assertRaises(error):
            await invoke()

    def write_pipeline(self, references):
        case = self

        class BorrowedState:
            pass

        class FakeWrite:
            def __init__(self, native):
                self.native = native
                self.state = BorrowedState()
                references.append(weakref.ref(self.state))
                self.intent = self.main = self

            async def run_in_call(self, *args, **options):
                case.assertEqual(self.native.parent.children, (self, self))
                self.native.dispatches += 1
                if self.native.mode == "error":
                    raise RuntimeError("fixture")
                if self.native.mode == "cancel":
                    raise asyncio.CancelledError()
                return dict(dispatch="dispatched", application=dict(id="running", displayName="fixture"))

        return FakeWrite

    async def test_engine_releases_per_call_graph_after_success_error_and_cancellation(self):
        references = []
        for mode in ("success", "error", "cancel"):
            with self.subTest(mode=mode):
                native = SimpleNamespace(mode=mode, dispatches=0)
                engine = EngineCallPipeline(native)
                native.parent = engine
                with patch(
                    "computer_use.interaction.write.call.pipeline.WritePipeline", self.write_pipeline(references)
                ):
                    await self.outcome(
                        lambda *, engine=engine: engine._borrow(EngineRequest(None, None), lambda: 100, Control()), mode
                    )
                self.assertEqual(native.dispatches, 1)
                self.assertEqual(engine.children, ())
                gc.collect()
                self.assertIsNone(references[-1]())

    async def test_startup_releases_per_call_graph_after_success_error_and_cancellation(self):
        references = []
        for mode in ("success", "error", "cancel"):
            with self.subTest(mode=mode):
                native = SimpleNamespace(mode=mode, dispatches=0)
                launch = _LaunchDispatch(SimpleNamespace(port=native))
                native.parent = launch
                selection = ApplicationSelection(
                    ReadCall(ReadRequest("fixture"), lambda: 100, _StartupReceipt()),
                    dict(id="installation"),
                    None,
                    applications=dict(session="s"),
                    installation="installation",
                )
                with patch("computer_use.interaction.write.pipeline.WritePipeline", self.write_pipeline(references)):
                    await self.outcome(lambda *, launch=launch, selection=selection: launch.arun(selection), mode)
                self.assertEqual(native.dispatches, 1)
                self.assertEqual(launch.children, ())
                gc.collect()
                self.assertIsNone(references[-1]())

    async def test_controller_write_releases_borrowed_engine_graph_on_all_exit_paths(self):
        case = self
        target = Binding("s", "instance", "window", "surface", "node")

        async def exercise(mode, borrowed):
            class FakeEngine:
                pipeline = object()

                async def execute(self, *args, **options):
                    case.assertEqual(node.children, (self.pipeline,))
                    calls.append(options["_control"])
                    if mode == "error":
                        raise RuntimeError("fixture")
                    if mode == "cancel":
                        raise asyncio.CancelledError()
                    return {}

                execute_in_call = execute

            calls = []
            controller = SimpleNamespace(engine=FakeEngine())
            node = ExecuteNativeWrite(controller, borrowed)
            plan = ActionPlan(ExecuteAction(None, Intent("click"), target), target=target, remaining=lambda: 100)
            await self.outcome(lambda: node.arun(plan), mode)
            self.assertEqual(len(calls), 1)
            self.assertEqual(node.children, ())

        for mode in ("success", "error", "cancel"):
            for borrowed in (False, True):
                with self.subTest(mode=mode, borrowed=borrowed):
                    await exercise(mode, borrowed)
