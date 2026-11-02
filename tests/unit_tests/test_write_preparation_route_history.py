# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Preparation-mode refusals do not blacklist later ready dispatch contracts."""

from dataclasses import replace

import pytest
from computer_use.errors import ComputerUseError
from computer_use.interaction.write.pipeline import WritePipeline
from computer_use.native.contracts import (
    Channel,
    Confirmation,
    Dispatch,
    Intent,
    Knowledge,
    Primitive,
    StepResult,
)
from unit_tests.test_text_input_routes import KEYS, TARGET, RecordingPort, observed


@pytest.fixture
def anyio_backend():
    return "asyncio"


POINTER_CONTEXT = Primitive(
    "pointer-focus",
    "prepare_receiver",
    Channel.POINTER,
    Knowledge.YES,
    Knowledge.NO,
    pending=("receiver",),
    background_preparation=Knowledge.YES,
)


def unavailable_preparation(detail):
    return StepResult(
        TARGET,
        Dispatch.NOT_DISPATCHED,
        detail,
        error_code="input_unavailable",
        retry_reason="preparationUnavailable",
    )


def pointer_prepared():
    return StepResult(
        TARGET,
        Dispatch.NOT_DISPATCHED,
        "pointer established the original receiver",
        preparation=("pointerFocus",),
        confirmation=Confirmation.CONFIRMED,
    )


async def run(port, kind="replace"):
    pipeline = WritePipeline(port)
    with port.calls.claim(5000) as remaining:
        return await pipeline.run_in_call(Intent(kind, {"text": "replacement"}), TARGET, remaining)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "kind,unit", [("replace", "key_sequence"), ("insert", "key_sequence"), ("paste", "clipboard_paste")]
)
async def test_another_preparation_can_make_the_previously_unprepared_route_ready(kind, unit):
    ready = replace(KEYS, unit=unit)
    cold = replace(ready, readiness=Knowledge.NO, pending=("receiver",))
    port = RecordingPort(
        [observed(cold), observed(cold), observed(cold, POINTER_CONTEXT), observed(ready)],
        [
            unavailable_preparation("background preparation unavailable"),
            unavailable_preparation("foreground preparation could not establish this receiver"),
            pointer_prepared(),
            StepResult(TARGET, Dispatch.DISPATCHED, "requested input sent once"),
        ],
    )
    result = await run(port, kind)
    assert result["status"] == "dispatched"
    assert [(command.primitive.channel, command.prepare, command.foreground) for command in port.commands] == [
        (Channel.KEYBOARD, True, False),
        (Channel.KEYBOARD, True, True),
        (Channel.POINTER, True, False),
        (Channel.KEYBOARD, False, False),
    ]
    assert all(command.target == TARGET and command.actuator is command.receiver is None for command in port.commands)
    assert port.inspections == 4 and port.finished == ["operation"]


@pytest.mark.anyio
async def test_failed_preparation_modes_remain_consumed_when_receiver_stays_unready():
    cold = replace(KEYS, readiness=Knowledge.NO, pending=("receiver",))
    port = RecordingPort(
        [observed(cold)],
        [unavailable_preparation("background failed"), unavailable_preparation("foreground failed")],
    )
    with pytest.raises(ComputerUseError) as caught:
        await run(port)
    assert caught.value.code == "input_unavailable"
    assert [(command.prepare, command.foreground) for command in port.commands] == [(True, False), (True, True)]
    assert port.inspections == 3


@pytest.mark.anyio
async def test_true_dispatch_route_refusal_stays_excluded_after_another_preparation():
    cold = replace(KEYS, readiness=Knowledge.NO, pending=("receiver",))
    port = RecordingPort(
        [observed(KEYS), observed(cold, POINTER_CONTEXT), observed(KEYS)],
        [
            StepResult(
                TARGET,
                Dispatch.NOT_DISPATCHED,
                "dispatch route unavailable",
                error_code="input_unavailable",
                retry_reason="routeUnavailable",
            ),
            pointer_prepared(),
        ],
    )
    with pytest.raises(ComputerUseError):
        await run(port)
    assert len(port.commands) == 2 and port.inspections == 3
    assert not port.commands[0].prepare and port.commands[1].primitive == POINTER_CONTEXT


@pytest.mark.anyio
async def test_terminal_pointer_preparation_failure_cannot_dispatch_now_ready_keys():
    cold = replace(KEYS, readiness=Knowledge.NO, pending=("receiver",))
    port = RecordingPort(
        [observed(cold), observed(cold), observed(cold, POINTER_CONTEXT), observed(KEYS)],
        [
            unavailable_preparation("background unavailable"),
            unavailable_preparation("foreground unavailable"),
            StepResult(
                TARGET,
                Dispatch.NOT_DISPATCHED,
                "the preparation click could not preserve the original caret",
                preparation=("pointerFocus",),
                error_code="input_unavailable",
            ),
            StepResult(TARGET, Dispatch.DISPATCHED, "must never send this input"),
        ],
    )
    with pytest.raises(ComputerUseError) as caught:
        await run(port, "insert")
    assert caught.value.code == "input_unavailable" and caught.value.effect == "possible"
    assert len(port.commands) == 3 and port.inspections == 3
    assert all(command.prepare for command in port.commands)
