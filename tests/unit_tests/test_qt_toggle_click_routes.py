# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""State-only Qt AXPress offers cannot displace complete mouse activation."""

from dataclasses import replace

import pytest
from computer_use.errors import ComputerUseError
from computer_use.interaction.write.engine import Engine
from computer_use.native.contracts import (
    ClickContinuation,
    ClickReceipt,
    ClickTransportEvidence,
    Dispatch,
    Intent,
    Knowledge,
    StepResult,
)
from unit_tests.test_text_input_routes import RecordingPort
from unit_tests.test_unified_write_routing import CLICK, TARGET, click_route, facts


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
@pytest.mark.parametrize("role", ["AXCheckBox", "AXRadioButton"])
@pytest.mark.parametrize("preparation", [(), ("background",)])
async def test_default_toggle_click_sends_one_process_pointer_before_ready_hid(role, preparation):
    state_only = click_route("object_action", "unavailable")
    directed = click_route("surface_pointer", "preparationRequired" if preparation else "ready", preparation)
    hid = click_route("desktop_pointer")
    receipt = ClickReceipt(
        (),
        ClickTransportEvidence(call_entered=True, call_returned=True),
        ClickContinuation("stop", TARGET, directed.click.route_family),
    )
    port = RecordingPort(
        [facts(state_only, hid, directed, native_role=role)],
        [StepResult(TARGET, Dispatch.DISPATCHED, "one complete click", click=receipt)],
    )
    assert (await Engine(port).execute(CLICK, TARGET))["status"] == "dispatched"
    assert len(port.commands) == 1
    command = port.commands[0]
    assert command.primitive == directed
    assert not command.prepare and not command.foreground
    assert port.finished == ["operation"]


@pytest.mark.anyio
@pytest.mark.parametrize("role", ["AXCheckBox", "AXRadioButton"])
async def test_explicit_toggle_axpress_keeps_one_native_action(role):
    # Named actions are sampled independently of default click offers. Their
    # caller explicitly requested this native command rather than mouse input.
    action = replace(click_route("object_action"), click=None)
    port = RecordingPort(
        [facts(action, native_role=role)],
        [StepResult(TARGET, Dispatch.DISPATCHED, "one explicit native action")],
    )
    assert (await Engine(port).execute(Intent("action", {"action": "AXPress"}), TARGET))["status"] == "dispatched"
    assert len(port.commands) == 1 and port.commands[0].primitive == action
    assert not port.commands[0].foreground


@pytest.mark.anyio
@pytest.mark.parametrize("role", ["AXCheckBox", "AXRadioButton"])
async def test_unavailable_mouse_does_not_fall_back_to_state_only_toggle(role):
    routes = [
        click_route(delivery, "unavailable") for delivery in ("object_action", "surface_pointer", "desktop_pointer")
    ]
    port = RecordingPort([facts(*routes, native_role=role, enabled=Knowledge.YES)], [])
    with pytest.raises(ComputerUseError):
        await Engine(port).execute(CLICK, TARGET)
    assert port.commands == []
