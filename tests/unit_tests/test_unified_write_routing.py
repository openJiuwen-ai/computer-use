# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Mechanism regressions for addressed-first input with independent activation."""

from dataclasses import replace

import pytest
from computer_use.errors import ComputerUseError
from computer_use.interaction.write.engine import Engine
from computer_use.interaction.write.routes.policy import Attempts, choose, route_key
from computer_use.interaction.write.routes.selection.admission import stage_admission
from computer_use.native.contracts import (
    Binding,
    Channel,
    ClickContinuation,
    ClickOffer,
    ClickPreparationEvidence,
    ClickReceipt,
    ClickTransportEvidence,
    Dispatch,
    Facts,
    HostPolicy,
    InputContext,
    Intent,
    Knowledge,
    Primitive,
    StepResult,
    UnitCommand,
)
from computer_use.protocol.execution import validate_click_offer
from unit_tests.test_text_input_routes import RecordingPort

TARGET = Binding("session", "app", "root", "surface", "button")
CLICK = Intent("click", {"button": "left", "count": 1})


def click_route(delivery, admission="ready", preparation=(), *, profile="default", action=None):
    semantic = delivery == "object_action"
    return Primitive(
        delivery,
        "native_action" if semantic else "pointer_sequence",
        Channel.OBJECT if semantic else Channel.POINTER,
        Knowledge.NO if admission == "unavailable" else Knowledge.YES,
        Knowledge.YES if admission == "ready" else Knowledge.NO,
        pending=() if admission == "ready" else ("foreground",) if preparation == ("foreground",) else ("receiver",),
        action=action or ("AXPress" if semantic else None),
        click=ClickOffer(
            "test." + delivery,
            "semantic" if semantic else "pointer",
            delivery,
            admission,
            preparation_options=preparation,
            preference_profile=profile,
        ),
    )


def facts(*routes, **changes):
    return replace(
        Facts(
            TARGET,
            "facts",
            "target",
            Knowledge.YES,
            Knowledge.YES,
            Knowledge.YES,
            (),
            tuple(routes),
            InputContext(),
            platform="macos",
            native_role="AXButton",
            actions_complete=True,
        ),
        **changes,
    )


@pytest.mark.parametrize("delivery", ["object_action", "surface_pointer"])
@pytest.mark.parametrize("preparation", [(), ("background",), ("foreground",)])
def test_addressed_route_preparation_precedes_ready_global_mouse(delivery, preparation):
    addressed = click_route(delivery, "preparationRequired" if preparation else "ready", preparation)
    pointer = click_route("desktop_pointer")
    selected = choose(facts(pointer, addressed), CLICK, HostPolicy(), Attempts())
    assert selected.primitive == addressed
    assert selected.foreground == (preparation == ("foreground",))
    assert not selected.prepare  # Integrated preparation and dispatch stay atomic.


def test_available_background_route_precedes_activation_of_another_route():
    action = click_route("object_action", "preparationRequired", ("foreground",))
    directed = click_route("surface_pointer", "preparationRequired", ("background",))
    selected = choose(facts(action, directed), CLICK, HostPolicy(), Attempts())
    assert selected.primitive == directed and not selected.foreground


@pytest.mark.parametrize("admission", ["unavailable", "insufficientEvidence"])
def test_unavailable_addressed_route_requires_explicit_global_input(admission):
    directed, pointer = click_route("surface_pointer", admission), click_route("desktop_pointer")
    with pytest.raises(ComputerUseError, match="explicit Computer"):
        choose(facts(directed, pointer), CLICK, HostPolicy(), Attempts())
    # A legacy global flag cannot turn the object selector into raw input.
    with pytest.raises(ComputerUseError, match="explicit Computer"):
        choose(facts(directed, pointer), replace(CLICK, global_input=True), HostPolicy(), Attempts())


def test_failed_addressed_route_keeps_macos_scope_and_preserves_other_platform_defaults():
    directed, pointer = (
        click_route("surface_pointer", "preparationRequired", ("background",)),
        click_route("desktop_pointer"),
    )
    attempts = Attempts()
    attempts.unavailable.add(route_key(UnitCommand(directed, TARGET, CLICK)))
    with pytest.raises(ComputerUseError, match="explicit Computer"):
        choose(facts(directed, pointer), CLICK, HostPolicy(), attempts)
    for platform in ("windows", "linux"):
        assert choose(facts(directed, pointer, platform=platform), CLICK, HostPolicy(), attempts).primitive == pointer


@pytest.mark.parametrize(
    "platform,role,action",
    [
        ("macos", "AXButton", "AXPress"),
        ("windows", "ControlType.Button", "Invoke.Invoke"),
        ("linux", "push button", "click"),
    ],
)
def test_frontmost_global_pointer_does_not_override_semantic_action(platform, role, action):
    native, pointer = click_route("object_action", action=action), click_route("desktop_pointer")
    current = facts(
        native,
        pointer,
        platform=platform,
        native_role=role,
        input=InputContext(foreground_surface=TARGET.surface, pointer_hit=TARGET.node, geometry_current=Knowledge.YES),
    )
    assert choose(current, CLICK, HostPolicy(), Attempts()).primitive == native


