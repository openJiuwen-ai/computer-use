# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio
import importlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.fixture
def workflow(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "manual"))
    return importlib.import_module("windows_owned_window_capture_workflow")


def test_passive_capture_click_checks_same_physical_point_before_begin_and_execute(workflow, monkeypatch):
    requests = []

    async def send(method, params, options):
        requests.append(method)
        return {"operation": "write", "target": params.get("target")}

    checks = []
    monkeypatch.setattr(workflow, "require_reachable_point", lambda expected, point: checks.append((expected, point)))
    connection = SimpleNamespace(_child=None, session_identity=None, session="session", request=send)
    fixture = {"hwnd": 44}
    gate = workflow.OwnedCaptureRequests(connection, fixture, "driver.exe", natural=True)
    gate.binding = {"session": "session", "rootWindow": "root", "capture": None}
    gate.captures["cap"] = (840, 430)
    gate.arm_clicks(
        {"capture": "cap", "native_dimensions": [840, 430], "coordinates": {"toNative": [1, 0, 0, 1, -70, 90]}}
    )
    begin = {
        "mode": "write",
        "target": gate.click_target,
        "intent": {"kind": "click", "parameters": {"button": "left", "count": 1, "x": 22, "y": 30}},
    }
    command = {"kind": "pointer_sequence", "primitiveId": "point", "target": gate.click_target, "foreground": False}

    async def exercise():
        with pytest.raises(RuntimeError, match="refused observation preparation"):
            await gate.request("prepareObservation", {})
        await gate.request("begin", begin)
        with pytest.raises(RuntimeError, match="its foreground preparation"):
            await gate.request("execute", {"operation": "write", "step": 1, "command": {**command, "foreground": True}})
        await gate.request("execute", {"operation": "write", "step": 1, "command": command})
        with pytest.raises(RuntimeError, match="changed the owned click"):
            await gate.request("execute", {"operation": "write", "step": 1, "command": command})

    asyncio.run(exercise())
    assert checks == [(fixture, (-48, 120)), (fixture, (-48, 120))]
    assert requests == ["begin", "execute"]
    assert gate.approved_clicks == 1 and gate.denied_actions == 3


def test_prepared_capture_click_requires_current_offer_for_exact_lease(workflow, monkeypatch):
    connection = SimpleNamespace(_child=None, session_identity=None, session="session")
    calls = []
    fixture = {"hwnd": 44}
    monkeypatch.setattr(workflow, "require_reachable_point", Mock())

    async def send(method, params, _options):
        calls.append(method)
        if method == "begin":
            return {"operation": "write", "target": params["target"]}
        if method == "inspect":
            return {
                "binding": params["target"],
                "primitives": [
                    {
                        "id": "prepared",
                        "unit": "pointer_sequence",
                        "click": {
                            "routeFamily": "windows.sendInput",
                            "admission": "preparationRequired",
                            "preparationOptions": ["foreground"],
                        },
                    }
                ],
            }
        return {"dispatch": "dispatched"}

    connection.request = send
    gate = workflow.OwnedCaptureRequests(connection, fixture, "driver.exe", prepare_foreground=True)
    gate.binding = {"session": "session", "rootWindow": "root", "capture": None}
    gate.captures["cap"] = (840, 430)
    gate.arm_clicks(
        {"capture": "cap", "native_dimensions": [840, 430], "coordinates": {"toNative": [1, 0, 0, 1, 5, 8]}}
    )
    intent = {"kind": "click", "parameters": {"button": "left", "count": 1, "x": 22, "y": 30}}
    command = {"kind": "pointer_sequence", "primitiveId": "prepared", "target": gate.click_target, "foreground": True}

    async def exercise():
        await gate.request("begin", {"mode": "write", "target": gate.click_target, "intent": intent})
        with pytest.raises(RuntimeError, match="lacks the exact foreground preparation offer"):
            await gate.request("execute", {"operation": "write", "step": 1, "command": command})
        await gate.request(
            "inspect", {"operation": "write", "target": gate.click_target, "query": {"kind": "inputContext"}}
        )
        with pytest.raises(RuntimeError, match="its foreground preparation"):
            await gate.request(
                "execute", {"operation": "write", "step": 1, "command": {**command, "foreground": False}}
            )
        await gate.request("execute", {"operation": "write", "step": 1, "command": command})

    asyncio.run(exercise())
    assert calls == ["begin", "inspect", "execute"]
    assert gate.approved_clicks == 1 and gate.denied_actions == 2
    assert gate.click_attempts[0]["dispatch"] == "dispatched"


@pytest.mark.parametrize("active,visible,root", [(99, True, 44), (99, False, 99), (99, False, 44), (44, True, 44)])
def test_passive_hit_test_rejects_visible_bystander_or_nonowned_point(workflow, monkeypatch, active, visible, root):
    dpi = Mock(side_effect=[8, 9])
    point = Mock(return_value=45)
    user = SimpleNamespace(
        GetForegroundWindow=Mock(return_value=active),
        IsWindowVisible=Mock(return_value=visible),
        WindowFromPoint=point,
        GetAncestor=Mock(return_value=root),
        SetThreadDpiAwarenessContext=dpi,
    )
    monkeypatch.setattr(workflow, "require_owned_window", Mock())
    monkeypatch.setattr(workflow.ctypes, "WinDLL", Mock(return_value=user), raising=False)
    fixture = {"hwnd": 44}
    if active != 44 and visible:
        with pytest.raises(RuntimeError, match="hidden foreground"):
            workflow.require_reachable_point(fixture, (7, 11))
        point.assert_not_called()
        dpi.assert_not_called()
        return
    if root == 44:
        workflow.require_reachable_point(fixture, (7, 11))
    else:
        with pytest.raises(RuntimeError, match="exact owned window"):
            workflow.require_reachable_point(fixture, (7, 11))
    assert (point.call_args.args[0].x, point.call_args.args[0].y) == (7, 11)
    assert dpi.call_args.args == (8,)
    workflow.require_owned_window.assert_called_once_with(fixture, foreground=False)
