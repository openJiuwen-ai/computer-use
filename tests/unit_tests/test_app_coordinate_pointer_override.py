"""An explicit pointer request stays local to one App coordinate click."""

import hashlib
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from computer_use import ClickOption, ComputerUseError, Element
from computer_use.client.computer import computer_for_controller
from computer_use.interaction.agent.requests import UIPointer
from unit_tests.test_application_window_lifetimes import directory, image_for, ref, setup, surface
from unit_tests.test_image_gesture_scope import capture


def test_app_pointer_override_is_per_call_and_default_payload_is_unchanged():
    controller, app = setup()
    controller.run = AsyncMock(return_value="dispatched")
    assert app.click(at=(12, 34), force_pointer=True, window=ref(app, "a")) == "dispatched"
    request = controller.run.call_args.args[0]
    assert isinstance(request, UIPointer)
    assert request.options == {"button": "left", "count": 1, "pointerOnly": True}
    assert request.points == ((12, 34),) and request.window == ref(app, "a")
    assert request.global_input is False
    app.click(ClickOption(button="right"), at=(12, 34))
    assert controller.run.call_args.args[0].options == {"button": "right", "count": 1}
    app.click(at=(12, 34), force_pointer=False)
    assert controller.run.call_args.args[0].options == {"button": "left", "count": 1}


@pytest.mark.parametrize("value", [None, 0, 1, "true", [], {}])
def test_invalid_pointer_override_is_rejected_before_native_work(value):
    controller, app = setup()
    controller.run = AsyncMock()
    with pytest.raises(ComputerUseError, match="force_pointer must be a boolean"):
        app.click(at=(1, 2), force_pointer=value)
    controller.run.assert_not_called()


@pytest.mark.parametrize("platform", ["macos", "windows", "linux"])
def test_retained_point_override_preserves_binding_and_other_platform_wire(platform):
    controller, app = setup()
    controller.transport.platform = platform
    window = ref(app, "a")
    image = image_for("a", "original-a")
    captured = replace(capture("a"), target=image.pieces[0].image.target)
    image.png = captured.image
    image.pieces = (replace(image.pieces[0], image=captured),)
    image.digest = hashlib.sha256(image.png).digest()
    controller.interfaces.windows.retain_image(app._instance, image, 1)
    point = app.point((10, 20), window=window)
    other = image_for("b", "new-b")
    controller.interfaces.windows.retain_image(app._instance, other, 2)
    app._instance.delivered_image = other
    controller.reader.directory._query = AsyncMock(return_value=directory(surface("a"), surface("b"), focus="b"))
    writes = AsyncMock(return_value="dispatched")
    controller.interfaces.write = SimpleNamespace(arun=writes)
    assert app.click(at=point, window=window, force_pointer=True) == "dispatched"
    executed = writes.call_args.args[0]
    assert executed.target == captured.target
    assert executed.intent.global_input is False
    assert executed.intent.parameters.get("pointerOnly") is (True if platform == "macos" else None)
    assert executed.intent.parameters["x"] == 10 and executed.intent.parameters["y"] == 20


def test_computer_and_element_have_no_coordinate_pointer_override():
    controller, app = setup()
    controller.run = AsyncMock()
    computer = computer_for_controller(controller)
    with pytest.raises(TypeError, match="force_pointer"):
        computer.click(at=(1, 2), force_pointer=True)
    element = Element(controller, app, {}, _internal=True)
    with pytest.raises(TypeError, match="force_pointer"):
        element.click(force_pointer=True)
    controller.run.assert_not_called()
