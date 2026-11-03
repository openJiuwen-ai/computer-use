# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Late permission discovery closes one effect-free route, never replays input."""

from dataclasses import replace

import pytest
from computer_use.errors import ComputerUseError
from computer_use.interaction.write.engine import Engine
from computer_use.native.contracts import Channel, Cleanup, Dispatch, InputContext, Intent, Knowledge, Primitive, StepResult
from unit_tests.test_text_input_routes import EDITOR, KEYS, TARGET, RecordingPort, observed


@pytest.fixture
def anyio_backend():
    return "asyncio"


def denied(**changes):
    return replace(
        StepResult(
            TARGET, Dispatch.NOT_DISPATCHED, "resolved keyboard delivery needs global input",
            error_code="permission_required", retry_reason="routeUnavailable",
        ), **changes,
    )


@pytest.mark.anyio
async def test_late_permission_discovery_selects_independent_editor_in_same_call():
    port = RecordingPort(
        [observed(KEYS, EDITOR)],
        [denied(), StepResult(TARGET, Dispatch.DISPATCHED, "object edit applied")],
    )
    result = await Engine(port).execute(Intent("insert", {"text": "query"}), TARGET)
    assert result["status"] == "dispatched"
    assert [command.primitive for command in port.commands] == [KEYS, EDITOR]
    assert port.inspections == 2 and port.finished == ["operation"]
    assert all(command.target == TARGET for command in port.commands)


@pytest.mark.anyio
async def test_late_denied_preparation_closes_delivery_instead_of_trying_foreground():
    keys = replace(KEYS, readiness=Knowledge.NO, pending=("receiver",))
    value = Primitive("value", "native_value_replace", Channel.OBJECT, Knowledge.YES, Knowledge.YES)
    current = observed(keys, value, input=InputContext(foreground_surface=TARGET.surface))
    port = RecordingPort([current], [denied(), StepResult(TARGET, Dispatch.DISPATCHED, "value replaced")])
    result = await Engine(port).execute(Intent("replace", {"text": "query"}), TARGET)
    assert result["status"] == "dispatched"
    assert [(c.primitive, c.prepare, c.foreground) for c in port.commands] == [(keys, True, False), (value, False, False)]


@pytest.mark.anyio
async def test_permission_is_preserved_when_no_permitted_alternative_remains():
    port = RecordingPort([observed(KEYS)], [denied()])
    with pytest.raises(ComputerUseError) as caught:
        await Engine(port).execute(Intent("insert", {"text": "query"}), TARGET)
    assert caught.value.code == "permission_required" and caught.value.effect == "none"
    assert "allow_global_input" in caught.value.next
    assert len(port.commands) == 1 and port.inspections == 2


@pytest.mark.anyio
@pytest.mark.parametrize(
    "changes",
    [
        {"preparation": ("focus changed",)},
        {"retry_reason": None},
        {"cleanup": Cleanup.UNKNOWN, "retry_reason": None},
        {"dispatch": Dispatch.UNKNOWN, "retry_reason": None},
        {"dispatch": Dispatch.PARTIAL, "retry_reason": None},
    ],
)
async def test_permission_error_with_effect_or_without_native_allowance_never_replays(changes):
    port = RecordingPort([observed(KEYS, EDITOR)], [denied(**changes)])
    with pytest.raises(ComputerUseError) as caught:
        await Engine(port).execute(Intent("insert", {"text": "query"}), TARGET)
    assert caught.value.code == ("execution_failed" if changes.get("cleanup") == Cleanup.UNKNOWN else "permission_required")
    assert len(port.commands) == 1
