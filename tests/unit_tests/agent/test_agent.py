# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio
import json

from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent import create_agent_session
from openjiuwen.harness import DeepAgent
from openjiuwen.harness.factory import create_deep_agent

from computer_use_agent import AgentConfig, ComputerUseToolkit, create_agent

from .conftest import answer, tool_call


def test_native_agent_multi_turn_and_compaction(model_server, tmp_path):
    server = model_server(
        [
            tool_call("counter = 41; print(counter)"),
            answer("Initialized 41"),
            tool_call("counter += 1; print(counter)"),
            answer("Result 42"),
        ]
    )
    events = []

    async def run():
        async with create_agent(
            server.model(),
            config=AgentConfig(workspace=str(tmp_path)),
            on_event=lambda kind, data: events.append((kind, data)),
        ) as agent:
            assert isinstance(agent, DeepAgent)
            session = create_agent_session(card=agent.card)
            await agent.invoke({"query": "Initialize the counter"}, session=session)
            session = create_agent_session(session_id=session.get_session_id(), card=agent.card)
            chunks = [chunk async for chunk in agent.stream({"query": "Increment it"}, session=session)]
            assert any(chunk.type == "llm_output" for chunk in chunks)
            compacted = await agent.compact(session)
            assert (compacted.get("result") if isinstance(compacted, dict) else compacted) in ("compressed", "noop")
            result = await agent.toolkit.tools[0].invoke({"code": "print(counter)"})
            assert "42" in result.data["content"]
            return [tool.card.id for tool in agent.toolkit.tools]

    ids = asyncio.run(run())
    assert not server.replies
    assert any("Initialize the counter" in json.dumps(request) for request in server.requests[2:])
    assert (
        sum(
            kind == "tool_call" and data["arguments"].get("code") == "counter += 1; print(counter)"
            for kind, data in events
        )
        == 1
    )
    assert all(Runner.resource_mgr.get_tool(identifier) is None for identifier in ids)


def test_standard_toolkit_delivers_images_to_model(model_server, tmp_path):
    code = (
        "from PIL import Image; import io; "
        "buffer = io.BytesIO(); Image.new('RGB', (8, 8), 'red').save(buffer, format='PNG'); "
        "print('image evidence'); print_image(buffer.getvalue())"
    )
    server = model_server([tool_call(code), answer("Observed image")])

    async def run():
        async with ComputerUseToolkit() as toolkit:
            agent = create_deep_agent(
                server.model(),
                tools=toolkit.tools,
                system_prompt=toolkit.system_prompt,
                workspace=str(tmp_path),
                enable_sys_operation=False,
                enable_task_loop=False,
                parallel_tool_calls=False,
                enable_tool_resilience_rail=False,
                enable_read_image_multimodal=True,
                enable_model_anomaly_detection_rail=False,
            )
            await Runner.run_agent(agent, {"query": "Produce and inspect a red image"})
            for tool in toolkit.tools:
                Runner.resource_mgr.remove_tool(tool.card.id)

    asyncio.run(run())
    follow_up = server.requests[1]["messages"]
    assert "image evidence" in json.dumps(follow_up)
    assert any(
        isinstance(message.get("content"), list)
        and any(
            block.get("type") == "image_url" and block["image_url"]["url"].startswith("data:image/png;base64,")
            for block in message["content"]
        )
        for message in follow_up
    )


def test_cancellation_resets_python_without_replay():
    events = []

    async def run():
        async with ComputerUseToolkit(on_event=lambda kind, data: events.append((kind, data))) as toolkit:
            call = asyncio.create_task(
                toolkit.tools[0].invoke({"code": "marker = 'before'; await __import__('asyncio').sleep(60)"})
            )
            while toolkit.connection.session is None or toolkit.connection._active is None:
                await asyncio.sleep(0.01)
            await asyncio.sleep(0.2)
            call.cancel()
            await asyncio.gather(call, return_exceptions=True)
            result = await toolkit.tools[0].invoke({"code": "print('marker' in globals())"})
            assert "False" in result.data["content"]

    asyncio.run(run())
    assert sum(kind == "tool_call" and "marker =" in data["arguments"].get("code", "") for kind, data in events) == 1
    assert any(kind == "tool_cancelled" for kind, _ in events)


def test_partial_output_and_error_status_are_retained():
    async def run():
        async with ComputerUseToolkit() as toolkit:
            result = await toolkit.tools[0].invoke(
                {"code": "print('before failure'); raise ValueError('expected failure')"}
            )
            assert result.success is False
            assert "before failure" in result.data["content"]
            assert "expected failure" in result.data["content"]
            assert result.data["mcp_result"]["isError"] is True

    asyncio.run(run())


def test_toolkit_close_is_idempotent_and_sessions_are_isolated():
    async def run():
        first, second = ComputerUseToolkit(), ComputerUseToolkit()
        try:
            await first.tools[0].invoke({"code": "value = 11"})
            await second.tools[0].invoke({"code": "value = 22"})
            await first.close()
            await first.close()
            result = await second.tools[0].invoke({"code": "print(value)"})
            assert "22" in result.data["content"]
        finally:
            await first.close()
            await second.close()

    asyncio.run(run())
