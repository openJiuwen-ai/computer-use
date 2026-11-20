# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Program execution keeps cleanup and the original failure under cancellation."""

import asyncio
import gc
import sys
import weakref
from threading import Event
from types import SimpleNamespace

import pytest
from computer_use.client.computer import computer_for_controller
from computer_use.client.execution import SessionExecutor, _cancellation, _executor, execution_scope
from computer_use.interaction.agent.requests import ActOnElement
from computer_use.programs.pipeline import ProgramContext, execute
from computer_use.session import operations
from program_fixtures import artifact_at

from unit_tests.test_program_runtime import Desktop
from regression.compatibility.test_public_sync_sdk import Controller


def test_program_context_releases_a_cycle_with_its_host_callback():
    async def capture():
        artifact = SimpleNamespace(definition={}, expectation=lambda _parameters: ("scope", "digest"))
        context = ProgramContext(artifact, {}, 10, None, None, None, None)
        context.emit = lambda _record: context
        return weakref.ref(context)

    reference = asyncio.run(capture())
    gc.collect()
    assert reference() is None

def test_program_event_records_sink_failure_in_its_exception_scope():
    async def run():
        seen = []
        failure = OSError("event sink failed")
        artifact = SimpleNamespace(definition={}, expectation=lambda _: ("scope", "digest"))

        def emit(_event):
            raise failure

        context = ProgramContext(artifact, {}, 10, emit, lambda: None, None, None)

        def remember(error):
            seen.append((error, sys.exception()))
            ProgramContext._remember(context, error)

        context._remember = remember
        with pytest.raises(OSError) as caught:
            context.event("test event")
        assert caught.value is failure is context.first_failure
        assert seen == [(failure, failure)]

    asyncio.run(run())

def test_program_worker_retires_after_repeated_cancellation(monkeypatch):
    async def run():
        loop = asyncio.get_running_loop()
        release = Event()
        ready = loop.create_future()
        waits = asyncio.Queue()
        contexts = []
        failures = []
        owner = object()
        wait = asyncio.wait

        async def observe_wait(tasks):
            waits.put_nowait(sys.exception())
            return await wait(tasks)

        monkeypatch.setattr(asyncio, "wait", observe_wait)

        def worker():
            cancellation = _cancellation.get()
            loop.call_soon_threadsafe(ready.set_result, (cancellation, _executor.get()))
            assert release.wait(5)
            failures.append("worker retired")
            raise OSError("failure while retiring the worker")

        async def invoke_step():
            artifact = SimpleNamespace(definition={}, expectation=lambda _: ("scope", "digest"))
            context = ProgramContext(artifact, {}, 10, lambda _: None, lambda: None, None, owner)
            contexts.append(context)
            return await context.step("worker", worker)

        task = asyncio.create_task(invoke_step())
        try:
            cancellation, executor = await asyncio.wait_for(ready, 2)
            assert executor is owner
            task.cancel("first")
            original = await asyncio.wait_for(waits.get(), 2)
            assert isinstance(original, asyncio.CancelledError)
            assert cancellation.cancelled.is_set()
            task.cancel("again")
            assert await asyncio.wait_for(waits.get(), 2) is original
            assert not task.done()
        finally:
            release.set()

        with pytest.raises(asyncio.CancelledError, match="first") as caught:
            await task
        assert caught.value is original is contexts[0].first_failure
        assert contexts[0].steps[0]["status"] == "error"
        assert contexts[0].steps[0]["error_type"] == "CancelledError"
        assert failures == ["worker retired"]

    asyncio.run(run())

def test_cancelled_sync_sdk_step_waits_for_native_cleanup(tmp_path, monkeypatch):
    artifact = artifact_at(
        tmp_path,
        source="""async def run(ctx, computer, p):
    editor = await ctx.step("bind", lambda: computer.app("Editor").element("Field"))
    await ctx.step("replace", lambda: editor.type_text(p["replacement"], replace=True), write=True)
    await ctx.step("save", lambda: editor.press_key("super+s"), write=True)
""",
    )
    monkeypatch.setattr(operations, "_guard", None)

    async def scenario():
        entered, retiring, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        actions = []

        class BlockingController(Controller):
            async def run(self, request):
                if not isinstance(request, ActOnElement):
                    return await super().run(request)
                actions.append(request.intent)
                entered.set()
                try:
                    await asyncio.Future()
                finally:
                    retiring.set()
                    await release.wait()
                    actions.append("retired")

        executor = SessionExecutor(loop=asyncio.get_running_loop())
        with execution_scope(executor):
            computer = computer_for_controller(BlockingController())
        task = asyncio.create_task(
            execute(
                artifact.path,
                computer,
                {"document": "fixture", "initial": "old", "replacement": "new"},
                Desktop().environment,
                emit=lambda _: None,
                checkpoint=lambda: None,
                development=True,
            )
        )
        try:
            await asyncio.wait_for(entered.wait(), timeout=2)
            task.cancel()
            await asyncio.wait_for(retiring.wait(), timeout=2)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
        finally:
            release.set()
            result = await task
            await executor.aclose()
        assert result.status == "cancelled"
        assert result.failure["effect"] == "unknown"
        assert actions == ["replace", "retired"]

    asyncio.run(scenario())
