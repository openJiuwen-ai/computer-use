# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Receiver churn bounds preparation, while a ready endpoint can finish the write."""

from dataclasses import replace

import pytest
from computer_use.errors import ComputerUseError
from computer_use.interaction.write.pipeline import WritePipeline
from computer_use.native.contracts import (
    Actuator,
    Channel,
    ClickContinuation,
    ClickOffer,
    ClickReceipt,
    ClickTransportEvidence,
    Confirmation,
    Dispatch,
    FocusMember,
    Intent,
    Knowledge,
    Primitive,
    StepResult,
)
from unit_tests.test_text_input_routes import FOCUS, KEYS, TARGET, RecordingPort, observed


@pytest.fixture
def anyio_backend():
    return "asyncio"


EDIT = Primitive("edit", "native_text_edit", Channel.OBJECT, Knowledge.YES, Knowledge.YES)


def pair(index, *routes, **changes):
    return observed(
        *routes,
        actuator=Actuator(f"editor-{index}"),
        receiver=FocusMember(f"focus-{index}"),
        **changes,
    )


def prepared():
    return StepResult(
        TARGET,
        Dispatch.NOT_DISPATCHED,
        "receiver prepared",
        preparation=("nativeFocus",),
        confirmation=Confirmation.CONFIRMED,
    )


async def run(port, intent=None):
    pipeline = WritePipeline(port)
    with port.calls.claim(5000) as remaining:
        return await pipeline.run_in_call(intent or Intent("insert", {"text": "query"}), TARGET, remaining)


@pytest.mark.anyio
@pytest.mark.parametrize("endpoint", [EDIT, KEYS])
async def test_third_resolved_pair_can_dispatch_its_ready_endpoint(endpoint):
    port = RecordingPort(
        [pair(0, FOCUS), pair(1, FOCUS), pair(2, endpoint)],
        [prepared(), prepared(), StepResult(TARGET, Dispatch.DISPATCHED, "one requested edit")],
    )
    result = await run(port)
    assert result["status"] == "dispatched"
    assert [command.prepare for command in port.commands] == [True, True, False]
    assert port.commands[-1].primitive == endpoint
    assert all(command.target == TARGET for command in port.commands)
    assert port.inspections == 3 and port.finished == ["operation"]


@pytest.mark.anyio
async def test_continually_new_pairs_cannot_keep_starting_preparation():
    port = RecordingPort([pair(i, FOCUS) for i in range(5)], [prepared() for _ in range(5)])
    with pytest.raises(ComputerUseError) as caught:
        await run(port)
    assert caught.value.code == "input_unavailable"
    assert caught.value.effect == "possible"
    assert len(port.commands) == 2 and all(command.prepare for command in port.commands)
    assert port.inspections == 3 and port.finished == ["operation"]


@pytest.mark.anyio
async def test_exhausted_new_pair_preparation_keeps_ready_whole_value_route():
    value = Primitive("value", "native_value_replace", Channel.OBJECT, Knowledge.YES, Knowledge.YES)
    port = RecordingPort(
        [pair(0, FOCUS), pair(1, FOCUS), pair(2, FOCUS, value)],
        [prepared(), prepared(), StepResult(TARGET, Dispatch.DISPATCHED, "one whole-value edit")],
    )
    result = await run(port, Intent("replace", {"text": "replacement"}))
    assert result["status"] == "dispatched"
    assert [command.prepare for command in port.commands] == [True, True, False]
    assert port.commands[-1].primitive == value


@pytest.mark.anyio
async def test_same_pair_keeps_background_then_foreground_before_another_pair_prepares():
    cold = pair(0, FOCUS)
    port = RecordingPort(
        [cold, cold, pair(1, FOCUS), pair(2, EDIT)],
        [
            StepResult(
                TARGET,
                Dispatch.NOT_DISPATCHED,
                "background unavailable",
                error_code="input_unavailable",
                retry_reason="preparationUnavailable",
            ),
            prepared(),
            prepared(),
            StepResult(TARGET, Dispatch.DISPATCHED, "one requested edit"),
        ],
    )
    assert (await run(port))["status"] == "dispatched"
    assert [(command.prepare, command.foreground) for command in port.commands] == [
        (True, False),
        (True, True),
        (True, False),
        (False, False),
    ]


