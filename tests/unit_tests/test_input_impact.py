# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Native effect evidence survives success, incomplete cleanup and lost replies."""

from dataclasses import replace

import pytest
from computer_use.errors import ComputerUseError
from computer_use.interaction.write.engine import Engine
from computer_use.native.contracts import (
    Cleanup,
    Dispatch,
    HostPolicy,
    InputEffect,
    Intent,
    Knowledge,
    NativeImpact,
    PreparationImpact,
    StepResult,
)
from computer_use.native.transport import decode_native_impact
from computer_use.protocol.execution import decode_execution_result, input_impact
from computer_use.session.results import action_result, result_receipt
from unit_tests.test_text_input_routes import KEYS, TARGET, RecordingPort, observed


@pytest.fixture
def anyio_backend():
    return "asyncio"


def actual(*, stage="input", transport="process", state="invoked", clipboard="notUsed"):
    return NativeImpact(
        "partial", (InputEffect(stage, "keyboard", transport, "native-key-post", state),), Knowledge.NO, clipboard
    )


@pytest.mark.anyio
async def test_result_distinguishes_declared_preparation_and_actual_transport_and_is_immutable():
    declared = replace(KEYS, preparation_impact=PreparationImpact(keyboard=Knowledge.NO, activation=Knowledge.YES))
    step = StepResult(TARGET, Dispatch.DISPATCHED, "posted", impact=actual())
    port = RecordingPort([observed(declared)], [step], HostPolicy(allow_global_input=False))
    raw = await Engine(port).execute(Intent("insert", {"text": "a"}), TARGET)
    result = action_result(raw)
    attempt = result.impact.attempts[0]
    assert result_receipt(result)["impact"] == raw["impact"]
    assert attempt.declared_global_input == "unknown" and not result.impact.global_input_allowed
    assert attempt.preparation_potential.activation == "yes"
    assert attempt.actual.effects[0].transport == "process"
    assert attempt.actual.foreground_changed == "no"
    assert attempt.actual.coverage == "partial"  # Omitted effect types are not thereby disproved.
    with pytest.raises(AttributeError):
        attempt.actual.effects[0].state = "notInvoked"


@pytest.mark.anyio
async def test_preparation_effect_is_retained_when_requested_input_never_dispatched():
    step = StepResult(
        TARGET,
        Dispatch.NOT_DISPATCHED,
        "permission discovered after preparation",
        preparation=("focused original editor",),
        error_code="permission_required",
        impact=actual(stage="preparation", transport="globalHID", state="possible"),
    )
    port = RecordingPort([observed(KEYS)], [step])
    with pytest.raises(ComputerUseError) as caught:
        await Engine(port).execute(Intent("insert", {"text": "a"}), TARGET)
    attempt = caught.value.impact.attempts[0]
    assert caught.value.effect == "possible" and attempt.dispatch == "notDispatched"
    assert attempt.preparation == ("focused original editor",)
    assert attempt.actual.effects[0].stage == "preparation"
    assert attempt.actual.effects[0].state == "possible"
    assert len(port.commands) == 1


@pytest.mark.anyio
async def test_native_mutation_evidence_prevents_claiming_a_clean_permission_failure():
    step = StepResult(
        TARGET,
        Dispatch.NOT_DISPATCHED,
        "late permission",
        error_code="permission_required",
        retry_reason="routeUnavailable",
        impact=actual(stage="preparation", state="possible"),
    )
    port = RecordingPort([observed(KEYS)], [step])
    with pytest.raises(ComputerUseError) as caught:
        await Engine(port).execute(Intent("insert", {"text": "a"}), TARGET)
    assert caught.value.code == "permission_required" and port.inspections == 1
    assert caught.value.effect == "possible"
    assert len(port.commands) == 1


@pytest.mark.anyio
async def test_cleanup_failure_preserves_input_and_clipboard_evidence():
    step = StepResult(
        TARGET, Dispatch.DISPATCHED, "paste sent", cleanup=Cleanup.FAILED, impact=actual(clipboard="failed")
    )
    port = RecordingPort([observed(KEYS)], [step])
    with pytest.raises(ComputerUseError) as caught:
        await Engine(port).execute(Intent("insert", {"text": "a"}), TARGET)
    attempt = caught.value.impact.attempts[0]
    assert attempt.dispatch == "dispatched" and attempt.cleanup == "failed"
    assert attempt.actual.clipboard_restoration == "failed"
    assert caught.value.effect == "possible" and port.calls.retired
    assert len(port.commands) == 1


@pytest.mark.anyio
async def test_lost_native_reply_keeps_attempt_with_unknown_effects():
    class LostReply(RecordingPort):
        async def execute(self, operation, step, command, timeout_ms):
            self.commands.append(command)
            raise ConnectionError("driver exited after request delivery")

    port = LostReply([observed(KEYS)], [])
    with pytest.raises(ComputerUseError) as caught:
        await Engine(port).execute(Intent("insert", {"text": "a"}), TARGET)
    attempt = caught.value.impact.attempts[0]
    assert attempt.actual is None and attempt.dispatch == attempt.cleanup == "unknown"
    assert attempt.preparation is None
    assert caught.value.effect == "possible" and len(port.commands) == 1


@pytest.mark.anyio
async def test_missing_native_impact_is_unknown_even_for_successful_dispatch():
    port = RecordingPort([observed(KEYS)], [StepResult(TARGET, Dispatch.DISPATCHED, "posted")])
    raw = await Engine(port).execute(Intent("insert", {"text": "a"}), TARGET)
    assert action_result(raw).impact.attempts[0].actual is None


def test_wire_impact_retains_unknown_and_truncation_and_rejects_malformed_state():
    wire = dict(
        coverage="partial",
        effects=[dict(stage="cleanup", device="keyboard", transport="globalHID", mechanism="post", state="possible")],
        foregroundChanged="unknown",
        clipboardRestoration="ownershipLost",
        omittedEffects=3,
    )
    parsed = decode_native_impact(input_impact.parse(wire))
    assert parsed.foreground_changed == Knowledge.UNKNOWN and parsed.omitted_effects == 3
    assert parsed.effects[0].state == "possible"
    wire["effects"][0]["state"] = "verified"
    with pytest.raises(ValueError):
        input_impact.parse(wire)


def test_complete_wire_impact_cannot_hide_omitted_effects():
    wire = dict(
        target=dict(session="session", instance="app", rootWindow="root", surface="window", node="field", capture=None),
        dispatch="dispatched",
        detail="posted",
        preparation=[],
        confirmation="unavailable",
        cleanup="complete",
        errorCode=None,
        retryReason=None,
        context=None,
        impact=dict(
            coverage="complete", effects=[], foregroundChanged="no", clipboardRestoration="notUsed", omittedEffects=1
        ),
    )
    with pytest.raises(ValueError, match="omit effects"):
        decode_execution_result("execute", wire)
