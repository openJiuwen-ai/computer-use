# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Selected-text qualification may recover after preparation, never after input."""

from dataclasses import asdict, replace
from itertools import pairwise

import pytest
from computer_use.errors import ComputerUseError
from computer_use.interaction.write.engine import Engine
from computer_use.native.contracts import (
    Cleanup,
    Confirmation,
    Dispatch,
    HostPolicy,
    InputEffect,
    Intent,
    Knowledge,
    NativeImpact,
    StepResult,
)
from unit_tests.test_text_input_routes import EDITOR, KEYS, TARGET, RecordingPort, observed

# NativeInputUnit.selectedText declares the editor channel and its own
# preparation endpoint; a cold object-value setter has a different contract.
SETTER = replace(
    EDITOR,
    readiness=Knowledge.NO,
    pending=("receiver",),
    global_input=Knowledge.NO,
)
COLD_KEYS = replace(KEYS, readiness=Knowledge.NO, pending=("receiver",), global_input=Knowledge.NO)
READY_SETTER = replace(SETTER, readiness=Knowledge.YES, pending=())
READY_KEYS = replace(COLD_KEYS, readiness=Knowledge.YES, pending=())
INTENT = Intent("insert", {"text": "query"})


@pytest.fixture
def anyio_backend():
    return "asyncio"


def excluded(**changes):
    return replace(
        StepResult(
            TARGET,
            Dispatch.NOT_DISPATCHED,
            "the selected provider has no selected-text content setter",
            error_code="input_unavailable",
            retry_reason="routeUnavailable",
        ),
        **changes,
    )


def prepared(**changes):
    return replace(
        StepResult(TARGET, Dispatch.NOT_DISPATCHED, "receiver confirmed", confirmation=Confirmation.CONFIRMED),
        **changes,
    )


def impact(*, stage="preparation", state="invoked", foreground=Knowledge.NO):
    return NativeImpact(
        "partial",
        (InputEffect(stage, "keyboard", "process", "native-preparation", state),),
        foreground,
        "notUsed",
    )


PREPARATION_EVIDENCE = [
    pytest.param({"preparation": ("original target focused",)}, id="preparation-label"),
    pytest.param({"impact": impact()}, id="native-preparation"),
    pytest.param({"impact": impact(state="possible")}, id="possible-preparation"),
    pytest.param({"impact": NativeImpact("partial", (), Knowledge.YES, "notUsed")}, id="foreground-change"),
    pytest.param({"impact": NativeImpact("partial", (), Knowledge.NO, "restored")}, id="clipboard-restored"),
    pytest.param({"impact": NativeImpact("partial", (), Knowledge.NO, "ownershipLost")}, id="clipboard-ownership-lost"),
    pytest.param({"impact": NativeImpact("partial", (), Knowledge.NO, "failed")}, id="earlier-clipboard-failure"),
    pytest.param(
        {"impact": NativeImpact("partial", (), Knowledge.NO, "notUsed", omitted_effects=1)},
        id="omitted-effects",
    ),
]


def assert_retained_evidence(result, index, receipt):
    attempt = result["impact"]["attempts"][index]
    assert attempt["preparation"] == receipt.preparation
    assert attempt["cleanup"] == receipt.cleanup.value
    assert attempt["dispatch"] == receipt.dispatch.value
    expected = None
    if receipt.impact is not None:
        expected = asdict(receipt.impact)
        expected["foreground_changed"] = receipt.impact.foreground_changed.value
    assert attempt["actual"] == expected


class TracePort(RecordingPort):
    def __init__(self, observations, results, *, advance=lambda: None, policy=None):
        super().__init__(observations, results, policy)
        self.requests = []
        self.advance = advance

    async def begin(self, intent, target, timeout_ms):
        self.requests.append(("begin", None, target, timeout_ms))
        self.advance()
        return await super().begin(intent, target, timeout_ms)

    async def inspect(self, operation, target, timeout_ms):
        self.requests.append(("inspect", operation, target, timeout_ms))
        self.advance()
        return await super().inspect(operation, target, timeout_ms)

    async def execute(self, operation, step, command, timeout_ms):
        self.requests.append(("execute", operation, command.target, timeout_ms))
        self.advance()
        return await super().execute(operation, step, command, timeout_ms)