def test_native_mechanism_can_require_pointer_before_any_action_is_dispatched():
    native, pointer = click_route("object_action"), click_route("surface_pointer", profile="physicalRequired")
    assert choose(facts(native, pointer), CLICK, HostPolicy(allow_global_input=True), Attempts()).primitive == pointer
    explicit = Intent("action", {"action": "AXPress"})
    assert choose(facts(native, pointer), explicit, HostPolicy(), Attempts()).primitive == native


@pytest.mark.parametrize(
    "parameters",
    [{"button": "right", "count": 1}, {"button": "left", "count": 2}, {"x": 12, "y": 20, "button": "left", "count": 1}],
)
def test_mouse_specific_requests_retain_pointer_semantics(parameters):
    native, pointer = click_route("object_action"), click_route("surface_pointer")
    selected = choose(
        facts(native, pointer), Intent("click", parameters), HostPolicy(allow_global_input=True), Attempts()
    )
    assert selected.primitive == pointer


@pytest.mark.parametrize("foreground", [False, True])
def test_editor_preparation_precedes_ready_pointer_focus(foreground):
    editor = Primitive(
        "editor",
        "prepare_receiver",
        Channel.EDITOR,
        Knowledge.YES,
        Knowledge.NO,
        ("foreground",) if foreground else ("receiver",),
        Knowledge.NO if foreground else Knowledge.YES,
        global_input=Knowledge.NO,
    )
    pointer = Primitive("mouse", "prepare_receiver", Channel.POINTER, Knowledge.YES, Knowledge.YES)
    selected = choose(facts(pointer, editor), Intent("focus", {}), HostPolicy(), Attempts())
    assert selected.primitive == editor and selected.prepare and selected.foreground == foreground


@pytest.mark.anyio
@pytest.mark.parametrize("dispatch", [Dispatch.DISPATCHED, Dispatch.PARTIAL, Dispatch.UNKNOWN])
async def test_uncertain_addressed_click_never_replays_globally(dispatch):
    directed, pointer = click_route("surface_pointer"), click_route("desktop_pointer")
    receipt = ClickReceipt(
        (),
        ClickTransportEvidence(call_entered=True, call_returned=True),
        ClickContinuation("stop", TARGET, directed.click.route_family),
    )
    port = RecordingPort(
        [facts(directed, pointer)], [StepResult(TARGET, dispatch, "effect unconfirmed", click=receipt)]
    )
    operation = Engine(port).execute(CLICK, TARGET)
    if dispatch == Dispatch.DISPATCHED:
        assert (await operation)["status"] == "dispatched"
    else:
        with pytest.raises(ComputerUseError):
            await operation
    assert len(port.commands) == 1 and port.commands[0].primitive == directed


@pytest.mark.anyio
@pytest.mark.parametrize("progress_confirmed", [False, True])
async def test_only_confirmed_original_viewport_progress_can_continue_an_unrequested_click(progress_confirmed):
    directed = click_route("surface_pointer", "preparationRequired", ("background", "foreground"))
    preparation = ClickPreparationEvidence(
        "pointerPreparation",
        TARGET,
        Dispatch.DISPATCHED,
        "contentPossible",
        "withinScope",
        ("originalTargetViewportProgress=confirmed",) if progress_confirmed else (),
    )
    failed = ClickReceipt(
        (preparation,),
        ClickTransportEvidence(),
        ClickContinuation("reselectWithinScope", TARGET, directed.click.route_family),
    )
    completed = ClickReceipt(
        (),
        ClickTransportEvidence(call_entered=True, call_returned=True),
        ClickContinuation("stop", TARGET, directed.click.route_family),
    )
    port = RecordingPort(
        [facts(directed)],
        [
            StepResult(
                TARGET,
                Dispatch.NOT_DISPATCHED,
                "original viewport moved but final background click receiver remained unavailable",
                error_code="input_unavailable",
                retry_reason="preparationUnavailable",
                click=failed,
            ),
            StepResult(TARGET, Dispatch.DISPATCHED, "original click sent once", click=completed),
        ],
    )
    operation = Engine(port).execute(CLICK, TARGET)
    if progress_confirmed:
        assert (await operation)["status"] == "dispatched"
        assert len(port.commands) == 2
        assert not port.commands[0].foreground and port.commands[1].foreground
        assert all(command.target == TARGET and command.primitive == directed for command in port.commands)
    else:
        with pytest.raises(ComputerUseError):
            await operation
        assert len(port.commands) == 1


