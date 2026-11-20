"""Raw input survives missing object capabilities; locators never prepare targets."""

import asyncio
from dataclasses import replace
from functools import partial
from types import SimpleNamespace

import pytest
from computer_use.client.computer import computer_for_controller
from computer_use.client.targets import Target, image_target
from computer_use.errors import ComputerUseError
from computer_use.interaction.global_input import GlobalInput
from computer_use.interaction.requests import ComputerInput
from computer_use.native.contracts import Binding, HostPolicy, Intent
from computer_use.native.transport import ConnectionPort
from computer_use.session.desktop_store import DesktopStore
from computer_use.session.image_coordinates import ImageCoordinates
from computer_use.session.results import action_result
from computer_use.session.windows import WindowReferences
from regression.test_image_gesture_scope import setup_pipeline
from regression.compatibility.global_input_fixtures import PrimitiveConnection
from regression.compatibility.test_public_sync_sdk import Controller


@pytest.fixture
def primitive_connection(tmp_path):
    return partial(PrimitiveConnection, tmp_path / "wire.jsonl")


def controller_for_connection(connection):
    return SimpleNamespace(port=ConnectionPort(connection, HostPolicy()), _action_result=action_result)


@pytest.mark.parametrize(
    ("kind", "parameters", "points"),
    [
        ("click", {"button": "left", "count": 1}, ((-200, 80),)),
        ("scroll", {"direction": "down", "pages": 1}, ((-300, -20),)),
        ("drag", {}, ((-200, 80), (100, -10))),
        ("insert", {"text": "hello"}, ()),
        ("key", {"key": "super+a"}, ()),
        ("paste", {"text": "one\ntwo"}, ()),
    ],
)
def test_raw_global_needs_no_read_or_object_contracts(kind, parameters, points, primitive_connection):
    connection = primitive_connection()
    controller = controller_for_connection(connection)
    result = connection.run(GlobalInput(controller).arun(ComputerInput(Intent(kind, parameters, True), points)))
    assert [method for method, _ in connection.requests] == ["begin", "execute", "finish"]
    begin, execute = connection.requests[0][1], connection.requests[1][1]
    assert execute["step"] == 1
    assert begin["target"] == {"session": "session", "global": True}
    assert "clickContract" not in begin
    assert execute["command"] == {
        "kind": "global_input",
        "primitiveId": "global",
        "target": begin["target"],
        "foreground": False,
    }
    assert result["status"] == "dispatched" and result["observation_status"] == "not_requested"
    assert result["observation"] is None
    assert result["impact"]["attempts"][0]["declared_global_input"] == "yes"
    assert not controller.port.calls.busy


@pytest.mark.parametrize(
    ("kind", "parameters"),
    [
        ("key", {"key": "ctrl+shift+Escape"}),
        ("insert", {"text": "输入😀"}),
        ("paste", {"text": "first\nsecond"}),
    ],
)
def test_windows_global_keyboard_does_not_require_a_desktop_capture(kind, parameters, primitive_connection):
    connection = primitive_connection(platform="windows")
    controller = controller_for_connection(connection)
    controller.desktop = DesktopStore(None)
    result = connection.run(GlobalInput(controller).arun(ComputerInput(Intent(kind, parameters, True))))
    assert [method for method, _ in connection.requests] == ["begin", "execute", "finish"]
    begin, execute = connection.requests[0][1], connection.requests[1][1]
    assert begin["target"] == {"session": "session", "global": True}
    assert begin["intent"] == {"kind": kind, "parameters": parameters, "globalInput": True}
    assert execute["command"] == {
        "kind": "global_input",
        "primitiveId": "global",
        "target": begin["target"],
        "foreground": False,
    }
    assert result["status"] == "dispatched" and result["observation_status"] == "not_requested"
    assert not controller.port.calls.busy


def test_old_windows_driver_fails_before_native_begin(primitive_connection):
    connection = primitive_connection(platform="windows", global_input=False)
    controller = controller_for_connection(connection)
    with pytest.raises(ComputerUseError, match="Rebuild the Windows driver") as caught:
        connection.run(GlobalInput(controller).arun(ComputerInput(Intent("key", {"key": "Return"}, True))))
    assert caught.value.code == "unsupported_operation" and caught.value.effect == "none"
    assert connection.requests == []


@pytest.mark.parametrize("kind", ["insert", "paste"])
def test_windows_empty_global_text_is_a_confirmed_noop(kind, primitive_connection):
    connection = primitive_connection(platform="windows", dispatch="notDispatched", confirmation="confirmed")
    controller = controller_for_connection(connection)
    result = connection.run(GlobalInput(controller).arun(ComputerInput(Intent(kind, {"text": ""}, True))))
    assert result["status"] == "verified"
    assert result["impact"]["attempts"][0]["dispatch"] == "notDispatched"


def test_raw_public_coordinates_ignore_previous_image_and_keyboard_has_no_target():
    class Recording(Controller):
        async def run(self, request):
            assert isinstance(request, ComputerInput)
            self.actions.append(request)
            return {"status": "dispatched"}

    controller = Recording()
    # There is deliberately no DesktopStore or screenshot in this session.
    with computer_for_controller(controller) as computer:
        computer.click(at=(-500, -10))
        computer.type_text("test")
        computer.press_key("Return")
        computer.paste("a\nb")
        assert controller.actions[0].points == ((-500, -10),)
        assert [request.intent.kind for request in controller.actions] == ["click", "insert", "key", "paste"]
        assert all(request.points == () for request in controller.actions[1:])


