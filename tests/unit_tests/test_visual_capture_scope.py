# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Keep OCR collection scoped to reads that request visual references."""

from __future__ import annotations

import asyncio

import computer_use.client.computer as computer_module
import computer_use.native.transport as transport_module
import pytest
from computer_use.client.computer import App
from computer_use.native.contracts import Binding, HostPolicy
from computer_use.native.transport import ConnectionPort
from computer_use.session.capture_options import visual_text_scope
from computer_use.session.observation import CapturedTextDocument, lazy_observation


class _CaptureConnection:
    protocol_version = 6
    platform = "windows"
    session_identity = ("generation", "session")

    def __init__(self, capabilities):
        self.capabilities = frozenset(capabilities)
        self.sent = []

    async def request(self, method, params, options=None, *, decode_response=None):
        self.sent.append(dict(params))
        return {"kind": "capture", "binding": {"session": "session"}, "image": object(), "coordinates": {}}


def _port(capabilities):
    connection = _CaptureConnection(capabilities)
    port = ConnectionPort(connection, HostPolicy())
    port._operations["operation"] = (connection.session_identity, None)
    return connection, port


def test_capture_request_schema_accepts_visual_text():
    from computer_use.protocol.execution import decode_capture_request

    request = decode_capture_request(
        {
            "operation": "operation",
            "target": {
                "session": "session",
                "instance": "instance",
                "rootWindow": "window",
                "surface": "surface",
                "node": None,
                "capture": None,
            },
            "kind": "window",
            "maxDimension": 2048,
            "visualText": True,
        }
    )

    assert request["visualText"] is True


def test_capture_requests_ocr_only_for_a_visual_read(monkeypatch):
    target = Binding("session", None, None, "surface")
    monkeypatch.setattr(transport_module, "decode_binding", lambda value: target)
    monkeypatch.setattr(transport_module, "capture_metadata", lambda image: {"width": 10, "height": 10})
    connection, port = _port({"nativeVisualText"})

    async def run():
        await port.capture("operation", target, 2048, 1000)
        with visual_text_scope(True):
            await port.capture("operation", target, 2048, 1000)

    asyncio.run(run())

    assert "visualText" not in connection.sent[0]
    assert connection.sent[1]["visualText"] is True


def test_capture_ignores_ocr_requests_without_driver_support(monkeypatch):
    target = Binding("session", None, None, "surface")
    monkeypatch.setattr(transport_module, "decode_binding", lambda value: target)
    monkeypatch.setattr(transport_module, "capture_metadata", lambda image: {"width": 10, "height": 10})
    connection, port = _port({"nativeCapture"})

    async def run():
        with visual_text_scope(True):
            await port.capture("operation", target, 2048, 1000)

    asyncio.run(run())

    assert "visualText" not in connection.sent[0]


class _Executor:
    def register(self, controller):
        pass

    def run(self, operation):
        return asyncio.run(operation())


class _Transport:
    def __init__(self, capabilities):
        self.capabilities = frozenset(capabilities)


class _State:
    delivered_coordinates = None


def _fake_visual_app(monkeypatch, capabilities):
    base = lazy_observation(CapturedTextDocument('0 window "Designer"'), screenshot=b"source-png")
    views = []

    class Controller:
        _executor = _Executor()
        transport = _Transport(capabilities)

        def run(self, request):
            views.append(request.view)
            return base

        def state(self, owner):
            return _State()

    def fake_operation(factory):
        async def value():
            return factory()

        return value()

    monkeypatch.setattr(computer_module, "operation", fake_operation)
    app = object.__new__(App)
    app._controller = Controller()
    app._executor = _Executor()
    app._window_title = None
    app._visual = None
    app._visual_frame = None
    app._visual_window = None
    return app, views


@pytest.mark.parametrize("visual", ["auto", "always"])
def test_unsupported_platform_does_not_request_an_extra_image(monkeypatch, visual):
    app, views = _fake_visual_app(monkeypatch, {"nativeCapture"})

    app.read(view="tree", visual=visual)

    assert views == ["tree"]
    if visual == "auto":
        assert app._visual is None
    else:
        assert app._visual is not None


def test_forced_visual_without_driver_support_reports_an_empty_table(monkeypatch):
    app, views = _fake_visual_app(monkeypatch, {"nativeCapture"})

    observation = app.read(view="tree", visual="always")

    assert views == ["tree"]
    assert observation.visual is not None
    assert observation.visual.elements == ()
    assert "currently Windows" in observation.visual.detail
    assert observation.get("marked") is None
