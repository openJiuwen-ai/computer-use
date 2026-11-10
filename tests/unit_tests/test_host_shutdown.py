# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Host shutdown must retain the loop until native retirement completes."""

import asyncio
from types import SimpleNamespace

import pytest

from computer_use.client.execution import SessionExecutor, execution_scope
from computer_use.client.host import computer_host
from computer_use_mcp.image_presentation import PresentationImages
from computer_use_mcp.output import ObservationPresenter
from computer_use_mcp.runtime import Runtime


@pytest.mark.parametrize("kind", ["sdk_host", "mcp_runtime"])
def test_close_waits_for_retirement_before_returning(kind):
    async def scenario():
        release, waiting = asyncio.Event(), asyncio.Event()
        order = []

        class Transport:
            def __init__(self, name):
                self.name, self.closed = name, False

            def close(self):
                order.append(("close", self.name))

            async def wait_closed(self):
                order.append(("wait", self.name))
                waiting.set()
                await release.wait()
                self.closed = True

        transports = [Transport("first"), Transport("second")] if kind == "mcp_runtime" else [Transport("first")]
        executor = SessionExecutor(loop=asyncio.get_running_loop())
        if kind == "sdk_host":
            with execution_scope(executor):
                owner = computer_host()
            owner.computer._controller.transport = transports[0]
            closing = asyncio.create_task(asyncio.to_thread(owner.close))
        else:
            # Exercise lifecycle ownership without installing execution callbacks.
            owner = Runtime.__new__(Runtime)
            owner.executions = set()
            owner.executor = executor
            owner.presenter = ObservationPresenter()
            owner.presentation_images = PresentationImages()
            for transport in transports:
                executor.register(SimpleNamespace(transport=transport))
            closing = asyncio.create_task(owner.close())
        try:
            await asyncio.wait_for(waiting.wait(), timeout=2)
            assert not closing.done(), "Host returned while native processes were still retiring"
        finally:
            release.set()
            await closing
        assert all(t.closed for t in transports)
        for transport in transports:
            assert [event for event, name in order if name == transport.name] == ["close", "wait"]

    asyncio.run(scenario())