@pytest.mark.anyio
async def test_cold_setter_refusal_prepares_keys_and_dispatches_once_in_the_original_call(monkeypatch):
    now = [100.0]
    monkeypatch.setattr("computer_use.native.lifecycle.time.monotonic", lambda: now[0])

    def advance():
        now[0] += 0.025

    port = TracePort(
        [observed(SETTER, COLD_KEYS), observed(SETTER, COLD_KEYS), observed(SETTER, READY_KEYS)],
        [
            excluded(),
            prepared(preparation=("focused the original receiver",)),
            StepResult(TARGET, Dispatch.DISPATCHED, "keys delivered"),
        ],
        advance=advance,
        policy=HostPolicy(allow_global_input=False),
    )
    result = await Engine(port).execute(INTENT, TARGET, timeout_ms=1000)
    assert result["status"] == "dispatched"
    assert [(c.primitive, c.prepare, c.foreground) for c in port.commands] == [
        (SETTER, True, False),
        (COLD_KEYS, True, False),
        (READY_KEYS, False, False),
    ]
    assert all(c.target == TARGET and c.intent == INTENT for c in port.commands)
    assert [r[0] for r in port.requests].count("begin") == 1
    assert all(operation == "operation" for kind, operation, _, _ in port.requests if kind != "begin")
    assert all(target == TARGET for _, _, target, _ in port.requests)
    budgets = [timeout for _, _, _, timeout in port.requests]
    assert all(0 < later < earlier <= 1000 for earlier, later in pairwise(budgets))
    assert port.finished == ["operation"] and port.inspections == 3
    assert not port.policy.allow_global_input


@pytest.mark.anyio
async def test_unknown_provider_keeps_its_normal_selected_text_preparation_and_dispatch():
    port = RecordingPort(
        [observed(SETTER, COLD_KEYS), observed(READY_SETTER, COLD_KEYS)],
        [prepared(preparation=("focused original editor",)), StepResult(TARGET, Dispatch.DISPATCHED, "object edit")],
    )
    result = await Engine(port).execute(INTENT, TARGET)
    assert result["status"] == "dispatched"
    assert [(c.primitive, c.prepare, c.foreground) for c in port.commands] == [
        (SETTER, True, False),
        (READY_SETTER, False, False),
    ]
    assert port.finished == ["operation"]


@pytest.mark.anyio
async def test_plain_input_unavailable_does_not_authorize_another_route():
    port = RecordingPort([observed(SETTER, COLD_KEYS)], [excluded(retry_reason=None)])
    with pytest.raises(ComputerUseError) as caught:
        await Engine(port).execute(INTENT, TARGET)
    assert caught.value.code == "input_unavailable"
    assert len(port.commands) == port.inspections == 1
    assert port.commands[0].primitive == SETTER and port.commands[0].prepare


@pytest.mark.anyio
@pytest.mark.parametrize("changes", PREPARATION_EVIDENCE)
async def test_current_preparation_allows_key_recovery_and_retains_its_evidence(changes):
    refusal = excluded(**changes)
    port = RecordingPort(
        [observed(SETTER, COLD_KEYS), observed(SETTER, READY_KEYS)],
        [refusal, StepResult(TARGET, Dispatch.DISPATCHED, "keys delivered")],
    )
    result = await Engine(port).execute(INTENT, TARGET)
    assert result["status"] == "dispatched"
    assert [(c.primitive, c.prepare) for c in port.commands] == [(SETTER, True), (READY_KEYS, False)]
    assert all(c.target == TARGET and not c.foreground for c in port.commands)
    assert_retained_evidence(result, 0, refusal)
    assert port.finished == ["operation"]


@pytest.mark.anyio
@pytest.mark.parametrize("changes", PREPARATION_EVIDENCE)
async def test_original_target_preparation_survives_late_qualification_then_key_preparation_and_input(changes):
    previous = prepared(**changes)
    port = RecordingPort(
        [
            observed(SETTER, COLD_KEYS),
            observed(READY_SETTER, COLD_KEYS),
            observed(READY_SETTER, COLD_KEYS),
            observed(READY_SETTER, READY_KEYS),
        ],
        [previous, excluded(), prepared(), StepResult(TARGET, Dispatch.DISPATCHED, "keys delivered")],
    )
    result = await Engine(port).execute(INTENT, TARGET)
    assert result["status"] == "dispatched"
    assert [(c.primitive, c.prepare) for c in port.commands] == [
        (SETTER, True),
        (READY_SETTER, False),
        (COLD_KEYS, True),
        (READY_KEYS, False),
    ]
    assert all(c.target == TARGET and not c.foreground for c in port.commands)
    assert_retained_evidence(result, 0, previous)
    assert [attempt["dispatch"] for attempt in result["impact"]["attempts"]] == [
        "notDispatched",
        "notDispatched",
        "notDispatched",
        "dispatched",
    ]
    assert port.inspections == 4 and port.finished == ["operation"]


@pytest.mark.anyio
@pytest.mark.parametrize("state", ["invoked", "possible"])
@pytest.mark.parametrize("preparing", [True, False], ids=["preparing", "dispatching"])
async def test_explicit_input_effect_stops_qualification_recovery_even_with_not_dispatched(state, preparing):
    setter = SETTER if preparing else READY_SETTER
    port = RecordingPort(
        [observed(setter, COLD_KEYS)],
        [excluded(impact=impact(stage="input", state=state))],
    )
    with pytest.raises(ComputerUseError) as caught:
        await Engine(port).execute(INTENT, TARGET)
    assert caught.value.effect == "possible"
    assert len(port.commands) == port.inspections == 1
    assert port.commands[0].primitive == setter
    assert caught.value.impact.attempts[0].actual.effects[0].stage == "input"
    assert caught.value.impact.attempts[0].actual.effects[0].state == state


