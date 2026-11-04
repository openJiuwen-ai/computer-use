# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Windows editing preference preserves unused native replacement contracts.

These public-pipeline tests consume synthetic native declarations. They do not
claim to classify a Qt provider or establish that a particular setter works.
"""

from dataclasses import replace

import pytest
from computer_use.errors import ComputerUseError
from computer_use.interaction.write.pipeline import WritePipeline
from computer_use.interaction.write.routes.policy import Attempts, choose, route_key
from computer_use.interaction.write.routes.selection.candidates import enumerate_candidates
from computer_use.native.contracts import (
    Actuator,
    Channel,
    Dispatch,
    HostPolicy,
    InputContext,
    Intent,
    Knowledge,
    Primitive,
    StepResult,
    UnitCommand,
)
from unit_tests.test_text_input_routes import KEYS, TARGET, RecordingPort, observed


@pytest.fixture
def anyio_backend():
    return "asyncio"


SHAPES = ("owned-combo", "date-spinner")
# Keep the unit currently emitted by the Windows ValuePattern adapter. The
# regression is public preference/history handling, not setter reclassification.
VALUE = Primitive("value", "native_text_edit", Channel.OBJECT, Knowledge.YES, Knowledge.YES)
COLD_KEYS = replace(KEYS, readiness=Knowledge.NO, pending=("receiver",))


def intent(shape):
    return Intent("replace", {"text": "2026/11/02" if shape == "date-spinner" else "14:00"})


def contextual(shape, *routes):
    return observed(
        *routes,
        platform="windows",
        native_role="ControlType.Spinner" if shape == "date-spinner" else "ControlType.ComboBox",
        input=InputContext(editor="owned-editor", editor_owner=TARGET.node),
    )


def unavailable_preparation(detail):
    return StepResult(
        TARGET,
        Dispatch.NOT_DISPATCHED,
        detail,
        error_code="input_unavailable",
        retry_reason="preparationUnavailable",
    )


def sent():
    return StepResult(TARGET, Dispatch.DISPATCHED, "replacement sent once")


async def run(port, shape):
    pipeline = WritePipeline(port)
    with port.calls.claim(5000) as remaining:
        return await pipeline.run_in_call(intent(shape), TARGET, remaining)


def signature(port, shape):
    assert all(command.target == TARGET and command.intent == intent(shape) for command in port.commands)
    assert port.finished == ["operation"]
    return [(command.primitive.id, command.prepare, command.foreground) for command in port.commands]


@pytest.mark.parametrize("shape", SHAPES)
def test_contextual_preference_does_not_remove_declared_setter_candidates(shape):
    candidates = enumerate_candidates(contextual(shape, VALUE, COLD_KEYS), intent(shape), HostPolicy())
    assert VALUE in candidates and COLD_KEYS in candidates


@pytest.mark.anyio
@pytest.mark.parametrize("shape", SHAPES)
async def test_unused_setter_survives_both_keyboard_preparation_refusals(shape):
    cold = contextual(shape, VALUE, COLD_KEYS)
    port = RecordingPort(
        [cold, cold, cold],
        [
            unavailable_preparation("background preparation unavailable"),
            unavailable_preparation("foreground preparation unavailable"),
            sent(),
        ],
    )

    assert (await run(port, shape))["status"] == "dispatched"
    assert signature(port, shape) == [("keys", True, False), ("keys", True, True), ("value", False, False)]
    assert port.inspections == 3


@pytest.mark.anyio
@pytest.mark.parametrize("shape", SHAPES)
async def test_ready_contextual_keyboard_precedes_unused_setter(shape):
    port = RecordingPort([contextual(shape, VALUE, KEYS)], [sent()])

    assert (await run(port, shape))["status"] == "dispatched"
    assert signature(port, shape) == [("keys", False, False)]
    assert port.inspections == 1


@pytest.mark.anyio
@pytest.mark.parametrize("shape", SHAPES)
async def test_undispatched_keyboard_route_refusal_keeps_unused_setter(shape):
    ready = contextual(shape, VALUE, KEYS)
    port = RecordingPort(
        [ready, ready],
        [
            StepResult(
                TARGET,
                Dispatch.NOT_DISPATCHED,
                "requested keyboard input was not sent",
                error_code="input_unavailable",
                retry_reason="routeUnavailable",
            ),
            sent(),
        ],
    )

    assert (await run(port, shape))["status"] == "dispatched"
    assert signature(port, shape) == [("keys", False, False), ("value", False, False)]
    assert port.inspections == 2


@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("reason", ("route-unavailable", "geometry", "unsupported", "preparation-budget"))
def test_ineligible_keyboard_cannot_erase_independent_setter(shape, reason):
    keys = COLD_KEYS
    attempts = Attempts()
    if reason == "route-unavailable":
        attempts.unavailable.add(route_key(UnitCommand(keys, TARGET, intent(shape))))
    elif reason == "geometry":
        keys = replace(keys, pending=("geometry",))
    elif reason == "unsupported":
        keys = replace(keys, supported=Knowledge.NO)
    else:
        attempts.preparation_pairs.update(((Actuator("editor-a"), None), (Actuator("editor-b"), None)))

    command = choose(contextual(shape, VALUE, keys), intent(shape), HostPolicy(), attempts)
    assert command.primitive == VALUE and not command.prepare and not command.foreground


@pytest.mark.parametrize(
    ("role", "text"),
    (("ControlType.Edit", "replacement"), ("ControlType.Spinner", "42.50")),
)
def test_noncontextual_replacement_keeps_existing_object_priority(role, text):
    facts = replace(contextual("owned-combo", VALUE, KEYS), native_role=role)
    command = choose(facts, Intent("replace", {"text": text}), HostPolicy(), Attempts())
    assert command.primitive == VALUE and not command.prepare and not command.foreground


@pytest.mark.anyio
@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("dispatch", (Dispatch.DISPATCHED, Dispatch.UNKNOWN, Dispatch.PARTIAL))
async def test_possible_contextual_keyboard_input_never_replays_through_setter(shape, dispatch):
    port = RecordingPort(
        [contextual(shape, VALUE, KEYS), contextual(shape, VALUE)],
        [
            StepResult(
                TARGET,
                dispatch,
                "requested keyboard effect is unconfirmed",
                error_code="execution_unknown",
            ),
            sent(),
        ],
    )

    with pytest.raises(ComputerUseError) as caught:
        await run(port, shape)

    assert caught.value.effect == ("partial" if dispatch == Dispatch.PARTIAL else "possible")
    assert signature(port, shape) == [("keys", False, False)]
    assert port.inspections == 1