@pytest.mark.parametrize("dispatch", [Dispatch.UNKNOWN, Dispatch.PARTIAL])
def test_viewport_progress_never_covers_pending_native_preparation(dispatch):
    preparation = ClickPreparationEvidence(
        "pointerPreparation",
        TARGET,
        dispatch,
        "contentPossible",
        "withinScope",
        ("originalTargetViewportProgress=confirmed",),
    )
    receipt = ClickReceipt(
        (preparation,),
        ClickTransportEvidence(),
        ClickContinuation("reselectWithinScope", TARGET, "original-route"),
    )
    assert not receipt.permits_replanning
    confirmed = replace(preparation, dispatch=Dispatch.DISPATCHED)
    entered = replace(
        receipt, preparation_evidence=(confirmed,), transport_evidence=ClickTransportEvidence(call_entered=True)
    )
    assert not entered.permits_replanning


@pytest.mark.anyio
@pytest.mark.parametrize("dispatch", [Dispatch.DISPATCHED, Dispatch.PARTIAL, Dispatch.UNKNOWN])
async def test_disabled_action_with_document_preparation_keeps_click_atomic_without_replay(dispatch):
    action = replace(
        click_route("object_action", "preparationRequired", ("background", "foreground")),
        background_preparation=Knowledge.YES,
    )
    pointer = click_route("desktop_pointer")
    receipt = ClickReceipt(
        (),
        ClickTransportEvidence(call_entered=True, call_returned=True),
        ClickContinuation("sameCandidateRefresh", TARGET, action.click.route_family),
    )
    port = RecordingPort(
        [facts(action, pointer, enabled=Knowledge.NO)],
        [StepResult(TARGET, dispatch, "native document preparation and one action attempt", click=receipt)],
    )

    operation = Engine(port).execute(CLICK, TARGET)
    if dispatch == Dispatch.DISPATCHED:
        assert (await operation)["status"] == "dispatched"
    else:
        with pytest.raises(ComputerUseError):
            await operation

    # Raw enabled=false cannot erase an explicit native preparation offer.
    # Possible requested input still prevents both refresh and pointer fallback.
    assert len(port.commands) == 1 and port.inspections == 1
    command = port.commands[0]
    assert command.primitive == action and command.target == TARGET and command.intent == CLICK
    assert not command.prepare and not command.foreground
    assert port.finished == ["operation"]


@pytest.mark.parametrize("admission", ["unavailable", "insufficientEvidence"])
def test_unusable_physical_requirement_cannot_erase_independent_action(admission):
    native = click_route("object_action")
    pointer = click_route("desktop_pointer", admission, profile="physicalRequired")
    assert choose(facts(native, pointer), CLICK, HostPolicy(), Attempts()).primitive == native


@pytest.mark.parametrize(
    "old_metadata",
    [
        {"preferenceProfile": "physicalWhenAvailableInExclusive"},
        {"preferenceHint": "readyPhysical"},
    ],
)
def test_old_mode_specific_click_metadata_is_rejected(old_metadata):
    wire = click_route("desktop_pointer").click.wire()
    wire.update(old_metadata)
    with pytest.raises(ValueError):
        validate_click_offer(wire, "pointer_sequence")


@pytest.mark.parametrize("unit", ["native_text_edit", "native_value_replace"])
def test_object_text_unit_cannot_invent_receiver_or_foreground_preparation(unit):
    cold = Primitive("object", unit, Channel.OBJECT, Knowledge.YES, Knowledge.NO, ("selection",), Knowledge.NO)
    assert not stage_admission(cold).available
    ready = replace(cold, readiness=Knowledge.YES, pending=())
    assert stage_admission(ready).ready


@pytest.mark.parametrize("foreground", [False, True])
def test_same_object_editor_preparation_remains_available(foreground):
    editor = Primitive(
        "editor",
        "native_text_edit",
        Channel.EDITOR,
        Knowledge.YES,
        Knowledge.NO,
        ("foreground",) if foreground else ("selection",),
        Knowledge.NO if foreground else Knowledge.YES,
    )
    command = choose(facts(editor), Intent("insert", {"text": "text"}), HostPolicy(), Attempts())
    assert command.prepare and command.foreground == foreground


@pytest.mark.anyio
@pytest.mark.parametrize("platform", ["macos", "windows", "linux"])
async def test_missing_object_selection_prepares_declared_keyboard_then_refreshes(platform):
    cold = Primitive(
        "object", "native_text_edit", Channel.OBJECT, Knowledge.YES, Knowledge.NO, ("selection",), Knowledge.NO
    )
    keys = Primitive(
        "keys", "key_sequence", Channel.KEYBOARD, Knowledge.YES, Knowledge.NO, ("foreground",), Knowledge.NO
    )
    ready = replace(cold, readiness=Knowledge.YES, pending=())
    port = RecordingPort(
        [facts(cold, keys, platform=platform), facts(ready, platform=platform)],
        [
            StepResult(TARGET, Dispatch.NOT_DISPATCHED, "keyboard prepared"),
            StepResult(TARGET, Dispatch.DISPATCHED, "text sent"),
        ],
    )
    result = await Engine(port).execute(Intent("insert", {"text": "text"}), TARGET)
    assert result["status"] == "dispatched"
    assert [(command.primitive.id, command.prepare, command.foreground) for command in port.commands] == [
        ("keys", True, True),
        ("object", False, False),
    ]
