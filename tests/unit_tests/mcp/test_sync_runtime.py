# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Synchronous scripts retain owner-loop output and cancellation boundaries."""

import asyncio
import threading
import unittest

from computer_use.client.host import (
    set_action_handler,
    set_diagnostic_handler,
    set_execution_guard,
    set_observation_handler,
    set_preview_handler,
    set_text_page_handler,
)
from computer_use.session import images as images_state
from computer_use.session.operations import operation
from computer_use_mcp.runtime import Runtime


class Probe:
    def __init__(self, runtime):
        self.runtime = runtime
        self.started = asyncio.Event()
        self.cleaned = asyncio.Event()
        self.cleanup_release = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = []

    def perform(self, name):
        return self.runtime.executor.run(lambda: operation(lambda: self._perform(name)))

    async def _perform(self, name):
        self.calls.append((name, threading.get_ident()))
        if name == "wait":
            self.started.set()
            try:
                await self.release.wait()
            finally:
                await self.cleanup_release.wait()
                self.cleaned.set()
        else:
            self.runtime.action({"target": name, "status": "dispatched", "detail": "Sent once."})
        return name


class SyncRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.previous_delivery = images_state._delivery_required
        self.owner = threading.get_ident()
        self.sent = []
        self.output_threads = []

        def send(event):
            self.sent.append(event)
            self.output_threads.append(threading.get_ident())

        self.runtime = Runtime(send)
        self.probe = Probe(self.runtime)
        self.runtime.task_namespace["probe"] = self.probe

    async def asyncTearDown(self):
        self.probe.cleanup_release.set()
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
            {
                "code": code,
                "executionId": 1,
                "history": {"resetText": False, "resetImages": False, "incremental": False},
            }
        )

    async def test_sync_statements_wait_for_each_operation_and_emit_on_owner(self):
        result = await self.execute(
            "first = probe.perform('first')\nsecond = probe.perform('second')\nprint(first, second)"
        )
        self.assertFalse(result.get("isError"), result)
        self.assertEqual(self.probe.calls, [("first", self.owner), ("second", self.owner)])
        self.assertTrue(all(thread == self.owner for thread in self.output_threads))
        text = "\n".join(block["text"] for block in result["content"] if block["type"] == "text")
        self.assertIn("first second", text)
        self.assertEqual(text.count("Action [dispatched]"), 2)

    async def test_old_top_level_await_is_a_compile_error_before_any_action(self):
        result = await self.execute("probe.perform('before')\nawait probe.perform('after')")
        self.assertTrue(result.get("isError"), result)
        self.assertEqual(self.probe.calls, [])
        text = "\n".join(block["text"] for block in result["content"])
        self.assertIn("SyntaxError", text)
        self.assertIn("no statements from this call ran", text)

    async def test_cancellation_settles_cleanup_before_return_and_never_runs_suffix(self):
        active = asyncio.create_task(
            self.execute("probe.perform('before')\nprobe.perform('wait')\nprobe.perform('after')")
        )
        await asyncio.wait_for(self.probe.started.wait(), 2)
        active.cancel()
        await asyncio.sleep(0)
        self.assertFalse(active.done(), "Cancellation abandoned native cleanup")
        # A second cancellation must not cut through the pending cleanup.
        active.cancel()
        await asyncio.sleep(0)
        self.assertFalse(active.done())
        self.probe.cleanup_release.set()
        result = await asyncio.wait_for(active, 2)
        self.assertTrue(result.get("isError"), result)
        self.assertTrue(self.probe.cleaned.is_set())
        self.assertEqual([name for name, _ in self.probe.calls], ["before", "wait"])
        text = "\n".join(block["text"] for block in result["content"] if block["type"] == "text")
        self.assertEqual(text.count("Action [dispatched]"), 1)
        self.assertIn("Earlier statements may have executed", text)

    async def test_cancellation_interrupts_a_python_loop_and_runtime_close_settles_it(self):
        active = asyncio.create_task(self.execute("print('loop started')\nwhile True:\n    pass"))
        async with asyncio.timeout(2):
            while not any("loop started" in str(item) for item in self.sent):
                await asyncio.sleep(0.001)
        await asyncio.wait_for(self.runtime.close(), 2)
        self.assertTrue(active.done())
        result = await active
        self.assertTrue(result.get("isError"), result)
        self.assertEqual(self.probe.calls, [])
        denied = await self.execute("probe.perform('after close')")
        self.assertTrue(denied.get("isError"), denied)
        self.assertEqual(self.probe.calls, [])
