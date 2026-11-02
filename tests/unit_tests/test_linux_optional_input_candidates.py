# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Optional candidate queries must not erase independent input contracts."""

import importlib
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from unit_tests.test_linux_explicit_actions import (
    ProviderError,
    native_modules,
    scene,
)

__all__ = ["native_modules", "scene"]


@pytest.fixture
def candidates(scene, native_modules):
    context = importlib.import_module("computer_use_linux.control.input_context.pipeline")
    model = importlib.import_module("computer_use_linux.control.input_context.model")
    pointer = importlib.import_module("computer_use_linux.control.native_element_pointer")
    bootstrap, _ = scene.prepare(kind="click")
    operation = native_modules.operation.NativeOperation(
        bootstrap.target, scene.ctx.deadline, bootstrap.intent, writable=True
    )
    operation.click_contract = "v1"
    target = scene.unit.target
    element_pointer = pointer.NativeElementPointer(target.b, target.store, target.directory, None)
    unused = object()
    units = model.ContextUnits(unused, unused, unused, unused, unused, unused, element_pointer, scene.unit)
    pipeline = context.NativeInputContextPipeline(units, {"nativeElementPointer"}, target.b.desktop)
    scope = SimpleNamespace(operation=operation, target=operation.target, request={"within": []})
    return SimpleNamespace(
        scene=scene,
        modules=native_modules,
        operation=operation,
        scope=scope,
        pipeline=pipeline,
        target=target,
        pointer=element_pointer,
    )


def metadata_failure(candidates, error, before_error=lambda: None):
    """Fail the optional pointer's role query, after action inspection passed."""
    accessible = candidates.target.ax.atspi.Accessible
    original = accessible.get_role_name
    reads = 0

    def role(obj):
        nonlocal reads
        reads += 1
        if reads == 2:
            before_error()
            raise error
        return original(obj)

    accessible.get_role_name = role


def unknown_optional_scope(candidates):
    """Make the original root temporarily unreadable during pointer inspection."""
    desktop = candidates.target.b.desktop
    original = desktop.validate
    window = original(None)
    calls = 0

    def validate(selected):
        nonlocal calls
        calls += 1
        if calls == 2:
            window.accessible = None
        return original(selected)

    desktop.validate = validate
    candidates.target.ax.matching_window = lambda *_: None
    candidates.target.ax.windows = list
    return window


def run_context(candidates):
    return candidates.pipeline.run(candidates.scope, candidates.scene.ctx)


def assert_independent_actions(candidates, result):
    assert [p["unit"] for p in result["primitives"]] == ["native_action", "native_action"]
    assert [p["action"]["name"] for p in result["primitives"]] == ["activate", "menu"]
    assert all(p["supported"] == "yes" and p["readiness"] == "yes" for p in result["primitives"])
    assert all(p["click"]["admission"] == "ready" for p in result["primitives"])
    assert result["actionsComplete"] is True and result["binding"] == candidates.operation.target
    assert "optional pointer" in result["unavailableReason"]
    assert candidates.scene.sent == [] and not candidates.operation.dispatched
    assert candidates.scene.ctx.effect == "none"


def dispatch_action(candidates, result):
    primitive = result["primitives"][0]
    command = {
        "kind": "native_action",
        "primitiveId": primitive["id"],
        "target": candidates.operation.target,
        "foreground": False,
    }
    return candidates.operation.step(
        1, command, lambda: candidates.scene.unit.execute(candidates.operation, command, candidates.scene.ctx)
    )


@pytest.mark.parametrize(
    "code", [None, "input_unavailable", "mode_unsupported", "observation_incomplete", "ambiguous_target"]
)
def test_optional_pointer_failure_retains_freshly_executable_actions(candidates, code):
    error = (
        ProviderError("optional role unavailable")
        if code is None
        else candidates.modules.protocol.Failure("optional role unavailable", code)
    )
    metadata_failure(candidates, error)

    result = run_context(candidates)

    assert_independent_actions(candidates, result)
    assert str(error) in result["unavailableReason"]
    receipt = dispatch_action(candidates, result)
    assert receipt["dispatch"] == "dispatched" and candidates.scene.sent == [(0, "activate")]


@pytest.mark.parametrize(
    "code",
    ["stale_target", "blocked_by_modal", "invalid_request", "deadline_exceeded", "cancelled", "execution_unknown"],
)
def test_optional_query_keeps_known_boundary_and_request_failures(candidates, code):
    error = candidates.modules.protocol.Failure("known failure", code)
    metadata_failure(candidates, error)

    with pytest.raises(candidates.modules.protocol.Failure) as caught:
        run_context(candidates)

    assert caught.value is error and candidates.scene.sent == []


