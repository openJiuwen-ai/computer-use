# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Resolve only the editor evidence required by the actually selected contract."""

from dataclasses import replace

import pytest
from computer_use.errors import ComputerUseError
from computer_use.interaction.write.engine import Engine
from computer_use.interaction.write.model import (
    AttemptView,
    BoundWrite,
    OperationSpec,
    ResolutionInput,
    ResolutionMemory,
)
from computer_use.interaction.write.resolution.pipeline import TargetResolutionPipeline
from computer_use.interaction.write.routes.policy import choose, route_key
from computer_use.native.contracts import Actuator, Intent, UnitCommand
from unit_tests.test_confirmed_text_receiver_evidence import TARGET, TextEvidencePort


@pytest.fixture
def anyio_backend():
    return "asyncio"


REPLACE = Intent("replace", {"text": "replacement"})


def value_port(*, foreground=False, key_state=None):
    port = TextEvidencePort(role="scalar")
    port.value_route = port.opaque_child = True
    port.foreground, port.key_state = foreground, key_state
    port.focused = "unrelated-field"
    port.capabilities = (*port.capabilities, "nativeFocusMembers", "nativeVirtualFocus", "nativeFocusScopes")
    return port


def request(history=AttemptView()):
    return ResolutionInput(
        BoundWrite(OperationSpec(REPLACE, TARGET, (), TextEvidencePort.policy), "operation", lambda: 1000),
        history,
        ResolutionMemory(),
    )


@pytest.mark.anyio
@pytest.mark.parametrize("foreground,key_state", [(False, None), (True, None), (False, "preparable")])
async def test_selected_ready_whole_value_replacement_does_not_read_an_unrelated_editor_tree(foreground, key_state):
    port = value_port(foreground=foreground, key_state=key_state)
    result = await Engine(port).execute(REPLACE, TARGET)
    assert result["status"] == "dispatched" and port.contents == "replacement"
    assert port.queries == []
    assert len(port.commands) == 1 and port.commands[0].primitive.unit == "native_value_replace"
    assert port.commands[0].target == TARGET and not port.commands[0].prepare and not port.commands[0].foreground
    assert port.finished == ["operation"]


@pytest.mark.anyio
@pytest.mark.parametrize("foreground", [False, True])
@pytest.mark.parametrize("key_state", ["ready", "preparable"])
async def test_resolution_and_route_selection_agree_when_the_contextual_route_is_consumed(foreground, key_state):
    port = value_port(foreground=foreground, key_state=key_state)
    facts = await port.inspect("operation", TARGET, 1000)
    key = next(primitive for primitive in facts.primitives if primitive.unit == "key_sequence")
    history = AttemptView(unavailable=frozenset({route_key(UnitCommand(key, TARGET, REPLACE))}))
    resolved = await TargetResolutionPipeline(port).arun(request(history))
    command = choose(resolved.target.facts, REPLACE, port.policy, history)
    assert command.primitive.unit == "native_value_replace" and not command.prepare and not command.foreground
    assert port.queries == [] and not resolved.target.needs_editing_evidence


@pytest.mark.anyio
@pytest.mark.parametrize(
    "foreground,key_state,prepare", [(False, "ready", False), (True, "ready", False), (True, "preparable", True)]
)
async def test_unproven_contextual_preference_does_not_erase_ready_original_value(foreground, key_state, prepare):
    port = value_port(foreground=foreground, key_state=key_state)
    facts = await port.inspect("operation", TARGET, 1000)
    command = choose(facts, REPLACE, port.policy, AttemptView())
    assert command.primitive.unit == "key_sequence" and command.prepare is prepare
    result = await Engine(port).execute(REPLACE, TARGET)
    assert result["status"] == "dispatched" and port.contents == "replacement"
    assert port.queries == [TARGET.node, "unrelated-field", "opaque-child"]
    assert len(port.commands) == 1 and port.commands[0].primitive.unit == "native_value_replace"
    assert not port.commands[0].prepare and not port.commands[0].foreground
    assert len(port.inspections) >= 3


@pytest.mark.anyio
async def test_consumed_value_contract_does_not_bypass_contextual_resolution():
    port = value_port(key_state="ready")
    facts = await port.inspect("operation", TARGET, 1000)
    value = next(primitive for primitive in facts.primitives if primitive.unit == "native_value_replace")
    history = AttemptView(unavailable=frozenset({route_key(UnitCommand(value, TARGET, REPLACE))}))
    with pytest.raises(ComputerUseError):
        await TargetResolutionPipeline(port).arun(request(history))
    assert port.queries == [TARGET.node, "unrelated-field", "opaque-child"] and port.commands == []


