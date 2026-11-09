# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Execution admission and cleanup work without version-specific task APIs."""

import asyncio

import pytest

from computer_use_mcp.session import ExecutionSession


class Writer:
    def __init__(self, session):
        self.session = session
        self.closed = False
        self.sent = []
        self.draining = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()

    def write(self, value):
        self.sent.append(value)

    async def drain(self):
        self.draining.set()
        await self.release.wait()
        self.session._finish(dict(content=[dict(type="text", text="executed")]))

    def close(self):
        self.closed = True


class Session(ExecutionSession):
    def __init__(self):
        super().__init__()
        self.startup = asyncio.Event()
        self.writer = Writer(self)
        self.starts = 0

    async def _start(self):
        self.starts += 1
        await self.startup.wait()
        self.writer.closed = False
        self._writer = self.writer


def test_unawaited_execution_reserves_startup_and_rejects_overlap():
    async def scenario():
        session = Session()
        first = session.execute("first")
        assert session._starting
        second = session.execute("second")
        assert second.done()
        assert "Another call" in (await second)["content"][0]["text"]
        session.startup.set()
        assert not (await first).get("isError")
        assert session.starts == 1
        await session.close()

    asyncio.run(scenario())


def test_startup_completion_cannot_admit_another_execution_before_dispatch():
    async def scenario():
        session = Session()
        first = session.execute("first")
        second = []
        session._start_task.add_done_callback(lambda _: second.append(session.execute("second")))
        await asyncio.sleep(0)
        session.startup.set()
        assert not (await first).get("isError")
        assert "Another call" in (await second[0])["content"][0]["text"]
        assert session.starts == 1
        await session.close()

    asyncio.run(scenario())


def test_reset_before_first_task_step_prevents_dispatch():
    async def scenario():
        session = Session()
        execution = session.execute("must not run")
        resetting = session.reset()
        session.startup.set()
        assert not (await resetting).get("isError")
        assert (await execution)["isError"]
        assert not session.writer.sent
        assert not session._starting
        await session.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["before_start", "startup", "drain"])
def test_cancellation_retires_startup_and_closes_transport(phase):
    async def scenario():
        session = Session()
        if phase == "drain":
            session.writer.release.clear()
            session.startup.set()
        execution = session.execute("sample")
        if phase == "startup":
            await asyncio.sleep(0)
        elif phase == "drain":
            await session.writer.draining.wait()
        execution.cancel()
        session.startup.set()
        with pytest.raises(asyncio.CancelledError):
            await execution
        await session.close()
        assert session.writer.closed
        assert session._start_task is None
        assert not session._starting
        if phase != "drain":
            assert not session.writer.sent

    asyncio.run(scenario())


def test_execution_requested_during_reset_waits_for_cleanup():
    async def scenario():
        session = Session()
        first = session.execute("cancelled")
        resetting = session.reset()
        next_execution = session.execute("next")
        session.startup.set()
        assert not (await resetting).get("isError")
        assert (await first)["isError"]
        assert not (await next_execution).get("isError")
        assert session.starts == 2
        await session.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("state", ["closed", "cancelled", "retiring"])
def test_rejected_execution_does_not_start_a_worker(state):
    async def scenario():
        session = Session()
        signal = asyncio.Event()
        if state == "closed":
            session._closed = True
        elif state == "cancelled":
            signal.set()
        else:
            session._retiring = asyncio.get_running_loop().create_future()
        assert (await session.execute("must not run", signal=signal))["isError"]
        assert session.starts == 0

    asyncio.run(scenario())


def test_execute_can_be_passed_to_asyncio_run():
    session = ExecutionSession()
    session._closed = True
    assert asyncio.run(session.execute("must not run"))["isError"]