@pytest.mark.anyio
@pytest.mark.parametrize("state", ["invoked", "possible"])
@pytest.mark.parametrize("retry_reason", [None, "preconditionChanged", "preparationUnavailable"])
async def test_input_evidence_in_a_preparation_receipt_prevents_any_following_write(state, retry_reason):
    first = prepared(impact=impact(stage="input", state=state))
    if retry_reason is not None:
        first = replace(first, error_code="input_unavailable", retry_reason=retry_reason)
    port = RecordingPort(
        [observed(SETTER, COLD_KEYS), observed(READY_SETTER, COLD_KEYS)],
        [first, excluded()],
    )
    with pytest.raises(ComputerUseError) as caught:
        await Engine(port).execute(INTENT, TARGET)
    assert caught.value.effect == "possible"
    assert [(c.primitive, c.prepare) for c in port.commands] == [(SETTER, True)]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "evidence",
    [None, NativeImpact("partial", (), Knowledge.UNKNOWN, "unknown"), impact(state="notInvoked")],
)
async def test_missing_or_unknown_impact_does_not_create_a_new_qualification_gate(evidence):
    port = RecordingPort(
        [observed(SETTER, COLD_KEYS), observed(SETTER, READY_KEYS)],
        [excluded(impact=evidence), StepResult(TARGET, Dispatch.DISPATCHED, "keys delivered")],
    )
    assert (await Engine(port).execute(INTENT, TARGET))["status"] == "dispatched"
    assert [(c.primitive, c.prepare) for c in port.commands] == [(SETTER, True), (READY_KEYS, False)]


@pytest.mark.anyio
@pytest.mark.parametrize("dispatch", [Dispatch.DISPATCHED, Dispatch.PARTIAL, Dispatch.UNKNOWN])
async def test_requested_input_progress_never_replays_through_the_keyboard(dispatch):
    port = RecordingPort(
        [observed(READY_SETTER, COLD_KEYS)],
        [excluded(dispatch=dispatch, retry_reason=None)],
    )
    with pytest.raises(ComputerUseError) as caught:
        await Engine(port).execute(INTENT, TARGET)
    assert caught.value.effect == ("partial" if dispatch == Dispatch.PARTIAL else "possible")
    assert len(port.commands) == port.inspections == 1
    assert port.commands[0].primitive == READY_SETTER and not port.commands[0].prepare


@pytest.mark.anyio
@pytest.mark.parametrize("cleanup", [Cleanup.FAILED, Cleanup.UNKNOWN])
async def test_incomplete_cleanup_never_opens_the_alternate_route(cleanup):
    port = RecordingPort([observed(SETTER, COLD_KEYS)], [excluded(cleanup=cleanup, retry_reason=None)])
    with pytest.raises(ComputerUseError):
        await Engine(port).execute(INTENT, TARGET)
    assert len(port.commands) == port.inspections == 1


@pytest.mark.anyio
@pytest.mark.parametrize("changes", [{"identity": Knowledge.NO}, {"within_scope": Knowledge.NO}])
async def test_recovery_cannot_leave_an_unconfirmed_original_target(changes):
    refusal = excluded(preparation=("focused original target",))
    port = RecordingPort(
        [observed(SETTER, COLD_KEYS), observed(READY_KEYS, **changes)],
        [refusal],
    )
    with pytest.raises(ComputerUseError) as caught:
        await Engine(port).execute(INTENT, TARGET)
    assert caught.value.code == "stale_target" and caught.value.effect == "possible"
    assert len(port.commands) == 1 and port.inspections == 2
    assert port.commands[0].target == TARGET and port.commands[0].primitive == SETTER


@pytest.mark.anyio
async def test_no_alternative_preserves_the_provider_refusal_without_foreground_retry():
    port = RecordingPort([observed(SETTER)], [excluded()])
    with pytest.raises(ComputerUseError) as caught:
        await Engine(port).execute(INTENT, TARGET)
    assert caught.value.code == "input_unavailable" and caught.value.effect == "none"
    assert len(port.commands) == 1 and port.inspections == 2
    assert not port.commands[0].foreground
    assert port.finished == ["operation"]


@pytest.mark.anyio
async def test_opaque_primitive_id_refresh_cannot_reset_the_closed_route_allowance():
    class RefreshingPort(RecordingPort):
        async def inspect(self, operation, target, timeout_ms):
            facts = await super().inspect(operation, target, timeout_ms)
            return replace(facts, primitives=(replace(SETTER, id=f"provider-{self.inspections}"),))

    port = RefreshingPort([observed(SETTER)], [excluded()] * 8)
    with pytest.raises(ComputerUseError) as caught:
        await Engine(port).execute(INTENT, TARGET)
    assert caught.value.code == "input_unavailable"
    assert len(port.commands) == 1 and port.inspections == 2
    assert port.commands[0].primitive.id == "provider-1"
    assert port.finished == ["operation"]
