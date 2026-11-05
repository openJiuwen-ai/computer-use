# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Retained destinations survive observation updates without crossing input scope."""

import asyncio
import gc
from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest
from computer_use import Target
from computer_use.client.computer import computer_for_controller
from computer_use.client.targets import image_target, target_request
from computer_use.errors import ComputerUseError
from computer_use.interaction.agent.requests import ActOnElement
from computer_use.interaction.requests import ComputerInput
from computer_use.native.contracts import Binding
from computer_use.session.image_coordinates import ImageCoordinates
from computer_use.session.windows import WindowReferences
from unit_tests.test_image_gesture_scope import capture, setup_pipeline
from unit_tests.test_sync_sdk import Controller


def test_explicit_global_and_element_input_reuse_one_binding():
    class Recording(Controller):
        async def run(self, request):
            if isinstance(request, (ActOnElement, ComputerInput)):
                self.requests.append(request)
            if isinstance(request, ComputerInput):
                return {"status": "dispatched"}
            return await super().run(request)

    controller = Recording()
    controller.requests = []
    original = Binding("session", "app", "window", "surface", "original")
    controller.current = {"binding": original, "container": False}
    with computer_for_controller(controller) as computer:
        element = computer.app("Editor").element("Field")
        target = element.target
        controller.current = {"binding": Binding("session", "app", "window", "surface", "new")}
        computer.click(target=element)
        computer.click(target=target)
        computer.type_text("value")
        computer.press_key("Return")
        element.click()
        global_requests = controller.requests[:-1]
        assert all(isinstance(request, ComputerInput) for request in global_requests)
        assert all(request.intent.global_input for request in global_requests)
        assert all(request.points[0]._binding == original for request in global_requests[:2])
        assert all(request.points == () for request in global_requests[2:])
        assert controller.requests[-1].global_input is False
        assert controller.requests[-1].snapshot["binding"] == original
        with pytest.raises(TypeError):
            computer.type_text("value", target=target, replace=True)
        with pytest.raises(TypeError):
            computer.press_key("Return", target=target)
        assert controller.resolutions == ["Field"]
        with pytest.raises(FrozenInstanceError):
            target._binding = controller.current["binding"]


def test_target_cannot_cross_sessions_or_silently_mix_coordinate_and_element_arguments():
    first, second = Controller(), Controller()
    first.current = {"binding": Binding("s", "a", "w", "surface", "node")}
    with computer_for_controller(first) as a, computer_for_controller(second) as b:
        target = a.app("Editor").element("Field").target
        with pytest.raises(ComputerUseError, match="another Computer"):
            b.click(target=target)
        with pytest.raises(ComputerUseError, match="exactly one"):
            a.click(at=(1, 2), target=target)
        with pytest.raises(ComputerUseError, match="start and end must differ"):
            a.drag(target, target)
        assert first.actions == second.actions == []


def test_spatial_target_freezes_delivered_transform_and_retains_capture(monkeypatch):
    pipeline, ui, write, queried, binding = setup_pipeline(monkeypatch)
    windows = WindowReferences()
    pipeline.controller.interfaces = SimpleNamespace(windows=windows)
    ui._image.delivered_coordinates = ImageCoordinates((200, 80), (100, 40))
    target = image_target(pipeline.controller, ui, (10, 15))
    assert isinstance(target, Target)
    assert binding.capture in windows.capture_references()
    # A later publication may resize or replace the current canvas. The saved
    # point still denotes the originally delivered image's (20, 30) pixels.
    ui._image.delivered_coordinates = ImageCoordinates((200, 80), (200, 80))
    ui._image.expired = True
    request = target_request(pipeline.controller, target, "click", {}, global_input=True)
    assert (
        asyncio.run(
            pipeline.run(
                request.owner,
                request.intent,
                request.points,
                request.options,
                None,
                global_input=request.global_input,
                image=request.image,
            )
        )
        == "dispatched"
    )
    executed = write.arun.call_args.args[0]
    assert executed.target == binding
    assert executed.intent.parameters == {"x": 20, "y": 30}
    assert executed.intent.global_input and queried == []
    del target
    gc.collect()
    assert not windows.capture_references()


def test_image_point_cannot_be_reinterpreted_as_a_keyboard_receiver(monkeypatch):
    pipeline, ui, write, _, _ = setup_pipeline(monkeypatch)
    pipeline.controller.interfaces = SimpleNamespace(windows=WindowReferences())
    target = image_target(pipeline.controller, ui, (20, 30))
    with pytest.raises(ComputerUseError, match="pointer input"):
        target_request(pipeline.controller, target, "insert", {"text": "x"}, global_input=True)
    assert not write.arun.called


