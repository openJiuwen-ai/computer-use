# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""A declared action prepares its document before one unchanged wire dispatch."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from computer_use.errors import ComputerUseError
from computer_use.interaction.write.engine import Engine
from computer_use.native.contracts import Channel, HostPolicy, Intent, Knowledge, Primitive
from computer_use.native.transport import ConnectionPort, encode_target
from computer_use.protocol.execution import decode_execution_result
from unit_tests.test_text_input_routes import TARGET, RecordingPort, observed


@pytest.fixture
def anyio_backend():
    return "asyncio"


ACTION = Intent("action", {"action": "AXPress"})
COLD = Primitive(
    "document-press",
    "native_action",
    Channel.OBJECT,
    Knowledge.YES,
    Knowledge.UNKNOWN,
    pending=("receiver",),
    background_preparation=Knowledge.YES,
    action="AXPress",
)
READY = replace(COLD, readiness=Knowledge.YES, pending=())


def receipt(*, dispatched=False, error_code=None, retry_reason=None, preparation=()):
    return decode_execution_result(
        "execute",
        {
            "target": encode_target(TARGET),
            "dispatch": "dispatched" if dispatched else "notDispatched",
            "detail": "AXPress sent" if dispatched else "Original document preparation result",
            "preparation": list(preparation),
            "confirmation": "unavailable" if error_code or dispatched else "confirmed",
            "cleanup": "complete",
            "errorCode": error_code,
            "retryReason": retry_reason,
            "context": None,
        },
    )


def wire_port(observations, receipts):
    connection = SimpleNamespace(
        protocol_version=6,
        platform="macos",
        capabilities=(),
        session_identity=(1, TARGET.session),
        request=AsyncMock(
            side_effect=[
                {"operation": "operation", "target": encode_target(TARGET), "remainingMs": 5000},
                *receipts,
                {"cleanup": "complete"},
            ]
        ),
    )
    port = ConnectionPort(connection, HostPolicy())
    observations = RecordingPort(observations, [])
    # Use the existing facts fixture for queries; all begin/prepare/dispatch/
    # finish envelopes still pass through the production ConnectionPort.
    port.inspect = observations.inspect
    return port, connection, observations


def execute_commands(connection):
    return [call.args[1]["command"] for call in connection.request.await_args_list if call.args[0] == "execute"]


@pytest.mark.anyio
@pytest.mark.parametrize("background_unavailable", (False, True))
@pytest.mark.parametrize("initial_enabled", (Knowledge.YES, Knowledge.NO), ids=("enabled", "disabled"))
@pytest.mark.parametrize("readiness", (Knowledge.NO, Knowledge.UNKNOWN), ids=("unready", "unknown"))
async def test_action_document_preparation_keeps_one_original_dispatch(
    background_unavailable, initial_enabled, readiness
):
    cold = observed(replace(COLD, readiness=readiness), enabled=initial_enabled, native_role="AXButton")
    ready = observed(READY, native_role="AXButton")
    observations = [cold]
    receipts = []
    expected = [("prepare_receiver", False)]
    if background_unavailable:
        observations.append(cold)
        receipts.append(receipt(error_code="input_unavailable", retry_reason="preparationUnavailable"))
        expected.append(("prepare_receiver", True))
    observations.append(ready)
    receipts.extend((receipt(), receipt(dispatched=True)))
    expected.append(("native_action", False))
    port, connection, facts = wire_port(observations, receipts)

    result = await Engine(port).execute(ACTION, TARGET)

    assert result["status"] == "dispatched"
    commands = execute_commands(connection)
    assert [(command["kind"], command["foreground"]) for command in commands] == expected
    assert all(command["primitiveId"] == COLD.id and command["target"] == encode_target(TARGET) for command in commands)
    assert sum(command["kind"] == "native_action" for command in commands) == 1
    calls = connection.request.await_args_list
    assert calls[0].args[0] == "begin"
    assert calls[0].args[1]["intent"] == {"kind": "action", "parameters": {"action": "AXPress"}, "globalInput": False}
    assert "clickContract" not in calls[0].args[1], "explicit actions must use the ordinary write pipeline"
    assert all(call.args[1]["operation"] == "operation" for call in calls if call.args[0] == "execute")
    assert facts.inspections == len(commands)
    assert calls[-1].args[0] == "finish"


@pytest.mark.anyio
@pytest.mark.parametrize("supported", (Knowledge.YES, Knowledge.NO), ids=("supported", "unsupported"))
async def test_document_preparation_that_leaves_action_disabled_never_dispatches(supported):
    cold = observed(COLD, enabled=Knowledge.NO, native_role="AXButton")
    disabled = replace(READY, supported=supported, readiness=Knowledge.NO, pending=("enabled",))
    port, connection, facts = wire_port(
        [cold, observed(disabled, enabled=Knowledge.NO, native_role="AXButton")],
        [receipt(preparation=("native document context AXMain",))],
    )

    with pytest.raises(ComputerUseError) as caught:
        await Engine(port).execute(ACTION, TARGET)

    assert caught.value.code == "input_unavailable"
    assert caught.value.detail == "The selected control is disabled."
    assert caught.value.effect == "possible", "preparation effects must survive a later disabled refusal"
    # A refusal before preparation cannot satisfy this regression. The original
    # document must have been prepared and inspected again before the refusal.
    assert execute_commands(connection) == [
        {
            "kind": "prepare_receiver",
            "primitiveId": COLD.id,
            "target": encode_target(TARGET),
            "foreground": False,
        }
    ]
    assert facts.inspections == 2
    assert connection.request.await_args_list[-1].args[0] == "finish"


