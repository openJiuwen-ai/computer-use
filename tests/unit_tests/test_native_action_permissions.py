# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""A native method's permission is independent of the control's general state."""

from dataclasses import replace

import pytest
from computer_use.errors import ComputerUseError
from computer_use.interaction.write.engine import Engine
from computer_use.native.contracts import Channel, Dispatch, Intent, Knowledge, Primitive, StepResult
from unit_tests.test_text_input_routes import TARGET, RecordingPort, observed


@pytest.fixture
def anyio_backend():
    return "asyncio"


ACTION = Intent("action", {"action": "ScrollItem.ScrollIntoView"})
SCROLL = Primitive(
    "scroll-item", "native_action", Channel.OBJECT, Knowledge.YES, Knowledge.YES, action=ACTION.parameters["action"]
)


def native_facts(*, enabled=Knowledge.NO, **changes):
    return observed(
        SCROLL,
        enabled=enabled,
        platform="windows",
        native_role=None,
        actions_complete=False,
        **changes,
    )


@pytest.mark.anyio
@pytest.mark.parametrize("enabled", (Knowledge.NO, Knowledge.UNKNOWN))
async def test_ready_named_method_does_not_require_general_control_enabled(enabled):
    port = RecordingPort(
        [native_facts(enabled=enabled)], [StepResult(TARGET, Dispatch.DISPATCHED, "viewport request sent")]
    )
    assert (await Engine(port).execute(ACTION, TARGET))["status"] == "dispatched"
    assert len(port.commands) == 1
    command = port.commands[0]
    assert command.target == TARGET and command.primitive == SCROLL
    assert not command.prepare and not command.foreground
    assert port.finished == ["operation"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "boundary", ({"identity": Knowledge.NO}, {"within_scope": Knowledge.NO}, {"blocked_by": ("modal",)})
)
async def test_method_permission_does_not_bypass_original_target_boundaries(boundary):
    port = RecordingPort([native_facts(**boundary)], [])
    with pytest.raises(ComputerUseError):
        await Engine(port).execute(ACTION, TARGET)
    assert port.commands == []


@pytest.mark.anyio
async def test_native_disabled_invoke_remains_unavailable():
    invoke = replace(SCROLL, action="Invoke.Invoke", readiness=Knowledge.NO, pending=("enabled",))
    port = RecordingPort([replace(native_facts(), primitives=(invoke,))], [])
    with pytest.raises(ComputerUseError):
        await Engine(port).execute(Intent("action", {"action": "Invoke.Invoke"}), TARGET)
    assert port.commands == []