def test_desktop_target_retains_original_scaled_map_across_reads_and_releases_it():
    from computer_use.session.desktop_store import DesktopImageAccess, DesktopStore

    desktop = DesktopStore(DesktopImageAccess(lambda *args: None, lambda: 0, lambda: False))
    windows = WindowReferences()
    controller = SimpleNamespace(desktop=desktop, interfaces=SimpleNamespace(windows=windows))
    first = {
        "desktopRef": "first",
        "binding": {"session": "session"},
        "screenshot": capture("first").image,
        "observation": {"displays": [{"captured": True, "rect": {"x": 0, "y": 0, "width": 100, "height": 80}}]},
    }
    desktop.record(first)
    desktop.delivered(first["screenshot"], ImageCoordinates((100, 80), (50, 40)))
    point = image_target(controller, None, (10, 15))
    desktop.record({**first, "desktopRef": "second", "screenshot": capture("second").image})
    other = image_target(controller, None, (10, 15))
    request = target_request(controller, point, "click", {}, global_input=True)
    assert request.target.capture == "first" and request.intent.parameters == {"x": 20, "y": 30}
    assert windows.capture_references() == {"first", "second"}
    with pytest.raises(ComputerUseError, match="same captured"):
        target_request(controller, point, "drag", {}, global_input=True, end=other)
    del point
    gc.collect()
    assert windows.capture_references() == {"second"}


def test_desktop_locator_uses_delivered_geometry_without_epoch_input_authority():
    from computer_use.session.desktop_store import DesktopImageAccess, DesktopStore

    epoch = [1]
    desktop = DesktopStore(DesktopImageAccess(lambda *args: None, lambda: epoch[0], lambda: True))
    controller = SimpleNamespace(desktop=desktop, interfaces=SimpleNamespace(windows=WindowReferences()))
    state = {
        "desktopRef": "image", "binding": {"session": "session"}, "screenshot": capture("image").image,
        "observation": {"displays": [{"captured": True, "rect": {"x": 0, "y": 0, "width": 100, "height": 80}}]},
    }
    desktop.record(state)
    desktop.delivered(state["screenshot"], ImageCoordinates((100, 80), (50, 40)))
    epoch[0] += 1
    target = image_target(controller, None, (10, 15))
    assert target._point == (20, 30) and target._binding.capture == "image"
    # Other platforms' existing captured input eligibility stays separate.
    with pytest.raises(ComputerUseError, match="not been returned"):
        desktop.check_coordinates(10, 15)


@pytest.mark.parametrize("global_input", [False, True])
def test_operation_scope_crosses_the_production_wire_schema(global_input):
    from computer_use.native.contracts import GlobalTarget, HostPolicy, Intent
    from computer_use.native.transport import ConnectionPort, encode_target
    from computer_use.protocol.execution import decode_begin

    port = ConnectionPort(SimpleNamespace(platform="macos", protocol_version=6, capabilities=()), HostPolicy())
    payload = port._intent_payload(Intent("click", {"button": "left", "count": 1}, global_input=global_input))
    target = GlobalTarget("s") if global_input else Binding("s", "a", "w", "surface", "node")
    decoded = decode_begin({"mode": "write", "intent": payload, "target": encode_target(target)})
    assert decoded["intent"]["globalInput"] is global_input
    if global_input:
        with pytest.raises(ValueError, match="global session scope"):
            decode_begin({
                "mode": "write", "intent": payload,
                "target": encode_target(Binding("s", "a", "w", "surface", "node")),
            })
    for platform in ("windows", "linux"):
        port.connection.platform = platform
        assert "globalInput" not in port._intent_payload(Intent("click", {}, global_input=True))


@pytest.mark.parametrize("invalid", [1, "true", None])
def test_operation_scope_is_not_coerced_on_wire(invalid):
    from computer_use.native.transport import encode_target
    from computer_use.protocol.execution import decode_begin
    from computer_use.protocol.schema import SchemaError

    with pytest.raises(SchemaError):
        decode_begin(
            {
                "mode": "write",
                "target": encode_target(Binding("s", "a", "w", "v", "n")),
                "intent": {"kind": "click", "parameters": {}, "globalInput": invalid},
            }
        )
