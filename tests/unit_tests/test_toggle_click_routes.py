# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unconfirmed toggle AXPress equivalence cannot displace complete click activation."""

from dataclasses import replace

import pytest
from computer_use.errors import ComputerUseError
from computer_use.interaction.write.engine import Engine
from computer_use.interaction.write.routes.policy import Attempts, choose
from computer_use.native.contracts import (
    ClickContinuation,
    ClickReceipt,
    ClickTransportEvidence,
    Dispatch,
    HostPolicy,
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
@pytest.mark.parametrize("preparation", [(), ("background",), ("foreground",)])
async def test_default_toggle_click_sends_one_process_pointer_before_ready_hid(role, preparation):
    unconfirmed = click_route("object_action", "insufficientEvidence")
    directed = click_route("surface_pointer", "preparationRequired" if preparation else "ready", preparation)
    hid = click_route("desktop_pointer")
    receipt = ClickReceipt(
        (),
        ClickTransportEvidence(call_entered=True, call_returned=True),
        ClickContinuation("stop", TARGET, directed.click.route_family),
    )
    port = RecordingPort(
        [facts(unconfirmed, hid, directed, native_role=role)],
        [StepResult(TARGET, Dispatch.DISPATCHED, "one complete click", click=receipt)],
    )
    assert (await Engine(port).execute(CLICK, TARGET))["status"] == "dispatched"
    assert len(port.commands) == 1
    command = port.commands[0]
    assert command.primitive == directed
    assert not command.prepare and command.foreground == (preparation == ("foreground",))
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
async def test_unavailable_mouse_does_not_fall_back_to_unconfirmed_toggle_press(role):
    routes = [click_route("object_action", "insufficientEvidence")]
    routes.extend(click_route(delivery, "unavailable") for delivery in ("surface_pointer", "desktop_pointer"))
    port = RecordingPort([facts(*routes, native_role=role, enabled=Knowledge.YES)], [])
    with pytest.raises(ComputerUseError):
        await Engine(port).execute(CLICK, TARGET)
    assert port.commands == []


@pytest.mark.parametrize("role", ["AXCheckBox", "AXRadioButton"])
@pytest.mark.parametrize("authorized", [False, True])
def test_toggle_global_click_is_explicit_and_respects_host_permission(role, authorized):
    unconfirmed = click_route("object_action", "insufficientEvidence")
    directed = click_route("surface_pointer", "unavailable")
    hid = click_route("desktop_pointer")
    current = facts(unconfirmed, directed, hid, native_role=role)
    with pytest.raises(ComputerUseError) as scoped:
        choose(current, CLICK, HostPolicy(allow_global_input=authorized), Attempts())
    assert scoped.value.code == "input_unavailable"
    explicit = replace(CLICK, global_input=True)
    with pytest.raises(ComputerUseError) as failure:
        choose(current, explicit, HostPolicy(allow_global_input=authorized), Attempts())
    assert failure.value.code == "input_unavailable"


@pytest.mark.anyio
async def test_ordinary_button_keeps_its_ready_object_action():
    action = click_route("object_action")
    directed = click_route("surface_pointer")
    receipt = ClickReceipt(
        (),
        ClickTransportEvidence(call_entered=True, call_returned=True),
        ClickContinuation("stop", TARGET, action.click.route_family),
    )
    port = RecordingPort(
        [facts(directed, action, native_role="AXButton")],
        [StepResult(TARGET, Dispatch.DISPATCHED, "one native activation", click=receipt)],
    )
    assert (await Engine(port).execute(CLICK, TARGET))["status"] == "dispatched"
    assert len(port.commands) == 1 and port.commands[0].primitive == action