@pytest.mark.anyio
async def test_new_primitive_id_does_not_reset_same_pair_route_history():
    port = RecordingPort(
        [pair(0, FOCUS), pair(0, replace(FOCUS, id="refreshed-focus"))],
        [prepared()],
    )
    with pytest.raises(ComputerUseError):
        await run(port)
    assert len(port.commands) == 1 and port.inspections == 2


@pytest.mark.anyio
@pytest.mark.parametrize("dispatch", [Dispatch.DISPATCHED, Dispatch.UNKNOWN, Dispatch.PARTIAL])
async def test_ready_endpoint_with_possible_input_never_replans(dispatch):
    port = RecordingPort(
        [pair(0, FOCUS), pair(1, FOCUS), pair(2, EDIT, KEYS), pair(3, FOCUS)],
        [
            prepared(),
            prepared(),
            StepResult(TARGET, dispatch, "effect unconfirmed", error_code="execution_unknown"),
            prepared(),
        ],
    )
    with pytest.raises(ComputerUseError) as caught:
        await run(port)
    assert caught.value.effect == ("partial" if dispatch == Dispatch.PARTIAL else "possible")
    assert port.inspections == 3 and len(port.commands) == 3
    assert not port.commands[-1].prepare


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"identity": Knowledge.NO}, "stale_target"),
        ({"within_scope": Knowledge.NO}, "stale_target"),
        ({"within_scope": Knowledge.UNKNOWN}, "input_unavailable"),
        ({"blocked_by": ("modal",)}, "blocked_by_modal"),
    ],
)
async def test_ready_endpoint_keeps_original_target_and_modal_boundaries(changes, code):
    port = RecordingPort(
        [pair(0, FOCUS), pair(1, FOCUS), pair(2, EDIT, **changes)],
        [prepared(), prepared()],
    )
    with pytest.raises(ComputerUseError) as caught:
        await run(port)
    assert caught.value.code == code and len(port.commands) == 2


def click(ready):
    return Primitive(
        "click",
        "pointer_sequence",
        Channel.POINTER,
        Knowledge.YES,
        Knowledge.YES if ready else Knowledge.NO,
        pending=() if ready else ("receiver",),
        click=ClickOffer(
            "test.directed",
            "pointer",
            "surface_pointer",
            "ready" if ready else "preparationRequired",
            preparation_options=() if ready else ("background",),
        ),
    )


def click_unavailable():
    return StepResult(
        TARGET,
        Dispatch.NOT_DISPATCHED,
        "background route unavailable",
        error_code="input_unavailable",
        retry_reason="routeUnavailable",
        click=ClickReceipt(
            (), ClickTransportEvidence(), ClickContinuation("reselectWithinScope", TARGET, "test.directed")
        ),
    )


@pytest.mark.anyio
@pytest.mark.parametrize("ready", [True, False])
async def test_integrated_clicks_share_the_same_preparation_pair_budget(ready):
    result = StepResult(
        TARGET,
        Dispatch.DISPATCHED,
        "one requested click",
        click=ClickReceipt((), ClickTransportEvidence(), ClickContinuation("stop", TARGET, "test.directed")),
    )
    port = RecordingPort(
        [pair(0, click(False)), pair(1, click(False)), pair(2, click(ready))],
        [click_unavailable(), click_unavailable(), result],
    )
    if ready:
        assert (await run(port, Intent("click", {})))["status"] == "dispatched"
        assert len(port.commands) == 3
    else:
        with pytest.raises(ComputerUseError) as caught:
            await run(port, Intent("click", {}))
        assert caught.value.code == "input_unavailable" and len(port.commands) == 2
    assert all(not command.prepare for command in port.commands)
