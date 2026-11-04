# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Read-only scroll-member qualification must precede pointer admission."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from computer_use.interaction.write.model import (
    AttemptView,
    BoundWrite,
    OperationSpec,
    ResolutionFrame,
    ResolutionInput,
    ResolutionMemory,
)
from computer_use.interaction.write.resolution.passes import refine_scroll
from computer_use.native.contracts import (
    Channel,
    HostPolicy,
    InputContext,
    Intent,
    Knowledge,
    Primitive,
    ScrollPointerActuator,
)
from unit_tests.test_text_input_routes import TARGET, observed


@pytest.fixture
def anyio_backend():
    return "asyncio"


def frame(supported):
    pointer = Primitive("pointer", "pointer_sequence", Channel.POINTER, supported, Knowledge.NO, pending=("receiver",))
    facts = observed(
        pointer,
        native_role="AXScrollArea",
        actuator=ScrollPointerActuator(TARGET.node, TARGET.node),
        input=InputContext(pointer_hit="hit", geometry_current=Knowledge.YES),
    )
    bound = BoundWrite(
        OperationSpec(Intent("scroll", {"direction": "down", "pages": 0.5}), TARGET, (), HostPolicy()),
        "operation",
        lambda: 123,
    )
    return ResolutionFrame(ResolutionInput(bound, AttemptView(), ResolutionMemory()), facts=facts)


@pytest.mark.anyio
@pytest.mark.parametrize("supported", [Knowledge.YES, Knowledge.NO])
@pytest.mark.parametrize("qualified", [True, False])
async def test_unqualified_hit_can_be_queried_without_authorizing_input(monkeypatch, supported, qualified):
    original = frame(supported)
    selected = ScrollPointerActuator("hit", TARGET.node) if qualified else None
    query = SimpleNamespace(resolve=AsyncMock(return_value=selected), basis=())
    monkeypatch.setattr(refine_scroll, "ScrollPointerQuery", lambda *_: query)
    port = SimpleNamespace(
        policy=HostPolicy(), inspect=AsyncMock(return_value=replace(original.facts, actuator=selected))
    )
    result = await refine_scroll.create(native=port)(original, None)
    query.resolve.assert_awaited_once_with("hit")
    if qualified:
        port.inspect.assert_awaited_once_with("operation", TARGET, 123, within=(), actuator=selected, basis=())
        assert result.facts.binding == TARGET and result.request.memory.scroll_member == "hit"
    else:
        port.inspect.assert_not_awaited()
        assert result is original


@pytest.mark.anyio
async def test_member_proof_cannot_rebind_original_scroll_target(monkeypatch):
    original = frame(Knowledge.NO)
    selected = ScrollPointerActuator("hit", TARGET.node)
    query = SimpleNamespace(resolve=AsyncMock(return_value=selected), basis=())
    monkeypatch.setattr(refine_scroll, "ScrollPointerQuery", lambda *_: query)
    changed = replace(original.facts, binding=replace(TARGET, node="other"))
    port = SimpleNamespace(policy=HostPolicy(), inspect=AsyncMock(return_value=changed))
    with pytest.raises(ValueError, match="changed the requested element"):
        await refine_scroll.create(native=port)(original, None)