@pytest.mark.anyio
async def test_ready_original_value_is_not_limited_by_other_preparation_recipients():
    port = value_port(foreground=True, key_state="preparable")
    history = AttemptView(preparation_pairs=frozenset({(Actuator("old1"), None), (Actuator("old2"), None)}))
    resolved = await TargetResolutionPipeline(port).arun(request(history))
    command = choose(resolved.target.facts, REPLACE, port.policy, history)
    assert command.primitive.unit == "native_value_replace" and not command.prepare
    assert port.queries == []


@pytest.mark.anyio
@pytest.mark.parametrize("boundary", ["identity", "scope", "modal", "actuator"])
async def test_ready_value_route_does_not_bypass_target_or_selected_relation_boundaries(boundary):
    port = value_port()
    if boundary == "identity":
        port.identity = type(port.identity).NO
    elif boundary == "scope":
        port.within_scope = type(port.within_scope).NO
    elif boundary == "modal":
        port.blocked_by = ("modal",)
    else:
        port.actuator = Actuator("related-editor")
    with pytest.raises(ComputerUseError):
        await Engine(port).execute(REPLACE, TARGET)
    assert port.commands == [] and port.contents == "original"


@pytest.mark.anyio
async def test_unready_value_contract_still_requires_its_missing_conditions():
    port = value_port()
    inspect = port.inspect

    async def not_ready(*args, **kwargs):
        facts = await inspect(*args, **kwargs)
        return replace(
            facts,
            primitives=tuple(
                replace(p, readiness=type(p.readiness).NO, pending=("enabled",)) for p in facts.primitives
            ),
        )

    port.inspect = not_ready
    with pytest.raises(ComputerUseError):
        await Engine(port).execute(REPLACE, TARGET)
    assert port.commands == [] and port.contents == "original"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "foreground,key_state,prepares", [(False, "ready", 0), (True, "ready", 0), (True, "preparable", 1)]
)
async def test_proven_contextual_preference_keeps_ordinary_input_callbacks(foreground, key_state, prepares):
    port = value_port(foreground=foreground, key_state=key_state)
    port.opaque_child = False
    port.focused = TARGET.node
    result = await Engine(port).execute(REPLACE, TARGET)
    assert result["status"] == "dispatched" and port.contents == "replacement"
    assert len(port.commands) == prepares + 1
    assert all(command.primitive.unit == "key_sequence" for command in port.commands)
    assert sum(command.prepare for command in port.commands) == prepares


@pytest.mark.anyio
@pytest.mark.parametrize("boundary", ["identity", "scope", "modal", "value-contract"])
async def test_fallback_refreshes_and_rechecks_the_original_value_contract(boundary):
    port = value_port(foreground=True, key_state="preparable")
    inspect = port.inspect

    async def changed(*args, **kwargs):
        if "opaque-child" in port.queries:
            if boundary == "identity":
                port.identity = type(port.identity).NO
            elif boundary == "scope":
                port.within_scope = type(port.within_scope).NO
            elif boundary == "modal":
                port.blocked_by = ("modal",)
            else:
                port.value_route = False
        return await inspect(*args, **kwargs)

    port.inspect = changed
    with pytest.raises(ComputerUseError):
        await Engine(port).execute(REPLACE, TARGET)
    assert port.commands == [] and port.contents == "original"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "code,effect",
    [
        ("stale_target", "none"),
        ("blocked_by_modal", "none"),
        ("input_unavailable", "none"),
        ("execution_timeout", "none"),
        ("observation_incomplete", "possible"),
    ],
)
async def test_contextual_fallback_does_not_swallow_boundary_or_uncertain_errors(code, effect):
    port = value_port(foreground=True, key_state="preparable")
    nodes = port.query_nodes

    async def failed(operation, target, *args):
        if target.node == "opaque-child":
            raise ComputerUseError(
                dict(code=code, effect=effect, target="original field", detail="provider error", next="observe")
            )
        return await nodes(operation, target, *args)

    port.query_nodes = failed
    with pytest.raises(ComputerUseError) as caught:
        await Engine(port).execute(REPLACE, TARGET)
    assert caught.value.code == code and caught.value.effect == effect
    assert port.commands == [] and port.contents == "original"
