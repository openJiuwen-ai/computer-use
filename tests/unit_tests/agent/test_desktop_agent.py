# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio
import json

from openjiuwen.core.runner import Runner
from openjiuwen.harness import create_deep_agent

from computer_use_agent import AgentConfig, DesktopTaskTool, create_agent

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
    server = model_server(
        [tool_call(setup), tool_call("print('tree diff'); capture('first window', 'green')"), answer("Observed")]
    )
    events = []

    async def run():
        async with create_agent(
            server.model(),
            config=AgentConfig(workspace=str(tmp_path)),
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
        async with create_agent(server.model(), config=AgentConfig(workspace=str(tmp_path))) as agent:
            await agent.invoke({"query": "Write to the test window."})
            assert agent.toolkit.progress["failure"]["receipt"]["status"] == "dispatched"

    asyncio.run(run())
    assert "Observe the affected state before retrying" in json.dumps(server.requests[1]["messages"])


def _delegate(task_description, **params):
    reply = tool_call("")
    reply["tool_calls"][0]["function"] = {
        "name": "desktop_task",
        "arguments": json.dumps({"task_description": task_description, **params}),
    }
    return reply


def _parent_tool_reply(server):
    return next(
        json.loads(message["content"])
        for message in reversed(server.requests[-1]["messages"])
        if message["role"] == "tool"
    )


def test_desktop_delegation_timeout_retains_write_progress_and_resumes(model_server, tmp_path):
    state = tmp_path / "desktop-state"
    write = rf"""
from pathlib import Path
from time import sleep
from computer_use.session.events import action_completed
state = Path({str(state)!r})
with state.open('a') as handle:
    handle.write('first\n')
action_completed(dict(status='dispatched', target='Test document', intent='write', detail='First write sent'))
sleep(60)
"""
    continue_task = rf"""
from pathlib import Path
assert 'state' not in globals()
fresh_state = Path({str(state)!r})
assert fresh_state.read_text() == 'first\n'
with fresh_state.open('a') as handle:
    handle.write('second\n')
print('Read back:', fresh_state.read_text())
"""
    child_server = model_server([tool_call(write), tool_call(continue_task), answer("Both writes observed.")])
    parent_server = model_server([_delegate("Write both lines to the test document."), answer("It timed out.")])
    tool = DesktopTaskTool(
        child_server.model(),
        config=AgentConfig(workspace=str(tmp_path / "child")),
        timeout_s=8,
    )
    parent = create_deep_agent(
        parent_server.model(),
        tools=[tool],
        workspace=str(tmp_path / "parent"),
        prompt_mode="minimal",
        enable_sys_operation=False,
        enable_read_image_multimodal=False,
        parallel_tool_calls=False,
    )

    async def run():
        conversation_id = "desktop-delegation-test"
        try:
            await parent.invoke({"query": "Delegate the writes.", "conversation_id": conversation_id})
            first = _parent_tool_reply(parent_server)
            context = first["resume_context"]
            assert context["status"] == "timeout"
            assert "cleanup_error" not in context
            assert context["progress"]["completion"] == "unknown"
            assert context["progress"]["receipts"][0]["detail"] == "First write sent"
            assert state.read_text() == "first\n"

            parent_server.replies.extend(
                [
                    _delegate("Write the remaining second line.", resume_task_id=first["resume_task_id"]),
                    answer("The child observed the result."),
                ]
            )
            await parent.invoke({"query": "Resume the remaining work.", "conversation_id": conversation_id})
            resumed = _parent_tool_reply(parent_server)
            assert resumed["resume_task_id"] == first["resume_task_id"]
            assert resumed["resume_context"]["status"] == "returned"
            assert resumed["resume_context"]["progress"]["receipts"][0]["detail"] == "First write sent"
            assert "Both writes observed." in resumed["output"]
        finally:
            Runner.resource_mgr.remove_tool(tool.card.id)

    asyncio.run(run())
    assert state.read_text() == "first\nsecond\n"
    resume_messages = json.dumps(child_server.requests[1]["messages"])
    assert "First write sent" in resume_messages and "Remaining task" in resume_messages
