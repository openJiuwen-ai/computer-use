# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from mcp.types import CallToolResult, TextContent
from openjiuwen.core.runner.callback import AbortError

from computer_use_agent import AgentConfig, ComputerUseToolkit, activity, create_agent

from .conftest import answer, tool_call


def _images(request):
    return [
        block
        for message in request["messages"]
        if isinstance(message["content"], list)
        for block in message["content"]
        if block.get("type") == "image_url"
    ]


def test_agent_elides_only_superseded_screenshots_between_tool_calls(model_server, tmp_path):
    setup = """
from io import BytesIO
from PIL import Image
from computer_use.client.images import register_image
from computer_use.protocol.png import PNG

def capture(scope, color):
    buffer = BytesIO()
    Image.new('RGB', (8, 8), color).save(buffer, format='PNG')
    png = PNG(buffer.getvalue())
    if scope:
        register_image(png, dict(kind='window', selector=scope, mappingAvailable=False,
                                 coordinateScope=('window', scope)))
    print_image(png)

print('tree baseline')
capture('first window', 'red')
capture('first window', 'yellow')
capture('second window', 'blue')
capture(None, 'black')
"""
    refresh = tool_call("print('tree diff'); capture('first window', 'green')")
    server = model_server([tool_call(setup), refresh, refresh, answer("Observed")])
    events = []

    async def run():
        async with create_agent(
            server.model(),
            config=AgentConfig(workspace=str(tmp_path), check_desktop=False),
            on_event=lambda kind, data: events.append((kind, data)),
        ) as agent:
            await agent.invoke({"query": "Compare these window captures."})

    asyncio.run(run())
    first, second = (_images(request) for request in server.requests[1:3])
    assert len(first) == 4
    assert second[:2] == first[2:]
    assert len(second) == 3 and second[-1] not in first
    final_request = json.dumps(server.requests[2]["messages"])
    assert "tree baseline" in final_request and "tree diff" in final_request
    assert "Screenshot omitted from history" in final_request
    previous = server.requests[2]["messages"]
    latest = server.requests[3]["messages"]
    assert latest[: len(previous)] == previous
    assert _images(server.requests[3]) == second
    assert "Screenshot unchanged from" in json.dumps(latest)
    first_result = next(data["result"] for kind, data in events if kind == "tool_result")
    raw_images = [block for block in first_result["content"] if block["type"] == "image"]
    assert len(raw_images) == 4
    first_scope, same_scope, other_scope = [
        block["_meta"]["computer-use"]["observationScope"] for block in raw_images[:3]
    ]
    assert first_scope == same_scope and first_scope != other_scope


def test_agent_reports_uncertain_sdk_effect_to_model(model_server, tmp_path):
    code = """
from computer_use.errors import ComputerUseError
error = ComputerUseError(dict(code='input_unavailable', target='Test window',
                              effect='possible', detail='The receiving control did not confirm input'))
error.action_receipt = dict(status='dispatched', target='Test window', detail='Input sent before observation failed')
raise error
"""
    server = model_server([tool_call(code), answer("I will inspect the window.")])

    async def run():
        config = AgentConfig(workspace=str(tmp_path), check_desktop=False)
        async with create_agent(server.model(), config=config) as agent:
            await agent.invoke({"query": "Write to the test window."})
            reset = await agent.toolkit.reset()
            assert reset.success
            assert reset.data["execution_progress"]["failure"]["receipt"]["status"] == "dispatched"

    asyncio.run(run())
    assert "Observe the affected state before retrying" in json.dumps(server.requests[1]["messages"])


def _mcp_text(*texts, is_error=False):
    return CallToolResult(isError=is_error, content=[TextContent(type="text", text=text) for text in texts])


def test_agent_keeps_distinct_tree_baselines_and_diffs(model_server, tmp_path):
    def tree(window, mode, body):
        return (
            f"Window {window}, App: \"Notes\" (window scope); computer.app('instance:notes').read(window={window}).\n"
            f"Tree: {mode}\n{body}"
        )

    original = tree(0, "full", "1 text field: Initial title")
    delta = tree(0, "diff", "~1 text field: Edited title")
    separate = tree(0, "full", "1 text field: Other baseline title")
    other = tree(1, "full", "2 text field: Other window title")
    scripts = ["initial", "edit", "read_other_handle", "read_initial_again"]
    server = model_server([*(tool_call(code) for code in scripts), answer("Observed")])
    events = []

    async def run():
        config = AgentConfig(workspace=str(tmp_path), check_desktop=False)
        async with create_agent(
            server.model(), config=config, on_event=lambda kind, data: events.append((kind, data))
        ) as agent:
            agent.toolkit.connection.call = AsyncMock(
                side_effect=[
                    _mcp_text(original, other, "Printed evidence"),
                    _mcp_text("Action #1 verified", delta),
                    _mcp_text(separate),
                    _mcp_text(original),
                ]
            )
            await agent.invoke({"query": "Inspect the two windows and update the title."})

    asyncio.run(run())
    after_new_tree, after_duplicate = (json.dumps(request["messages"]) for request in server.requests[3:5])
    assert "Initial title" in after_new_tree and "Other baseline title" in after_new_tree
    assert after_duplicate.count("Initial title") == 1
    assert "An identical full tree is retained" in after_duplicate
    for evidence in ("Other baseline title", "Edited title", "Other window title", "Printed evidence", "Action #1"):
        assert evidence in after_duplicate
    assert next(data for kind, data in events if kind == "tool_result")["result"]["content"][0]["text"] == original


