# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Host shutdown must retain the loop until native retirement completes."""

import asyncio
from types import SimpleNamespace

import pytest

from computer_use.client.host import computer_host
from computer_use_mcp.runtime import Runtime


@pytest.mark.parametrize("kind", ["sdk_host", "mcp_runtime"])
def test_close_waits_for_retirement_before_returning(kind):
    async def scenario():
        release = asyncio.Event()
        order = []

        class Transport:
            def __init__(self, name):
                self.name, self.closed = name, False

            def close(self):
                order.append(("close", self.name))

            async def wait_closed(self):
                order.append(("wait", self.name))
                await release.wait()
                self.closed = True

        transports = [Transport("first"), Transport("second")] if kind == "mcp_runtime" else [Transport("first")]
        computers = [SimpleNamespace(_controller=SimpleNamespace(transport=t)) for t in transports]
        if kind == "sdk_host":
            owner = computer_host(lambda: computers[0])
        else:
            # Exercise lifecycle ownership without installing execution callbacks.
            owner = Runtime.__new__(Runtime)
            owner.computers = computers
        closing = asyncio.create_task(owner.close())
        try:
            await asyncio.sleep(0)
            assert not closing.done(), "Host returned while native processes were still retiring"
            assert order[: len(transports)] == [("close", t.name) for t in transports]
        finally:
            release.set()
            await closing
        assert all(t.closed for t in transports)

    asyncio.run(scenario())
