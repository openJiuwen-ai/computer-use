# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Qualification fallback keeps preparation and requested input in separate states."""

import asyncio
from dataclasses import replace

import pytest
from computer_use.errors import ComputerUseError
from computer_use.interaction.write.engine import Engine
from computer_use.interaction.write.model import AttemptView
from computer_use.interaction.write.routes.policy import choose
from computer_use.native.contracts import Channel, Dispatch, Knowledge, Primitive, StepResult
from unit_tests.test_ready_object_replacement_resolution import REPLACE, TARGET, value_port


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_ready_value_after_qualification_failure_precedes_an_unproved_preparation_unit():
    port = value_port(foreground=True, key_state="preparable")
    inspect = port.inspect

    async def with_preparation(*args, **kwargs):
        facts = await inspect(*args, **kwargs)
        prepare = Primitive(
            "prepare",
            "prepare_receiver",
            Channel.EDITOR,
            Knowledge.YES,
            Knowledge.NO,
            ("receiver",),
            Knowledge.YES,
        )
        return replace(facts, primitives=(*facts.primitives, prepare))

    port.inspect = with_preparation
    initial = await port.inspect("operation", TARGET, 1000)
    assert choose(initial, REPLACE, port.policy, AttemptView()).prepare
    result = await Engine(port).execute(REPLACE, TARGET)
    assert result["status"] == "dispatched" and port.contents == "replacement"
    assert len(port.commands) == 1 and port.commands[0].primitive.unit == "native_value_replace"
    assert not port.commands[0].prepare and not port.commands[0].foreground


@pytest.mark.anyio
async def test_editor_query_cancellation_does_not_start_value_fallback():
    port = value_port(foreground=True, key_state="preparable")
    query = port.query_nodes

    async def cancelled(operation, target, *args):
        if target.node == "opaque-child":
            raise asyncio.CancelledError
        return await query(operation, target, *args)

    port.query_nodes = cancelled
    with pytest.raises(asyncio.CancelledError):
        await Engine(port).execute(REPLACE, TARGET)
    assert port.commands == [] and port.contents == "original"


@pytest.mark.anyio
@pytest.mark.parametrize("dispatch,effect", [(Dispatch.PARTIAL, "partial"), (Dispatch.UNKNOWN, "possible")])
async def test_uncertain_value_fallback_is_not_replayed_as_contextual_input(dispatch, effect):
    port = value_port(foreground=True, key_state="preparable")

    async def partial(operation, step, command, timeout_ms):
        port.commands.append(command)
        port.contents = "partial input"
        return StepResult(command.target, dispatch, "input effect not complete", error_code="execution_unknown")

    port.execute = partial
    with pytest.raises(ComputerUseError) as caught:
        await Engine(port).execute(REPLACE, TARGET)
    assert caught.value.effect == effect
    assert len(port.commands) == 1 and port.commands[0].primitive.unit == "native_value_replace"
    assert port.contents == "partial input"


@pytest.mark.anyio
async def test_editor_query_protocol_error_is_not_reinterpreted_as_missing_qualification():
    port = value_port(foreground=True, key_state="preparable")
    query = port.query_nodes

    async def invalid_scope(operation, target, *args):
        result = await query(operation, target, *args)
        if target.node == "opaque-child":
            result["session"] = "other-session"
        return result

    port.query_nodes = invalid_scope
    with pytest.raises(ComputerUseError) as caught:
        await Engine(port).execute(REPLACE, TARGET)
    assert caught.value.code == "execution_failed"
    assert port.commands == [] and port.contents == "original"