def test_global_element_location_has_no_focus_or_membership_checks(primitive_connection):
    connection = primitive_connection()
    controller = controller_for_connection(connection)
    binding = Binding("session", "app", "window", "surface", "original")
    target = Target(controller, object(), binding, _snapshot={"binding": binding}, _internal=True)
    result = connection.run(GlobalInput(controller).arun(ComputerInput(Intent("click", {}, True), (target,))))
    assert result["status"] == "dispatched"
    assert [method for method, _ in connection.requests] == ["locatePoint", "begin", "execute", "finish"]
    assert connection.requests[0][1]["point"] is None
    assert connection.requests[1][1]["intent"]["parameters"] == {"x": -90, "y": 24}


def test_global_drag_locates_independent_captures_without_same_image_gate(primitive_connection):
    connection = primitive_connection()
    controller = controller_for_connection(connection)
    targets = tuple(
        Target(controller, None, Binding("session", None, None, None, capture=token), _point=point, _internal=True)
        for token, point in (("first", (40, 80)), ("second", (210, 100)))
    )
    connection.run(GlobalInput(controller).arun(ComputerInput(Intent("drag", {}, True), targets)))
    assert [method for method, _ in connection.requests] == ["locatePoint", "locatePoint", "begin", "execute", "finish"]
    assert connection.requests[2][1]["intent"]["parameters"] == {"fromX": -260, "fromY": 120, "toX": -90, "toY": 140}


def test_retained_image_locator_uses_delivered_scaling_even_if_scope_is_unconfirmed(monkeypatch, primitive_connection):
    pipeline, ui, _, _, binding = setup_pipeline(monkeypatch)
    pipeline.controller.interfaces = SimpleNamespace(windows=WindowReferences())
    image = ui._image
    image.delivered_coordinates = ImageCoordinates((200, 80), (100, 40))
    image.expired = image.unavailable_mapping = True
    image.pieces = tuple(replace(piece, scope_confirmed=False) for piece in image.pieces)
    target = image_target(pipeline.controller, ui, (10, 15))
    connection = primitive_connection()
    controller = controller_for_connection(connection)
    connection.run(GlobalInput(controller).arun(ComputerInput(Intent("click", {}, True), (target,))))
    assert connection.requests[0][1]["target"]["capture"] == binding.capture
    assert connection.requests[0][1]["point"] == {"x": 20, "y": 30}
    assert connection.requests[1][1]["intent"]["parameters"] == {"x": -280, "y": 70}


@pytest.mark.parametrize("dispatch", ["partial", "unknown", "notDispatched"])
@pytest.mark.parametrize(("platform", "kind"), [("macos", "key"), ("macos", "click"), ("windows", "key")])
def test_failed_global_input_keeps_progress_and_never_replays(dispatch, platform, kind, primitive_connection):
    connection = primitive_connection(platform=platform, dispatch=dispatch)
    controller = controller_for_connection(connection)
    with pytest.raises(ComputerUseError) as caught:
        request = (
            ComputerInput(Intent("key", {"key": "Return"}, True))
            if kind == "key"
            else ComputerInput(Intent("click", {"button": "left", "count": 1}, True), ((-20, 30),))
        )
        connection.run(GlobalInput(controller).arun(request))
    assert [method for method, _ in connection.requests] == ["begin", "execute", "finish"]
    assert caught.value.impact["attempts"][0]["dispatch"] == dispatch
    assert caught.value.effect == {"partial": "partial", "unknown": "possible", "notDispatched": "none"}[dispatch]


def test_cleanup_failure_keeps_dispatched_input_and_retires_session(primitive_connection):
    connection = primitive_connection(cleanup="failed")
    controller = controller_for_connection(connection)
    with pytest.raises(ComputerUseError) as caught:
        connection.run(GlobalInput(controller).arun(ComputerInput(Intent("key", {"key": "Return"}, True))))
    assert caught.value.effect == "possible"
    assert caught.value.impact["attempts"][0]["dispatch"] == "dispatched"
    assert caught.value.impact["attempts"][0]["cleanup"] == "unknown"
    assert controller.port.calls.retired


def test_linux_global_paste_is_explicitly_unimplemented_without_object_preparation(primitive_connection):
    connection = primitive_connection(platform="linux")
    controller = controller_for_connection(connection)
    with pytest.raises(ComputerUseError, match="Computer.paste") as caught:
        connection.run(GlobalInput(controller).arun(ComputerInput(Intent("paste", {"text": "a\nb"}, True))))
    assert caught.value.code == "unsupported_operation"
    assert connection.requests == []


@pytest.mark.parametrize("platform", ["macos", "windows"])
def test_cancelled_input_finishes_once_without_replay_and_keeps_unknown_progress(platform, primitive_connection):
    connection = primitive_connection(platform=platform)
    controller = controller_for_connection(connection)

    async def interrupted(*args):
        raise asyncio.CancelledError()

    controller.port.execute_global = interrupted
    with pytest.raises(asyncio.CancelledError) as caught:
        connection.run(GlobalInput(controller).arun(ComputerInput(Intent("key", {"key": "Return"}, True))))
    assert [method for method, _ in connection.requests] == ["begin", "finish"]
    assert caught.value.impact["attempts"][0]["dispatch"] == "unknown"
    assert not controller.port.calls.busy
