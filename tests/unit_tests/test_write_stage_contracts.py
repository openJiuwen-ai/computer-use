# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Cross-operation preparation, refresh, dispatch and effect boundaries."""

from dataclasses import replace

import pytest
from computer_use.errors import ComputerUseError
from computer_use.interaction.write.engine import Engine
from computer_use.interaction.write.routes.selection.admission import stage_admission
from computer_use.native.contracts import (
    ApplicationBinding,
    Binding,
    Channel,
    Cleanup,
    ClickContinuation,
    ClickOffer,
    ClickReceipt,
    ClickTransportEvidence,
    Confirmation,
    Dispatch,
    Facts,
    HostPolicy,
    InputContext,
    Intent,
    Knowledge,
    Primitive,
    StepResult,
)
from unit_tests.test_text_input_routes import RecordingPort


@pytest.fixture
def anyio_backend():
    return "asyncio"


TARGET = Binding("session", "app", "root", "surface", "field")
IMAGE = replace(TARGET, node=None, capture="capture")
CASES = (
    ("insert", "key_sequence", Channel.KEYBOARD, {"text": "query"}, TARGET, None),
    ("replace", "key_sequence", Channel.KEYBOARD, {"text": "replacement"}, TARGET, None),
    ("paste", "clipboard_paste", Channel.KEYBOARD, {"text": "multi\nline"}, TARGET, None),
    ("key", "key_sequence", Channel.KEYBOARD, {"key": "Return"}, TARGET, None),
    ("scroll", "pointer_sequence", Channel.POINTER, {"direction": "down", "pages": 1, "x": 10, "y": 10}, IMAGE, None),
    ("drag", "pointer_sequence", Channel.POINTER, {"fromX": 10, "fromY": 10, "toX": 20, "toY": 20}, IMAGE, None),
    ("openMenu", "native_action", Channel.OBJECT, {}, TARGET, "AXShowMenu"),
    ("action", "native_action", Channel.OBJECT, {"action": "AXPress"}, TARGET, "AXPress"),
)


def primitive(case, *, ready=True, pending=(), background=Knowledge.YES):
    return Primitive(
        "unit",
        case[1],
        case[2],
        Knowledge.YES,
        Knowledge.YES if ready else Knowledge.NO,
        pending,
        background,
        action=case[5],
    )


