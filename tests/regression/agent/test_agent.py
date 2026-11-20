# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio

from computer_use_mcp.server import ComputerUseServer
from mcp import types

from computer_use_agent import ComputerUseToolkit


def test_application_menu_guidance_reaches_mcp_and_reference_agent():
    async def list_tools():
        server = ComputerUseServer(allow_global_input=False)
        handler = server.server.request_handlers[types.ListToolsRequest]
        response = await handler(types.ListToolsRequest(method="tools/list"))
        assert server.session is None
        return response.model_dump(mode="json")["tools"]

    tools = asyncio.run(list_tools())
    description = next(tool["description"] for tool in tools if tool["name"] == "computer_use")
    assert ComputerUseToolkit().tools[0].card.description == description
    assert "app.read(menu_path=['Edit'], window=ref)" in description
    assert "exact visible menu titles" in description
    assert "app.element(item_ref).click() on the same App" in description
    assert "a shortcut displayed for that enabled item" in description
    assert "app.press_key(shown_shortcut, window=ref)" in description


def test_cancellation_resets_python_without_replay(tmp_path):
    events = []
    started = tmp_path / "started"

    async def run():
        async with ComputerUseToolkit(on_event=lambda kind, data: events.append((kind, data))) as toolkit:
            call = asyncio.create_task(
                toolkit.tools[0].invoke(
                    {
                        "code": f"from pathlib import Path; marker = 'before'; "
                        f"Path({str(started)!r}).write_text(marker)\n"
                        "import time\nwhile True:\n    time.sleep(0.01)"
                    }
                )
            )
            async with asyncio.timeout(30):
                while not started.exists():
                    await asyncio.sleep(0.01)
            call.cancel()
            await asyncio.gather(call, return_exceptions=True)
            result = await toolkit.tools[0].invoke({"code": "print('marker' in globals())"})
            assert "False" in result.data["content"]

    asyncio.run(run())
    assert sum(kind == "tool_call" and "marker =" in data["arguments"].get("code", "") for kind, data in events) == 1
    assert any(kind == "tool_cancelled" for kind, _ in events)


def test_connection_shutdown_error_preserves_cause_without_reconnect(monkeypatch):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    import pytest

    from computer_use_agent import McpConfig
    from computer_use_agent.connection import McpConnection

    failure = OSError("Transport shutdown failed")
    connections = []

    async def initialize():
        return None

    @asynccontextmanager
    async def transport(*args, **kwargs):
        connections.append(args)
        yield (None, None)
        raise failure

    @asynccontextmanager
    async def session(*streams):
        yield SimpleNamespace(initialize=initialize)

    monkeypatch.setattr("computer_use_agent.connection.stdio_client", transport)
    monkeypatch.setattr("computer_use_agent.connection.ClientSession", session)

    async def run():
        connection = McpConnection(McpConfig(command="unused"))
        await connection.start()
        with pytest.raises(RuntimeError, match="Computer Use MCP connection ended") as raised:
            await connection.close()
        assert raised.value.__cause__ is failure

    asyncio.run(run())
    assert len(connections) == 1