def test_agent_guides_repeated_no_effect_failure_without_blocking_calls(model_server, tmp_path):
    failure = """
from computer_use.errors import ComputerUseError
raise ComputerUseError(dict(code='input_unavailable', target='Test window', effect='none', detail='Not ready'))
"""
    scripts = [failure, "# different script\n" + failure, "print('Observed')", failure, failure, failure]
    server = model_server([*(tool_call(code) for code in scripts), answer("I will change the approach.")])
    events = []

    async def run():
        config = AgentConfig(workspace=str(tmp_path), check_desktop=False)
        async with create_agent(
            server.model(), config=config, on_event=lambda kind, data: events.append((kind, data))
        ) as agent:
            await agent.invoke({"query": "Try the operation."})

    asyncio.run(run())
    advisory = "returned the same no-effect failure 3 times"
    assert advisory not in json.dumps(server.requests[4]["messages"])
    assert advisory in json.dumps(server.requests[5]["messages"])
    assert sum(kind == "tool_result" and data["name"] == "computer_use" for kind, data in events) == len(scripts)


@pytest.mark.parametrize(("available", "streaming"), [(True, False), (False, False), (False, True)])
def test_agent_checks_desktop_before_first_model_request(model_server, tmp_path, available, streaming):
    server = model_server([answer("Ready"), answer("Still ready")] if available else [])

    async def run():
        async def check(name, arguments):
            assert name == "computer_use" and arguments["code"] == "len(computer.list_apps())"
            assert not server.requests
            return _mcp_text("3") if available else _mcp_text("Desktop session is unavailable.", is_error=True)

        async with create_agent(server.model(), config=AgentConfig(workspace=str(tmp_path))) as agent:
            probe = agent.toolkit.connection.call = AsyncMock(side_effect=check)
            if available:
                await agent.invoke({"query": "First turn", "conversation_id": "check-desktop"})
                await agent.invoke({"query": "Second turn", "conversation_id": "check-desktop"})
            else:
                with pytest.raises(AbortError, match="Desktop startup check failed.*Desktop session is unavailable"):
                    if streaming:
                        await anext(agent.stream({"query": "First turn"}))
                    else:
                        await agent.invoke({"query": "First turn"})
            probe.assert_awaited_once()

    asyncio.run(run())
    assert len(server.requests) == (2 if available else 0)


def test_pause_refreshes_after_user_activity_without_dispatching_stale_code(monkeypatch):
    samples = iter((0.2, 5.5, 6.0))
    monkeypatch.setattr("computer_use_agent.toolkit.build_idle_probe", lambda: lambda: next(samples))
    monkeypatch.setattr(activity, "_POLL_INTERVAL", 0.0)
    events = []

    async def run():
        async with ComputerUseToolkit(
            AgentConfig(pause_on_user_input=True), on_event=lambda kind, data: events.append((kind, data))
        ) as toolkit:
            connection = toolkit.connection.call = AsyncMock(return_value=_mcp_text("Current observation"))
            held = await toolkit.call("computer_use", {"code": "computer.click(at=(10, 10))"})
            assert not held.success and "This call was not sent" in held.data["content"]
            connection.assert_not_awaited()
            fresh = await toolkit.call("computer_use", {"code": "computer.read(view='tree')"})
            assert fresh.success
            connection.assert_awaited_once_with("computer_use", {"code": "computer.read(view='tree')"})

    asyncio.run(run())
    assert [kind for kind, _ in events if kind.startswith("desktop_")] == ["desktop_paused", "desktop_resumed"]


def test_pause_timeout_leaves_code_unsent(monkeypatch):
    monkeypatch.setattr("computer_use_agent.toolkit.build_idle_probe", lambda: lambda: 0.2)
    monkeypatch.setattr(activity, "_MAX_WAIT", 0.0)

    async def run():
        async with ComputerUseToolkit(AgentConfig(pause_on_user_input=True)) as toolkit:
            connection = toolkit.connection.call = AsyncMock()
            held = await toolkit.call("computer_use", {"code": "computer.click(at=(10, 10))"})
            assert not held.success and "The desktop is still busy" in held.data["content"]
            connection.assert_not_awaited()

    asyncio.run(run())