@pytest.mark.parametrize("source", ["error", "request"])
@pytest.mark.parametrize("effect", ["possible", "partial"])
def test_optional_query_never_downgrades_effect_bearing_failure(candidates, source, effect):
    error = candidates.modules.protocol.Failure(
        "query has earlier effects", "input_unavailable", effect=effect if source == "error" else "none"
    )
    if source == "request":
        candidates.scene.ctx.effect = effect
    metadata_failure(candidates, error)

    with pytest.raises(candidates.modules.protocol.Failure) as caught:
        run_context(candidates)

    assert caught.value is error and candidates.scene.sent == []


@pytest.mark.parametrize("state,code", [("cancel", "cancelled"), ("expired", "deadline_exceeded")])
def test_raw_provider_error_cannot_hide_request_stop(candidates, state, code):
    def stop_request():
        if state == "cancel":
            candidates.scene.ctx.cancelled.set()
        else:
            candidates.scene.ctx.deadline = 0

    metadata_failure(candidates, ProviderError("optional role unavailable"), stop_request)

    with pytest.raises(candidates.modules.protocol.Failure) as caught:
        run_context(candidates)

    assert caught.value.code == code and candidates.scene.sent == []


@pytest.mark.parametrize("actions", [[], [{"name": "activate"}, {"name": "activate"}]])
def test_optional_failure_cannot_invent_an_independent_supported_route(candidates, actions):
    candidates.scene.actions = [dict(action, description="Same action") for action in actions]
    error = ProviderError("optional role unavailable")
    metadata_failure(candidates, error)

    with pytest.raises(ProviderError) as caught:
        run_context(candidates)

    assert caught.value is error and candidates.scene.sent == []


@pytest.mark.parametrize("final", ["restored", "unknown", "outside_scope", "modal", "destroyed"])
def test_optional_scope_unknown_does_not_replace_action_final_boundary(candidates, final):
    window = unknown_optional_scope(candidates)

    result = run_context(candidates)

    assert_independent_actions(candidates, result)
    if final != "unknown":
        window.accessible = candidates.scene.root
    if final == "outside_scope":
        candidates.scene.element.parent = None
    elif final == "modal":
        candidates.scene.modal = True
    elif final == "destroyed":
        candidates.scene.element.destroyed = True
    receipt = dispatch_action(candidates, result)
    if final == "restored":
        assert receipt["dispatch"] == "dispatched" and candidates.scene.sent == [(0, "activate")]
    else:
        assert receipt["dispatch"] == "notDispatched" and candidates.scene.sent == []
        assert receipt["errorCode"] == (
            "input_unavailable" if final == "unknown" else "blocked_by_modal" if final == "modal" else "stale_target"
        )


@pytest.mark.parametrize("query", ["pointer", "keyboard"])
@pytest.mark.parametrize("code", [None, "input_unavailable", "stale_target", "blocked_by_modal"])
def test_optional_candidate_failure_policy_is_shared(query, code, native_modules):
    module = importlib.import_module("computer_use_linux.control.input_context.passes.collect_" + query + "_contracts")
    error = (
        ProviderError("unrelated query failed")
        if code is None
        else native_modules.protocol.Failure("unrelated query failed", code)
    )
    unit = SimpleNamespace(
        ax=SimpleNamespace(glib=SimpleNamespace(Error=ProviderError)), inspect=Mock(side_effect=error)
    )
    result = {
        "primitives": [
            {
                "unit": "native_action" if query == "pointer" else "native_text_edit",
                "channel": "object",
                "supported": "yes",
            }
        ],
        "actionsComplete": True,
    }
    scope = SimpleNamespace(operation=object(), target=object(), request={"within": []})
    ctx = SimpleNamespace(check=Mock(), effect="none")
    collect = lambda: (
        module.collect_pointer_contracts(scope, result, ctx, unit)
        if query == "pointer"
        else module.collect_keyboard_contracts(scope, {}, result, ctx, unit)
    )
    if code in ("stale_target", "blocked_by_modal"):
        with pytest.raises(native_modules.protocol.Failure) as caught:
            collect()
        assert caught.value is error
    else:
        assert collect() is result
        assert "optional " + query in result["unavailableReason"]
        assert str(error) in result["unavailableReason"]
    ctx.check.assert_called_once()
    unit.inspect.assert_called_once()
