# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio
import json

from computer_use_agent import AgentConfig, create_agent

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
            reset = await agent.toolkit.reset()
            assert reset.success
            assert reset.data["execution_progress"]["failure"]["receipt"]["status"] == "dispatched"

    asyncio.run(run())
    assert "Observe the affected state before retrying" in json.dumps(server.requests[1]["messages"])