@pytest.mark.anyio
@pytest.mark.parametrize("supported", (Knowledge.YES, Knowledge.NO), ids=("supported", "unsupported"))
async def test_disabled_action_without_document_preparation_obligation_never_executes(supported):
    disabled = replace(READY, supported=supported, readiness=Knowledge.NO, pending=("enabled",))
    port, connection, facts = wire_port([observed(disabled, enabled=Knowledge.NO, native_role="AXButton")], [])

    with pytest.raises(ComputerUseError) as caught:
        await Engine(port).execute(ACTION, TARGET)

    assert caught.value.code == "input_unavailable"
    assert caught.value.detail == "The selected control is disabled."
    assert caught.value.effect == "none"
    assert execute_commands(connection) == []
    assert facts.inspections == 1
    assert connection.request.await_args_list[-1].args[0] == "finish"


@pytest.mark.anyio
@pytest.mark.parametrize("initial_enabled", (Knowledge.YES, Knowledge.NO), ids=("enabled", "disabled"))
async def test_preparation_error_without_explicit_unavailability_cannot_take_foreground(initial_enabled):
    cold = observed(COLD, enabled=initial_enabled, native_role="AXButton")
    port, connection, facts = wire_port(
        [cold, observed(READY, native_role="AXButton")],
        [receipt(error_code="input_unavailable")],
    )

    with pytest.raises(ComputerUseError) as caught:
        await Engine(port).execute(ACTION, TARGET)

    assert caught.value.code == "input_unavailable"
    commands = execute_commands(connection)
    assert commands == [
        {
            "kind": "prepare_receiver",
            "primitiveId": COLD.id,
            "target": encode_target(TARGET),
            "foreground": False,
        }
    ]
    assert facts.inspections == 1
    assert connection.request.await_args_list[-1].args[0] == "finish"


@pytest.mark.anyio
async def test_final_action_precondition_change_refreshes_into_background_preparation():
    ready = observed(READY, native_role="AXButton")
    cold = observed(COLD, native_role="AXButton")
    port, connection, facts = wire_port(
        [ready, cold, ready],
        [
            receipt(error_code="input_unavailable", retry_reason="preconditionChanged"),
            receipt(),
            receipt(dispatched=True),
        ],
    )
    events = []
    port.pipeline_diagnostic = events.append

    result = await Engine(port).execute(ACTION, TARGET)

    assert result["status"] == "dispatched"
    commands = execute_commands(connection)
    assert [(command["kind"], command["foreground"]) for command in commands] == [
        ("native_action", False),
        ("prepare_receiver", False),
        ("native_action", False),
    ]
    assert all(command["primitiveId"] == COLD.id and command["target"] == encode_target(TARGET) for command in commands)
    assert connection.request.await_args_list[0].args[1]["intent"] == {
        "kind": "action",
        "parameters": {"action": "AXPress"},
        "globalInput": False,
    }
    # Two action envelopes reach the driver. The first stops at its final
    # precondition guard; only the final receipt reports requested AXPress input.
    outcomes = [event.dispatch for event in events if event.phase == "native_receipt"]
    assert outcomes == ["notDispatched", "notDispatched", "dispatched"]
    assert facts.inspections == 3
    assert connection.request.await_args_list[-1].args[0] == "finish"


@pytest.mark.anyio
async def test_final_precondition_change_does_not_authorize_foreground_after_a_terminal_preparation_error():
    port, connection, facts = wire_port(
        [observed(READY, native_role="AXButton"), observed(COLD, native_role="AXButton")],
        [
            receipt(error_code="input_unavailable", retry_reason="preconditionChanged"),
            receipt(error_code="input_unavailable"),
        ],
    )
    events = []
    port.pipeline_diagnostic = events.append

    with pytest.raises(ComputerUseError) as caught:
        await Engine(port).execute(ACTION, TARGET)

    assert caught.value.code == "input_unavailable"
    commands = execute_commands(connection)
    assert [(command["kind"], command["foreground"]) for command in commands] == [
        ("native_action", False),
        ("prepare_receiver", False),
    ]
    assert all(command["primitiveId"] == COLD.id and command["target"] == encode_target(TARGET) for command in commands)
    assert [event.dispatch for event in events if event.phase == "native_receipt"] == ["notDispatched", "notDispatched"]
    assert facts.inspections == 2
    assert connection.request.await_args_list[-1].args[0] == "finish"
