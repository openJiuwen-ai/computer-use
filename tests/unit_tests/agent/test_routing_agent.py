# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Routing integration: route_task tool, GUI fallback metrics, and the agent loop."""

import asyncio
import json
import sys

import pytest

pytest.importorskip("openjiuwen")

from openjiuwen.core.single_agent import create_agent_session

from computer_use_agent import AgentConfig, ComputerUseToolkit, RoutingConfig, create_agent
from computer_use_agent.routing import GUI_FALLBACK_ID, OutcomeRecord, PathKind, TaskPath
from computer_use_agent.routing.paths import RiskClass, SdkSpec, VerifySpec

from .conftest import answer
from .test_routing import echo_path, make_cli


def _tool(name, toolkit):
    return next(tool for tool in toolkit.tools if tool.card.name == name)


def test_route_task_tool_executes_registered_cli_path(tmp_path):
    toolkit = ComputerUseToolkit(AgentConfig(workspace=str(tmp_path)))
    toolkit.routing.registry.register(echo_path())
    try:
        output = asyncio.run(_tool("route_task", toolkit).invoke({"task": "say hello"}))
        assert output.success
        assert "hello-route" in output.data["content"]
        assert output.data["route"]["chosen"] == "test.echo"
        snapshot = toolkit.routing.store.snapshot("test.echo", PathKind.CLI)
        assert snapshot.attempts == 1
        assert snapshot.successes == 1
    finally:
        asyncio.run(toolkit.close())


def test_computer_use_calls_feed_gui_fallback_metrics(tmp_path):
    toolkit = ComputerUseToolkit(AgentConfig(workspace=str(tmp_path)))
    try:
        asyncio.run(_tool("computer_use", toolkit).invoke({"title": "sample", "code": "print('sample-ok')"}))
        snapshot = toolkit.routing.store.snapshot(GUI_FALLBACK_ID, PathKind.GUI)
        assert snapshot.attempts == 1
        assert snapshot.successes == 1
    finally:
        asyncio.run(toolkit.close())


def test_routed_sdk_execution_is_not_counted_as_free_gui_sample(tmp_path):
    toolkit = ComputerUseToolkit(AgentConfig(workspace=str(tmp_path)))
    toolkit.routing.registry.register(
        TaskPath(
            id="sdk.hello",
            kind=PathKind.API,
            app="test",
            intent="say hello via sdk",
            risk=RiskClass.READ,
            sdk=SdkSpec(code_template="print('routed-sdk-ok')"),
            verify=VerifySpec(kind="output_contains", expect="routed-sdk-ok"),
        )
    )
    try:
        output = asyncio.run(_tool("route_task", toolkit).invoke({"task": "say hello via sdk"}))
        assert output.success
        assert "routed-sdk-ok" in output.data["content"]
        gui = toolkit.routing.store.snapshot(GUI_FALLBACK_ID, PathKind.GUI)
        assert gui.attempts == 0
        route = toolkit.routing.store.snapshot("sdk.hello", PathKind.API)
        assert route.attempts == 1
        assert route.successes == 1
    finally:
        asyncio.run(toolkit.close())


def test_routing_disabled_omits_route_tool_and_prompt():
    toolkit = ComputerUseToolkit(AgentConfig(routing=RoutingConfig(enabled=False)))
    assert all(tool.card.name != "route_task" for tool in toolkit.tools)
    assert toolkit.routing is None
    assert "route_task" not in toolkit.system_prompt
    enabled = ComputerUseToolkit(AgentConfig())
    try:
        assert any(tool.card.name == "route_task" for tool in enabled.tools)
        assert "route_task" in enabled.system_prompt
    finally:
        asyncio.run(enabled.close())


def test_agent_routes_task_end_to_end_without_mcp(model_server, tmp_path):
    route_call = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_route",
                "type": "function",
                "function": {
                    "name": "route_task",
                    "arguments": json.dumps({"task": "say hello"}),
                },
            }
        ],
    }
    server = model_server([route_call, answer("已完成：通过 CLI 路径输出 hello-route。")])
    events = []
    attempts = []

    async def run():
        async with create_agent(
            server.model(),
            config=AgentConfig(workspace=str(tmp_path)),
            on_event=lambda kind, data: events.append((kind, data)),
        ) as agent:
            agent.toolkit.routing.registry.register(echo_path())
            session = create_agent_session(card=agent.card)
            await agent.invoke({"query": "Say hello please"}, session=session)
            attempts.append(agent.toolkit.routing.store.snapshot("test.echo", PathKind.CLI).attempts)

    asyncio.run(run())
    assert not server.replies
    contents = [json.dumps(data, ensure_ascii=False) for kind, data in events if kind == "tool_result"]
    assert any("hello-route" in content for content in contents)
    assert attempts == [1]


def test_agent_refusal_is_reported_to_model(model_server, tmp_path):
    route_call = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_route",
                "type": "function",
                "function": {
                    "name": "route_task",
                    "arguments": json.dumps({"task": "delete everything"}),
                },
            }
        ],
    }
    server = model_server([route_call, answer("我没有可靠的路径，已向你说明原因。")])
    events = []

    async def run():
        async with create_agent(
            server.model(),
            config=AgentConfig(
                workspace=str(tmp_path),
                routing=RoutingConfig(min_confidence=0.99, refuse_floor=0.4, refuse_after_attempts=1),
            ),
            on_event=lambda kind, data: events.append((kind, data)),
        ) as agent:
            agent.toolkit.routing.registry.register(
                make_cli(
                    "rm.all",
                    (sys.executable, "-c", "print('gone')"),
                    intent="delete everything",
                    risk=RiskClass.DESTRUCTIVE,
                    verify_spec=VerifySpec(kind="output_contains", expect="gone"),
                )
            )
            for _ in range(2):
                agent.toolkit.routing.store.record(
                    OutcomeRecord(path_id=GUI_FALLBACK_ID, task="t", success=False), PathKind.GUI
                )
            session = create_agent_session(card=agent.card)
            await agent.invoke({"query": "Please delete everything"}, session=session)

    asyncio.run(run())
    assert not server.replies
    contents = [json.dumps(data, ensure_ascii=False) for kind, data in events if kind == "tool_result"]
    assert any("decision: refuse" in content for content in contents)
    assert all("gone" not in content for content in contents)  # destructive path never ran
