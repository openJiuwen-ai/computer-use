# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Exercise background-first preparation without a second desktop-mode pipeline."""

import importlib
import io
import json
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest


@pytest.fixture(scope="module")
def linux_modules():
    source = Path(__file__).resolve().parents[2] / "os_adapter/linux/src"
    with patch.object(sys, "path", [str(source), *sys.path]):
        return SimpleNamespace(
            keyboard=importlib.import_module("computer_use_linux.control.keyboard.pipeline"),
            desktop=importlib.import_module("computer_use_linux.control.native_desktop_input"),
            server=importlib.import_module("computer_use_linux.runtime.server"),
            protocol=importlib.import_module("computer_use_linux.contracts.protocol"),
        )


@pytest.fixture
def keyboard_scene(linux_modules):
    effects = []
    state = {"route": False, "focused": False, "can_grab": True}
    target = {"surface": "window", "node": "field"}
    contract = {"target": target, "within": (), "role": "entry"}

    def guard(*_):
        return dict(
            **state,
            ready=state["route"] and state["focused"],
            obj="field",
            window="window",
        )

    def activate(*_):
        effects.append("activation")
        state["route"] = True

    def focus(*args):
        assert args == ("Component", "grab_focus", "field")
        effects.append("nativeFocus")
        state["focused"] = True

    desktop = SimpleNamespace(require_idle_input=Mock(), activate=Mock(side_effect=activate))
    service = SimpleNamespace(call=Mock(side_effect=focus))
    pipeline = linux_modules.keyboard.NativePreparePipeline(guard, lambda *_: "route", desktop, service)
    # An expired observation window finishes immediately after one fresh query.
    # The request's check is substituted independently from that wait budget.
    ctx = SimpleNamespace(deadline=time.monotonic() - 1, check=Mock(), sent=Mock(), pause=Mock())
    operation = SimpleNamespace(background_unavailable=set())
    return SimpleNamespace(
        pipeline=pipeline,
        state=state,
        ctx=ctx,
        contract=contract,
        operation=operation,
        effects=effects,
        desktop=desktop,
    )


def test_keyboard_attempts_addressed_focus_before_requesting_foreground(keyboard_scene):
    s = keyboard_scene
    preparation = []
    assert not s.pipeline.run(s.operation, s.contract, False, s.ctx, preparation)
    assert preparation == ["nativeFocus"]
    assert s.operation.background_unavailable == {"route"}
    s.desktop.activate.assert_not_called()

    preparation = []
    assert s.pipeline.run(s.operation, s.contract, True, s.ctx, preparation)
    assert s.effects == ["nativeFocus", "activation"]
    assert preparation == ["foreground"]
    s.ctx.sent.assert_called_once()  # Only the addressed preparation; no requested key.


def test_ready_keyboard_needs_no_activation_or_preparation(keyboard_scene):
    s = keyboard_scene
    s.state.update(route=True, focused=True)
    assert s.pipeline.run(s.operation, s.contract, False, s.ctx, [])
    assert not s.effects
    s.desktop.require_idle_input.assert_not_called()
    s.ctx.sent.assert_not_called()


def test_requested_foreground_still_requires_background_exhaustion(keyboard_scene, linux_modules):
    s = keyboard_scene
    with pytest.raises(linux_modules.protocol.Failure, match="Background preparation has not been attempted"):
        s.pipeline.run(s.operation, s.contract, True, s.ctx, [])
    assert not s.effects
    s.ctx.sent.assert_not_called()


def test_impossible_background_focus_can_proceed_to_selected_activation(keyboard_scene):
    s = keyboard_scene
    s.state.update(can_grab=False, focused=True)
    preparation = []
    assert s.pipeline.run(s.operation, s.contract, True, s.ctx, preparation)
    assert preparation == ["foreground"]
    assert s.effects == ["activation"]


def test_desktop_keyboard_preserves_captured_scope_without_mode_flags(linux_modules):
    unit = object.__new__(linux_modules.desktop.NativeDesktopInput)
    unit.b = SimpleNamespace(ensure_unlocked=Mock())
    unit.desktop = SimpleNamespace(can_input=True)
    unit.captures = SimpleNamespace(keyboard_destination=Mock(return_value="originalReceiver"))
    target = dict(capture="capture", instance=None, rootWindow=None, surface=None, node=None)
    operation = SimpleNamespace(intent={"kind": "insert"})
    ctx = SimpleNamespace(check=Mock())
    assert unit.guard(operation, target, ctx) == "originalReceiver"
    target["node"] = "otherControl"
    with pytest.raises(linux_modules.protocol.Failure, match="captured desktop scope"):
        unit.guard(operation, target, ctx)
    unit.captures.keyboard_destination.assert_called_once()


@pytest.mark.parametrize("mode", ["shared", "restricted", ""])
def test_removed_modes_are_rejected_before_backend_initialization(linux_modules, mode):
    factory, output = Mock(), io.StringIO()
    server = linux_modules.server.Server(factory, source=io.BytesIO(), output=output)
    server.execute(
        dict(version=6, id=0, method="hello", params=dict(desktopMode=mode)),
        linux_modules.protocol.Request(time.monotonic() + 10, threading.Event()),
    )
    factory.assert_not_called()
    response = json.loads(output.getvalue())
    assert response["error"]["code"] == "invalid_request"
    assert response["error"]["effect"] == "none"
