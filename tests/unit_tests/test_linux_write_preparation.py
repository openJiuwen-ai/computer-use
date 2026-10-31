# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Exercise Linux's real element-pointer stages with only OS queries substituted."""

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest


@pytest.fixture(scope="module")
def native_pointer_module():
    source = Path(__file__).resolve().parents[2] / "os_adapter/linux/src"
    with patch.object(sys, "path", [str(source), *sys.path]):
        return importlib.import_module("computer_use_linux.control.native_element_pointer")


@pytest.fixture
def scene(native_pointer_module):
    module = native_pointer_module
    unit = object.__new__(module.NativeElementPointer)
    native = SimpleNamespace(accessible="window", override_redirect=False)
    selected = object()
    current = SimpleNamespace(hit="window", object="target", after_activation="target", foreground=False)
    unit.ax = SimpleNamespace(native_key=lambda obj: obj, glib=SimpleNamespace(Error=RuntimeError))
    unit.b = SimpleNamespace()
    unit.desktop = SimpleNamespace(can_input=True, require_idle_input=Mock())
    unit.directory = SimpleNamespace(surfaces={"surface": selected})
    unit.store = SimpleNamespace(get=lambda *_: SimpleNamespace(obj="target"), keep=lambda _, obj: obj, pins=set())
    unit.target = SimpleNamespace(validate_scope=Mock(), context=lambda *args: {"primitives": args[2], "input": {}})
    unit.read_target = lambda *_: (current.object, None, (), "canvas", True, selected, native)
    unit.query_geometry = lambda *_args, **_kwargs: (((50, 60),), "frame", current.hit)

    def require_hit(*_):
        if not current.foreground:
            raise module.Failure("Window is covered", "input_unavailable")

    def activate(*_):
        current.foreground = True
        current.hit = current.after_activation

    unit.desktop.require_hit = require_hit
    unit.desktop.activate = Mock(side_effect=activate)
    target = {"surface": "surface", "node": "target"}
    contract = {"target": target, "within": (), "object": "target", "role": "canvas", "gesture": None}
    operation = SimpleNamespace(
        intent={"kind": "click", "parameters": {}}, bind=Mock(), contract=Mock(return_value="id")
    )
    return SimpleNamespace(
        unit=unit,
        current=current,
        target=target,
        contract=contract,
        operation=operation,
        ctx=SimpleNamespace(check=Mock()),
        failure=module.Failure,
    )


@pytest.mark.parametrize("kind", ["click", "scroll"])
def test_window_root_preserves_geometry_and_can_prepare_before_precise_hit(scene, kind):
    scene.operation.intent = {"kind": kind, "parameters": {}}
    facts = scene.unit.inspect(scene.operation, scene.target, (), scene.ctx)
    assert facts["input"]["geometryCurrent"] == "yes"
    primitive = facts["primitives"][0]
    assert primitive["supported"] == "yes" and primitive["pending"] == ["foreground"]
    assert not scene.unit.click_state(scene.operation, scene.contract, scene.ctx).ready
    preparation = []
    assert scene.unit.prepare(scene.operation, scene.contract, True, scene.ctx, preparation)
    assert preparation == ["foreground"]
    scene.unit.desktop.activate.assert_called_once()
    assert scene.unit.guard(scene.operation, scene.contract, scene.ctx)[0] == ((50, 60),)
    assert scene.contract["gesture"] == "frame"


def test_root_hit_never_authorizes_dispatch_or_unrequested_activation(scene):
    assert not scene.unit.prepare(scene.operation, scene.contract, False, scene.ctx, [])
    with pytest.raises(scene.failure):
        scene.unit.guard(scene.operation, scene.contract, scene.ctx)
    scene.unit.desktop.activate.assert_not_called()
    assert scene.contract["gesture"] is None


@pytest.mark.parametrize("after", ["window", "sibling"])
def test_preparation_must_obtain_fresh_member_hit(scene, after):
    scene.current.after_activation = after
    with pytest.raises(scene.failure):
        scene.unit.prepare(scene.operation, scene.contract, True, scene.ctx, [])
    assert scene.contract["gesture"] is None


def test_known_sibling_cannot_acquire_foreground_to_guess_a_target(scene):
    scene.current.hit = "sibling"
    facts = scene.unit.inspect(scene.operation, scene.target, (), scene.ctx)
    assert "geometry" in facts["primitives"][0]["pending"]
    assert facts["input"]["geometryCurrent"] == "yes"
    with pytest.raises(scene.failure):
        scene.unit.prepare(scene.operation, scene.contract, True, scene.ctx, [])
    scene.unit.desktop.activate.assert_not_called()


def test_target_change_during_activation_is_checked_before_dispatch(scene):
    activate = scene.unit.desktop.activate.side_effect

    def replace_target(*args):
        activate(*args)
        scene.current.object = "replacement"

    scene.unit.desktop.activate.side_effect = replace_target
    with pytest.raises(scene.failure) as caught:
        scene.unit.prepare(scene.operation, scene.contract, True, scene.ctx, [])
    assert caught.value.code == "stale_target"
    assert scene.contract["gesture"] is None


def test_ready_pointer_does_not_prepare_keyboard_or_foreground(scene):
    scene.current.hit, scene.current.foreground = "target", True
    assert scene.unit.click_state(scene.operation, scene.contract, scene.ctx).ready
    assert scene.unit.prepare(scene.operation, scene.contract, False, scene.ctx, [])
    scene.unit.desktop.activate.assert_not_called()


def test_unmanaged_popup_cannot_be_activated_for_a_coarse_hit(scene):
    scene.unit.read_target(scene.target, (), scene.ctx)[-1].override_redirect = True
    facts = scene.unit.inspect(scene.operation, scene.target, (), scene.ctx)
    assert "geometry" in facts["primitives"][0]["pending"]
    with pytest.raises(scene.failure):
        scene.unit.prepare(scene.operation, scene.contract, True, scene.ctx, [])
    scene.unit.desktop.activate.assert_not_called()