def facts(case, *routes, **changes):
    return replace(
        Facts(
            case[4],
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


@pytest.mark.parametrize("case", CASES, ids=lambda case: case[0])
def test_same_stage_contract_distinguishes_readiness_preparation_and_prerequisites(case):
    assert stage_admission(primitive(case)).ready
    waiting = stage_admission(primitive(case, ready=False, pending=("receiver",)))
    assert not waiting.ready and waiting.preparation == ("background", "foreground")
    foreground = stage_admission(primitive(case, ready=False, pending=("foreground",)))
    assert foreground.preparation == ("foreground",)
    for condition in ("geometry", "enabled"):
        blocked = stage_admission(primitive(case, ready=False, pending=(condition,)))
        assert not blocked.available and blocked.blockers == (condition,)


@pytest.mark.anyio
@pytest.mark.parametrize("case", CASES, ids=lambda case: case[0])
async def test_ready_operations_dispatch_once_without_preparing(case):
    port = RecordingPort([facts(case, primitive(case))], [StepResult(case[4], Dispatch.DISPATCHED, "sent")])
    result = await Engine(port).execute(Intent(case[0], case[3]), case[4])
    assert result["status"] == "dispatched"
    assert len(port.commands) == 1 and not port.commands[0].prepare


@pytest.mark.anyio
@pytest.mark.parametrize("case", CASES, ids=lambda case: case[0])
async def test_context_preparation_is_followed_by_fresh_facts_then_original_intent(case):
    port = RecordingPort(
        [facts(case, primitive(case, ready=False, pending=("receiver",))), facts(case, primitive(case))],
        [
            StepResult(case[4], Dispatch.NOT_DISPATCHED, "prepared", confirmation=Confirmation.CONFIRMED),
            StepResult(case[4], Dispatch.DISPATCHED, "sent"),
        ],
    )
    result = await Engine(port).execute(Intent(case[0], case[3]), case[4])
    assert result["status"] == "dispatched" and port.inspections == 2
    assert [c.prepare for c in port.commands] == [True, False]
    assert all(c.target == case[4] and c.intent == Intent(case[0], case[3]) for c in port.commands)


@pytest.mark.anyio
@pytest.mark.parametrize("case", CASES, ids=lambda case: case[0])
async def test_scope_change_during_preparation_stops_every_operation(case):
    port = RecordingPort(
        [
            facts(case, primitive(case, ready=False, pending=("receiver",))),
            facts(case, primitive(case), within_scope=Knowledge.NO),
        ],
        [StepResult(case[4], Dispatch.NOT_DISPATCHED, "prepared")],
    )
    with pytest.raises(ComputerUseError) as caught:
        await Engine(port).execute(Intent(case[0], case[3]), case[4])
    assert caught.value.code == "stale_target" and len(port.commands) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("case", CASES, ids=lambda case: case[0])
@pytest.mark.parametrize("dispatch", (Dispatch.DISPATCHED, Dispatch.PARTIAL, Dispatch.UNKNOWN))
async def test_requested_input_never_reenters_preparation_or_another_route(case, dispatch):
    alternative = replace(primitive(case), id="alternative", action="AXConfirm" if case[5] is not None else None)
    port = RecordingPort(
        [facts(case, primitive(case), alternative)], [StepResult(case[4], dispatch, "effect unconfirmed")]
    )
    operation = Engine(port).execute(Intent(case[0], case[3]), case[4])
    if dispatch == Dispatch.DISPATCHED:
        assert (await operation)["status"] == "dispatched"
    else:
        with pytest.raises(ComputerUseError):
            await operation
    assert len(port.commands) == 1 and not port.commands[0].prepare


@pytest.mark.anyio
@pytest.mark.parametrize("case", CASES, ids=lambda case: case[0])
async def test_foreground_preparation_follows_background_unavailability(case):
    cold = facts(case, primitive(case, ready=False, pending=("receiver",)))
    results = [
        StepResult(
            case[4],
            Dispatch.NOT_DISPATCHED,
            "background unavailable",
            error_code="input_unavailable",
            retry_reason="preparationUnavailable",
        ),
        StepResult(case[4], Dispatch.NOT_DISPATCHED, "foreground prepared"),
        StepResult(case[4], Dispatch.DISPATCHED, "sent"),
    ]
    port = RecordingPort([cold, cold, facts(case, primitive(case))], results, HostPolicy())
    result = await Engine(port).execute(Intent(case[0], case[3]), case[4])
    assert result["status"] == "dispatched"
    assert [(c.prepare, c.foreground) for c in port.commands] == [(True, False), (True, True), (False, False)]


@pytest.mark.anyio
@pytest.mark.parametrize("case", CASES, ids=lambda case: case[0])
async def test_cleanup_failure_stops_without_replanning(case):
    port = RecordingPort(
        [facts(case, primitive(case))],
        [StepResult(case[4], Dispatch.UNKNOWN, "cleanup uncertain", cleanup=Cleanup.UNKNOWN)],
    )
    with pytest.raises(ComputerUseError):
        await Engine(port).execute(Intent(case[0], case[3]), case[4])
    assert len(port.commands) == 1 and port.calls.retired


@pytest.mark.anyio
async def test_paste_can_prepare_native_focus_before_keyboard_readiness_without_publishing_clipboard():
    case = CASES[2]
    context = Primitive(
        "context", "prepare_receiver", Channel.EDITOR, Knowledge.YES, Knowledge.NO, ("receiver",), Knowledge.YES
    )
    keys = primitive(case, ready=False, pending=("foreground",), background=Knowledge.NO)
    port = RecordingPort(
        [facts(case, context, keys), facts(case, primitive(case))],
        [
            StepResult(TARGET, Dispatch.NOT_DISPATCHED, "AX focus", preparation=("native focus",)),
            StepResult(TARGET, Dispatch.DISPATCHED, "one paste"),
        ],
    )
    await Engine(port).execute(Intent("paste", case[3]), TARGET)
    assert [(c.primitive.unit, c.prepare) for c in port.commands] == [
        ("prepare_receiver", True),
        ("clipboard_paste", False),
    ]
    assert port.inspections == 2


@pytest.mark.anyio
@pytest.mark.parametrize(
    "platform,role", [("macos", "AXTextField"), ("windows", "ControlType.Edit"), ("linux", "entry")]
)
@pytest.mark.parametrize("case", CASES[:6], ids=lambda case: case[0])
async def test_unified_write_stages_apply_across_platform_semantics(case, platform, role):
    cold = facts(case, primitive(case, ready=False, pending=("receiver",)), platform=platform, native_role=role)
    ready = facts(case, primitive(case), platform=platform, native_role=role)
    port = RecordingPort(
        [cold, ready],
        [
            StepResult(case[4], Dispatch.NOT_DISPATCHED, "prepared", confirmation=Confirmation.CONFIRMED),
            StepResult(case[4], Dispatch.DISPATCHED, "sent"),
        ],
    )
    result = await Engine(port).execute(Intent(case[0], case[3]), case[4])
    assert result["status"] == "dispatched" and port.inspections == 2
    assert [(c.prepare, c.foreground, c.intent.kind, c.target) for c in port.commands] == [
        (True, False, case[0], case[4]),
        (False, False, case[0], case[4]),
    ]


def test_click_uses_same_admission_contract_and_requires_an_explicit_preparation_offer():
    base = Primitive(
        "pointer",
        "pointer_sequence",
        Channel.POINTER,
        Knowledge.YES,
        Knowledge.NO,
        ("foreground",),
        Knowledge.NO,
        click=ClickOffer(
            "native.pointer", "pointer", "desktop_pointer", "preparationRequired", preparation_options=("foreground",)
        ),
    )
    admission = stage_admission(base)
    assert not admission.ready and admission.preparation == ("foreground",)
    unavailable = replace(base, click=replace(base.click, admission="insufficientEvidence", preparation_options=()))
    assert not stage_admission(unavailable).available


@pytest.mark.anyio
@pytest.mark.parametrize(
    "unit,delivery,channel",
    [
        ("native_action", "object_action", Channel.OBJECT),
        ("native_selection", "object_selection", Channel.OBJECT),
        ("pointer_sequence", "surface_pointer", Channel.POINTER),
        ("pointer_sequence", "desktop_pointer", Channel.POINTER),
    ],
)
@pytest.mark.parametrize("admission", ["ready", "preparationRequired"])
@pytest.mark.parametrize("dispatch", [Dispatch.DISPATCHED, Dispatch.PARTIAL, Dispatch.UNKNOWN])
async def test_every_click_delivery_keeps_preparation_atomic_and_stops_after_possible_input(
    unit,
    delivery,
    channel,
    admission,
    dispatch,
):
    offer = ClickOffer(
        "test." + delivery,
        "pointer" if channel == Channel.POINTER else "semantic",
        delivery,
        admission,
        preparation_options=("background",) if admission == "preparationRequired" else (),
    )
    declared = Primitive(
        "click",
        unit,
        channel,
        Knowledge.YES,
        Knowledge.YES if admission == "ready" else Knowledge.NO,
        pending=() if admission == "ready" else ("receiver",),
        action="AXPress" if unit == "native_action" else None,
        click=offer,
    )
    click = ClickReceipt(
        (),
        ClickTransportEvidence(call_entered=True, call_returned=True),
        ClickContinuation("sameCandidateRefresh", TARGET, offer.route_family),
    )
    port = RecordingPort([facts(CASES[0], declared)], [StepResult(TARGET, dispatch, "possible input", click=click)])
    operation = Engine(port).execute(Intent("click", {"button": "left", "count": 1}), TARGET)
    if dispatch == Dispatch.DISPATCHED:
        assert (await operation)["status"] == "dispatched"
    else:
        with pytest.raises(ComputerUseError):
            await operation
    assert len(port.commands) == 1 and not port.commands[0].prepare
    assert port.commands[0].primitive.click.route_family == offer.route_family


@pytest.mark.anyio
async def test_focus_completes_when_the_requested_receiver_is_confirmed():
    focus = Primitive(
        "focus", "prepare_receiver", Channel.EDITOR, Knowledge.YES, Knowledge.NO, ("receiver",), Knowledge.YES
    )
    case = CASES[0]
    port = RecordingPort(
        [facts(case, focus)],
        [StepResult(TARGET, Dispatch.NOT_DISPATCHED, "focused", confirmation=Confirmation.CONFIRMED)],
    )
    assert (await Engine(port).execute(Intent("focus", {}), TARGET))["status"] == "verified"
    assert len(port.commands) == 1 and port.commands[0].prepare


@pytest.mark.anyio
async def test_already_focused_target_can_be_confirmed_without_foreground_authorization():
    focus = Primitive(
        "focus", "prepare_receiver", Channel.EDITOR, Knowledge.YES, Knowledge.YES, background_preparation=Knowledge.NO
    )
    case = CASES[0]
    port = RecordingPort(
        [facts(case, focus)],
        [StepResult(TARGET, Dispatch.NOT_DISPATCHED, "already focused", confirmation=Confirmation.CONFIRMED)],
    )
    assert (await Engine(port).execute(Intent("focus", {}), TARGET))["status"] == "verified"
    assert len(port.commands) == 1 and not port.commands[0].foreground


@pytest.mark.anyio
@pytest.mark.parametrize("case", CASES, ids=lambda case: case[0])
async def test_unconfirmed_preparation_cleanup_cannot_start_another_attempt(case):
    cold = primitive(case, ready=False, pending=("receiver",))
    port = RecordingPort(
        [facts(case, cold)],
        [StepResult(case[4], Dispatch.NOT_DISPATCHED, "preparation cleanup unknown", cleanup=Cleanup.UNKNOWN)],
    )
    with pytest.raises(ComputerUseError):
        await Engine(port).execute(Intent(case[0], case[3]), case[4])
    assert len(port.commands) == 1 and port.commands[0].prepare and port.calls.retired


@pytest.mark.anyio
async def test_independent_native_edit_and_launch_do_not_acquire_a_receiver():
    for kind, unit, parameters in (
        ("replace", "native_text_edit", {"text": "value"}),
        ("launch", "launch_application", {}),
    ):
        independent = Primitive("object", unit, Channel.OBJECT, Knowledge.YES, Knowledge.YES)
        case = CASES[0]
        target = ApplicationBinding("session", "installation") if kind == "launch" else TARGET
        application = {"id": "running-instance"} if kind == "launch" else None
        port = RecordingPort(
            [facts(case, independent, binding=target)],
            [StepResult(target, Dispatch.DISPATCHED, "sent", application=application)],
        )
        await Engine(port).execute(Intent(kind, parameters), target)
        assert len(port.commands) == 1 and not port.commands[0].prepare
