# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The resource snapshot and final loop local have different lifetimes."""

import asyncio
import sys
from types import SimpleNamespace

import pytest

from computer_use import Computer
from computer_use.client import computer, factory
from computer_use.client.execution import SessionExecutor, execution_scope


@pytest.mark.skipif(sys.implementation.name != "cpython", reason="observes CPython reference release")
def test_aclose_releases_earlier_resources_before_the_final_controller():
    async def scenario():
        executor = SessionExecutor(loop=asyncio.get_running_loop())
        released = []

        class Controller:
            def __init__(self, name):
                self.name = name
                self.transport = SimpleNamespace(close=lambda: None)

            def __del__(self):
                released.append((self.name, executor._closed, executor._closing))

        for name in ("first", "middle", "last"):
            executor.register(Controller(name))
        await executor.aclose()
        assert released == [("middle", False, True), ("first", False, True), ("last", True, False)]

    asyncio.run(scenario())


def test_closing_one_client_rejects_both_api_kinds_without_closing_shared_executor():
    async def scenario():
        executor = SessionExecutor(loop=asyncio.get_running_loop())

        def close_one_client():
            with execution_scope(executor), Computer() as first, Computer() as second:
                app = first.app("first")
                first.close()
                first.close()
                for operation in (lambda: first.app("first"), first.list_apps, app.read):
                    with pytest.raises(RuntimeError, match="^Computer session is closed$"):
                        operation()
                assert second.app("second") is second.app("second")

        try:
            await asyncio.to_thread(close_one_client)
        finally:
            await executor.aclose()

    asyncio.run(scenario())


def test_close_failure_disposes_under_original_exception_scope(monkeypatch):
    executor = SessionExecutor()
    retirement_error = RuntimeError("retirement")
    disposal_error = ValueError("disposal")

    def fail_retirement(_operation):
        raise retirement_error

    def fail_disposal():
        assert sys.exception() is retirement_error
        raise disposal_error

    monkeypatch.setattr(executor, "run", fail_retirement)
    monkeypatch.setattr(executor, "close_if_owned", fail_disposal)
    with pytest.raises(ValueError, match="disposal") as failure:
        executor.close_client(object())
    assert failure.value is disposal_error
    assert failure.value.__context__ is retirement_error


def test_constructor_failure_retires_owned_executor_before_propagating(monkeypatch):
    executor = SessionExecutor()
    startup_error = RuntimeError("controller creation")
    close = executor.close_if_owned
    events = []

    def fail_construction(*, allow_global_input):
        assert asyncio.get_running_loop() is executor.loop
        events.append(("factory", allow_global_input))
        raise startup_error

    def dispose():
        assert sys.exception() is startup_error
        events.append(("cleanup", executor._closed))
        close()

    monkeypatch.setattr(computer, "session_executor", lambda: executor)
    monkeypatch.setattr(factory, "session_controller", fail_construction)
    monkeypatch.setattr(executor, "close_if_owned", dispose)
    with pytest.raises(RuntimeError, match="controller creation") as failure:
        Computer(allow_global_input=False)
    assert failure.value is startup_error
    assert events == [("factory", False), ("cleanup", False)]
    assert executor._closed and not executor._thread.is_alive()
