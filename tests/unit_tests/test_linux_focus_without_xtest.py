# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Native focus does not depend on the separate XTEST dispatch capability."""

import importlib
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest


@pytest.fixture(scope="module")
def modules():
    source = Path(__file__).resolve().parents[2] / "os_adapter/linux/src"
    with patch.object(sys, "path", [str(source), *sys.path]):
        return SimpleNamespace(
            keyboard=importlib.import_module("computer_use_linux.control.native_keyboard"),
            typed=importlib.import_module("computer_use_linux.control.native_typed_text"),
            paste=importlib.import_module("computer_use_linux.control.native_paste"),
            target=importlib.import_module("computer_use_linux.control.native_target"),
            operation=importlib.import_module("computer_use_linux.runtime.native_operation"),
            protocol=importlib.import_module("computer_use_linux.contracts.protocol"),
        )


@pytest.fixture
def scene(modules):
    target = {
        "session": "session",
        "instance": "instance",
        "rootWindow": "root",
        "surface": "surface",
        "node": "field",
        "capture": None,
    }
    state = {"route": True, "focused": True, "can_grab": True, "enabled": True}
    desktop = SimpleNamespace(
        can_input=False,
        keys=set(),
        buttons=set(),
        require_idle_input=Mock(),
        activate=Mock(),
        keycode=Mock(),
        press_key=Mock(),
        text_preflight=Mock(),
    )
    unit = object.__new__(modules.keyboard.NativeKeyboard)
    unit.desktop = desktop
    unit.ax = SimpleNamespace(begin=Mock())
    unit.store = SimpleNamespace(pins=set())

    def native_call(interface, method, obj):
        assert (interface, method, obj) == ("Component", "grab_focus", "field")
        state["focused"] = True

    unit.target = SimpleNamespace(context=modules.target.NativeTarget.context, call=Mock(side_effect=native_call))

    def receiver(*_args, **_kwargs):
        return dict(
            **state,
            ready=state["route"] and state["focused"] and state["enabled"],
            recipient=target,
            role="entry",
            obj="field",
            window="window",
        )

    unit.receiver = receiver
    context = modules.protocol.Request(time.monotonic() + 5, threading.Event())
    operation = modules.operation.NativeOperation(
        target, context.deadline, {"kind": "focus", "parameters": {}}, writable=True
    )
    return SimpleNamespace(unit=unit, target=target, state=state, desktop=desktop, context=context, operation=operation)


def prepare(scene):
    facts = scene.unit.inspect(scene.operation, scene.target, (), scene.context)
    primitive = facts["primitives"][0]
    assert primitive["unit"] == "prepare_receiver" and primitive["supported"] == "yes"
    assert primitive["globalInput"] == "no"
    result = scene.unit.execute(
        scene.operation,
        {"kind": "prepare_receiver", "primitiveId": primitive["id"], "target": scene.target, "foreground": False},
        scene.context,
    )
    assert result["confirmation"] == "confirmed" and result["errorCode"] is None
    assert result["dispatch"] == "notDispatched" and not scene.operation.dispatched
    scene.desktop.activate.assert_not_called()
    scene.desktop.press_key.assert_not_called()
    scene.desktop.keycode.assert_not_called()
    return result


def test_already_ready_focus_succeeds_without_xtest_or_preparation(scene):
    result = prepare(scene)

    assert result["preparation"] == []
    scene.unit.target.call.assert_not_called()
    scene.desktop.require_idle_input.assert_not_called()
    assert scene.context.effect == "none"


def test_native_focus_preparation_succeeds_without_xtest(scene):
    scene.state["focused"] = False

    result = prepare(scene)

    assert result["preparation"] == ["nativeFocus"]
    scene.unit.target.call.assert_called_once_with("Component", "grab_focus", "field")
    assert scene.state["focused"]


@pytest.mark.parametrize(
    "unit_name,type_name,kind,parameters",
    (
        ("keyboard", "NativeKeyboard", "key", {"key": "Return"}),
        ("typed", "NativeTypedText", "insert", {"text": "abc"}),
        ("typed", "NativeTypedText", "replace", {"text": "abc"}),
        ("paste", "NativePaste", "paste", {"text": "abc"}),
    ),
)
def test_actual_input_still_requires_xtest_before_preparation(modules, scene, unit_name, type_name, kind, parameters):
    module = getattr(modules, unit_name)
    unit = object.__new__(getattr(module, type_name))
    unit.desktop = scene.desktop
    operation = SimpleNamespace(intent={"kind": kind, "parameters": parameters})

    with pytest.raises(modules.protocol.Failure) as caught:
        unit.preflight(operation)

    assert caught.value.code == "unsupported_operation"
    scene.desktop.keycode.assert_not_called()
    scene.desktop.text_preflight.assert_not_called()
    scene.desktop.activate.assert_not_called()
    scene.unit.target.call.assert_not_called()


@pytest.mark.parametrize(
    "unit_name,type_name,kind,parameters,contract_unit,extra",
    (
        ("keyboard", "NativeKeyboard", "key", {"key": "Return"}, "key_sequence", {}),
        ("typed", "NativeTypedText", "insert", {"text": "abc"}, "key_sequence", {"typedText": True}),
        ("typed", "NativeTypedText", "replace", {"text": ""}, "key_sequence", {"typedText": True}),
        ("paste", "NativePaste", "paste", {"text": "abc"}, "clipboard_paste", {}),
    ),
)
@pytest.mark.parametrize("preparing", (False, True))
def test_global_input_denied_before_receiver_or_clipboard_preparation(
    modules, scene, unit_name, type_name, kind, parameters, contract_unit, extra, preparing
):
    unit = object.__new__(getattr(getattr(modules, unit_name), type_name))
    unit.desktop = scene.desktop
    unit.ax = SimpleNamespace(begin=Mock())
    unit.prepare = Mock()
    unit.guard = Mock()
    unit.preflight = Mock()
    scene.desktop.can_input = True  # Capability alone is not authorization.
    operation = modules.operation.NativeOperation(
        scene.target, scene.context.deadline, {"kind": kind, "parameters": parameters}, writable=True
    )
    primitive = operation.contract(contract_unit, scene.target, (), supported=True, **extra)
    result = unit.execute(
        operation,
        dict(
            kind="prepare_receiver" if preparing else contract_unit,
            primitiveId=primitive,
            target=scene.target,
            foreground=preparing,
        ),
        scene.context,
    )
    assert result["errorCode"] == "permission_required"
    assert result["dispatch"] == "notDispatched" and not operation.dispatched
    assert result["preparation"] == [] and scene.context.effect == "none"
    unit.prepare.assert_not_called()
    unit.guard.assert_not_called()
    unit.preflight.assert_not_called()
    unit.ax.begin.assert_not_called()
    scene.desktop.activate.assert_not_called()
    scene.desktop.press_key.assert_not_called()
