# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Only the native App-coordinate menu plugin may offer Press for a pixel click."""

from dataclasses import replace

import pytest
from computer_use.errors import ComputerUseError
from computer_use.interaction.write.engine import Engine
from computer_use.interaction.write.routes.policy import Attempts, choose
from computer_use.interaction.write.routes.selection.candidates import enumerate_candidates
from computer_use.native.contracts import (
    Binding,
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
from unit_tests.test_unified_write_routing import TARGET, click_route, facts

CAPTURE = Binding("session", "app", "root", "surface", capture="image")
POINT = Intent("click", {"x": 120, "y": 240, "button": "left", "count": 1})
POINTER = click_route("surface_pointer")
_ACTION = click_route("object_action")
MENU_PRESS = replace(_ACTION, click=replace(_ACTION.click, route_family="macos.coordinateMenuPress.v1"))


def observed(*routes, **changes):
    return facts(*routes, binding=CAPTURE, native_role=None, actions_complete=False, **changes)


def test_native_menu_plugin_can_select_press_without_rebinding_the_capture():
    selected = choose(observed(POINTER, MENU_PRESS), POINT, HostPolicy(), Attempts())
    assert selected.primitive == MENU_PRESS
    assert selected.target == CAPTURE
    assert selected.intent == POINT
    assert not selected.foreground and not selected.prepare


@pytest.mark.parametrize("route", [None, _ACTION, replace(MENU_PRESS, action="AXShowMenu")])
def test_absent_or_unqualified_menu_offer_keeps_original_pointer(route):
    routes = (POINTER,) if route is None else (route, POINTER)
    selected = choose(observed(*routes), POINT, HostPolicy(), Attempts())
    assert selected.primitive == POINTER
    assert selected.target == CAPTURE and selected.intent == POINT


@pytest.mark.parametrize(
    "parameters",
    [
        {"x": 120, "y": 240, "button": "right"},
        {"x": 120, "y": 240, "button": "middle"},
        {"x": 120, "y": 240, "count": 2},
        {"x": 120, "y": 240, "pointerOnly": True},
        {"x": 120, "y": 240, "modifiers": ["shift"]},
        {"x": 120, "y": 240, "holdDuration": 0.5},
    ],
)
def test_mouse_specific_requests_cannot_consume_the_plugin_offer(parameters):
    intent = Intent("click", parameters)
    selected = choose(observed(MENU_PRESS, POINTER), intent, HostPolicy(), Attempts())
    assert selected.primitive == POINTER
    assert selected.intent == intent


@pytest.mark.parametrize("platform", ["windows", "linux", None])
def test_other_platforms_do_not_use_the_macos_coordinate_plugin(platform):
    selected = choose(observed(MENU_PRESS, POINTER, platform=platform), POINT, HostPolicy(), Attempts())
    assert selected.primitive == POINTER


@pytest.mark.parametrize("binding", [TARGET, Binding("session", None, None, None, capture="desktop")])
def test_element_and_desktop_bindings_do_not_use_the_app_coordinate_plugin(binding):
    current = replace(observed(MENU_PRESS, POINTER), binding=binding)
    assert enumerate_candidates(current, POINT, HostPolicy()) == (POINTER,)


def test_global_intent_does_not_use_the_coordinate_plugin():
    explicit = replace(POINT, global_input=True)
    assert choose(observed(MENU_PRESS, POINTER), explicit, HostPolicy(), Attempts()).primitive == POINTER


@pytest.mark.parametrize("admission", ["unavailable", "insufficientEvidence"])
def test_ineligible_plugin_offer_does_not_block_independent_mouse(admission):
    rejected = replace(
        MENU_PRESS,
        supported=Knowledge.NO,
        click=replace(MENU_PRESS.click, admission=admission),
    )
    assert choose(observed(rejected, POINTER), POINT, HostPolicy(), Attempts()).primitive == POINTER


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
@pytest.mark.parametrize("dispatch", [Dispatch.DISPATCHED, Dispatch.PARTIAL, Dispatch.UNKNOWN])
async def test_press_effect_never_replays_as_a_mouse_click(dispatch):
    receipt = ClickReceipt(
        (),
        ClickTransportEvidence(kind="nativeAction", call_entered=True, call_returned=True),
        ClickContinuation("stop", CAPTURE, MENU_PRESS.click.route_family),
    )
    port = RecordingPort(
        [observed(MENU_PRESS, POINTER)],
        [StepResult(CAPTURE, dispatch, "menu action entered", click=receipt)],
    )
    operation = Engine(port).execute(POINT, CAPTURE)
    if dispatch == Dispatch.DISPATCHED:
        assert (await operation)["status"] == "dispatched"
    else:
        with pytest.raises(ComputerUseError):
            await operation
    assert len(port.commands) == 1
    assert port.commands[0].primitive == MENU_PRESS
    assert port.commands[0].target == CAPTURE and port.commands[0].intent == POINT
    assert port.inspections == 1 and port.finished == ["operation"]


@pytest.mark.anyio
async def test_menu_press_rejected_before_dispatch_refreshes_once_into_original_pointer():
    rejected = ClickReceipt(
        (),
        ClickTransportEvidence(),
        ClickContinuation("reselectWithinScope", CAPTURE, MENU_PRESS.click.route_family),
    )
    completed = ClickReceipt(
        (),
        ClickTransportEvidence(kind="posted", call_entered=True, call_returned=True),
        ClickContinuation("stop", CAPTURE, POINTER.click.route_family),
    )
    # Native matching publishes Press alone. A proven no-effect rejection marks
    # this operation's plugin attempt exhausted; its next sample is the original
    # pointer plan, not the old Press offer or an invented public element target.
    port = RecordingPort(
        [observed(MENU_PRESS), observed(POINTER)],
        [
            StepResult(
                CAPTURE,
                Dispatch.NOT_DISPATCHED,
                "menu item no longer qualifies; no native action entered",
                error_code="input_unavailable",
                retry_reason="preconditionChanged",
                click=rejected,
            ),
            StepResult(CAPTURE, Dispatch.DISPATCHED, "original coordinate mouse sent once", click=completed),
        ],
    )

    assert (await Engine(port).execute(POINT, CAPTURE))["status"] == "dispatched"
    assert [command.primitive for command in port.commands] == [MENU_PRESS, POINTER]
    assert all(command.target == CAPTURE and command.intent == POINT for command in port.commands)
    assert all(not command.foreground and not command.prepare for command in port.commands)
    assert port.inspections == 2  # One initial query and one bounded internal refresh.
    assert port.finished == ["operation"]
    assert not rejected.transport_evidence.call_entered
    assert completed.transport_evidence.call_entered
