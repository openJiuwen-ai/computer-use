"""The public App API selects windows without another handle or observation."""

import asyncio
import gc
import hashlib
import inspect
import weakref
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import computer_use
import pytest
from computer_use import App, ComputerUseError
from computer_use.client.computer import computer_for_controller
from computer_use.interaction.agent.requests import ResolveElement, UIKeyboard
from computer_use.interaction.agent.scope import request_scope, window_scope
from computer_use.interaction.query.instances.scope import adopt
from computer_use.interaction.read.views.publication import publish
from computer_use.native.contracts import Binding
from computer_use.session.events import observation_delivered, set_observation_handler
from computer_use.session.interfaces import WindowIdentity
from unit_tests.test_application_window_lifetimes import directory, image_for, ref, setup, surface
from unit_tests.test_image_gesture_scope import capture


def test_public_api_has_only_app_and_element_scopes():
    controller, app = setup()
    assert not hasattr(computer_use, "Window")
    assert not hasattr(computer_use, "UI")
    assert not hasattr(app, "window")
    assert not hasattr(computer_for_controller(controller), "window")
    assert not hasattr(controller.adapters, "create_window")
    assert not hasattr(app._instance, "views")
    for method in ("read", "element", "click", "scroll", "drag", "type_text", "paste", "press_key", "point"):
        parameter = inspect.signature(getattr(App, method)).parameters["window"]
        assert parameter.default is None and parameter.kind is inspect.Parameter.KEYWORD_ONLY


@pytest.mark.parametrize("value", [True, False, -1, "0", 1.5])
def test_window_parameters_reject_invalid_scalars_before_native_calls(value):
    controller, app = setup()
    controller.run = AsyncMock()
    calls = (
        lambda: app.read(window=value), lambda: app.element(0, window=value),
        lambda: app.click(at=(1, 2), window=value), lambda: app.scroll("up", at=(1, 2), window=value),
        lambda: app.drag((1, 2), (3, 4), window=value), lambda: app.type_text("a", window=value),
        lambda: app.paste("a", window=value), lambda: app.press_key("Return", window=value),
        lambda: app.point((1, 2), window=value),
    )
    for call in calls:
        with pytest.raises(ComputerUseError):
            call()
    controller.run.assert_not_called()


def test_scoped_name_lookup_without_public_read_keeps_element_on_app():
    controller, app = setup()
    controller.interfaces.read = AsyncMock(side_effect=AssertionError("no public read required"))

    async def locate(request, control):
        root = controller._query_root(request.owner)
        return {"binding": replace(root, node="field")}

    controller.interfaces.lookup = SimpleNamespace(arun=AsyncMock(side_effect=locate))
    field = app.element("Field", window=ref(app, "a"))
    assert field._owner is app and field._snapshot["binding"].root_window == "a"
    assert app._instance.lookup_context is None and app._instance.input_context is None
    assert controller.interfaces.lookup.arun.await_count == 1
    controller.interfaces.read.assert_not_called()


def test_desktop_only_app_reference_resolves_weak_ax_window_without_app_read():
    controller, app = setup(surface("a", accessible=False, parent_state="unknown"))
    alias = App(controller, {"selector": "Editor"}, _internal=True)
    controller.reader.directory.applications = AsyncMock(return_value={
        "session": "session", "stopReasons": [],
        "applications": [{"id": "editor", "displayName": "Editor", "isRunning": True,
                          "identifier": "editor", "path": "/Editor.app"}],
    })
    scope = asyncio.run(request_scope(controller, alias, ref(app, "a")))
    assert scope._window_identity == Binding("session", "owner:a", "a", "surface:a")
    assert alias._instance is app._instance


def test_same_element_ref_across_window_selection_and_other_app_read():
    controller, app = setup()
    a = WindowIdentity.of(app._instance.members["a"])
    original = {"binding": Binding("session", a.instance, a.window, a.surface, "field"), "window_identity": a}
    app._instance.refs["42"] = original
    first = app.element(42, window=ref(app, "a"))
    app._context = "b"
    publish(controller, app, "B", directory=app._instance.directory)
    assert app.element(42)._snapshot is first._snapshot is original
    with pytest.raises(ComputerUseError, match="outside"):
        app.element(42, window=ref(app, "b"))


@pytest.mark.parametrize("selector", ["Editor", "/Outside/Editor.app"])
def test_recreated_textual_app_retains_exact_delivered_element_without_discovery(selector):
    controller, source = setup()
    computer = computer_for_controller(controller)
    temporary = computer.app(selector)
    adopt(controller, temporary, source._instance.directory)
    identity = WindowIdentity.of(temporary._instance.members["a"])
    original = {
        "binding": Binding("session", identity.instance, identity.window, identity.surface, "field"),
        "window_identity": identity,
    }
    temporary._instance.refs["42"] = original
    retired_wrapper = weakref.ref(temporary)
    del temporary
    gc.collect()
    assert retired_wrapper() is None

    controller.reader.directory.applications = AsyncMock(side_effect=AssertionError("discovery must not rebind a ref"))
    assert computer.app(selector).element(42)._snapshot is original
    with pytest.raises(ComputerUseError, match="not observed"):
        computer.app("different selector").element(42)
    controller.transport.session = "next-session"
    with pytest.raises(ComputerUseError, match="not observed"):
        computer.app(selector).element(42)
    controller.reader.directory.applications.assert_not_called()


def test_window_context_delivery_survives_request_collection_and_other_window():
    controller, app = setup()
    scope = window_scope(controller, app, ref(app, "a"))
    scope._context = "a"
    set_observation_handler(lambda _event: None)
    try:
        pending = publish(controller, scope, "A", directory=app._instance.directory)
        identity = scope.identity
        del scope
        gc.collect()
        assert identity not in app._instance.window_contexts
        observation_delivered(pending)
        app._context = "b"
        observed_b = publish(controller, app, "B", directory=app._instance.directory)
        observation_delivered(observed_b)
        recreated = window_scope(controller, app, ref(app, "a"))
        assert recreated._input_context == identity
        assert app._instance.input_context.window == "b"
    finally:
        set_observation_handler(None)


def test_explicit_unobserved_keyboard_pins_window_before_refresh():
    controller, app = setup()
    controller.reader.directory._query = AsyncMock(return_value=directory(surface("a"), surface("b"), focus="b"))
    writes = AsyncMock(return_value="dispatched")
    controller.interfaces.write = SimpleNamespace(arun=writes)
    result = asyncio.run(controller.run(UIKeyboard(app, "press_key", {"key": "Return"}, ref(app, "a"))))
    assert result == "dispatched"
    assert writes.call_args.args[0].target.root_window == "a"
    assert app._instance.input_context is None


@pytest.mark.parametrize(
    "after_close, destination, error, query_count",
    [
        ("child_ended", "a", None, 1),
        ("child_unconfirmed", None, "observation_incomplete", 1),
        ("child_still_live", "editor", None, 1),
        ("root_ended", None, "stale_target", 0),
    ],
)
def test_explicit_keyboard_after_closing_a_delivered_child(after_close, destination, error, query_count):
    root, other = surface("a"), surface("b")
    child = surface("editor", parent="a")
    controller, app = setup(root, other, child)
    root_ref, child_ref = ref(app, "a"), ref(app, "editor")
    scope = window_scope(controller, app, child_ref)
    scope._context = "editor"
    publish(controller, scope, "Editor attached to A", directory=app._instance.directory)
    assert window_scope(controller, app, root_ref)._input_context == WindowIdentity.of(child)

    facts = {
        "child_ended": directory(root, other, focus="b", ended=(child,)),
        "child_unconfirmed": directory(root, other, focus="b", partial=True),
        "child_still_live": directory(root, other, child, focus="b"),
        "root_ended": directory(other, focus="b", ended=(root, child)),
    }[after_close]
    query = AsyncMock(return_value=facts)
    controller.reader.directory._query = query

    async def write(request, _control):
        if request.intent.kind == "closeWindow":
            assert request.target.root_window == "editor"
            adopt(controller, app, facts)
        return "dispatched"

    dispatch = AsyncMock(side_effect=write)
    controller.interfaces.write = SimpleNamespace(arun=dispatch)
    assert app.close_window(child_ref) == "dispatched"
    dispatch.reset_mock()

    if error is None:
        assert app.press_key("3", window=root_ref) == "dispatched"
        dispatch.assert_awaited_once()
        action = dispatch.call_args.args[0]
        assert action.target == Binding("session", f"owner:{destination}", destination, f"surface:{destination}")
        assert action.intent.global_input is False
    else:
        with pytest.raises(ComputerUseError) as caught:
            app.press_key("3", window=root_ref)
        assert caught.value.code == error
        dispatch.assert_not_awaited()
    assert query.await_count == query_count

    if after_close == "child_ended":
        dispatch.reset_mock()
        for target in (child_ref, None):
            with pytest.raises(ComputerUseError, match="no longer exists"):
                app.press_key("3", window=target)
        dispatch.assert_not_awaited()


@pytest.mark.parametrize("method", ["click", "scroll", "drag"])
def test_retained_point_reuses_selected_window_after_another_window_is_delivered(method):
    controller, app = setup()
    a, b = ref(app, "a"), ref(app, "b")
    image = image_for("a", "original-a")
    captured = replace(capture("a"), target=image.pieces[0].image.target)
    image.png = captured.image
    image.pieces = (replace(image.pieces[0], image=captured),)
    image.digest = hashlib.sha256(image.png).digest()
    controller.interfaces.windows.retain_image(app._instance, image, 1)
    start, end = app.point((10, 20), window=a), app.point((30, 40), window=a)
    other = image_for("b", "new-b")
    controller.interfaces.windows.retain_image(app._instance, other, 2)
    app._instance.delivered_image = other
    controller.reader.directory._query = AsyncMock(return_value=directory(surface("a"), surface("b"), focus="b"))
    writes = AsyncMock(return_value="dispatched")
    controller.interfaces.write = SimpleNamespace(arun=writes)
    if method == "click":
        result = app.click(at=start, window=a)
    elif method == "scroll":
        result = app.scroll("down", at=start, window=a)
    else:
        result = app.drag(start, end, window=a)
    assert result == "dispatched"
    executed = writes.call_args.args[0]
    assert executed.target == image.pieces[0].image.target
    assert executed.target.root_window == "a" and executed.target.capture == "original-a"
    assert executed.intent.global_input is False
    writes.reset_mock()
    with pytest.raises(ComputerUseError, match="original window binding changed"):
        app.click(at=start, window=b)
    writes.assert_not_awaited()


def test_other_application_window_is_not_an_app_switch():
    controller, app = setup()
    refs = controller.interfaces.windows.register_directory(directory(surface("foreign"), application="other"))
    with pytest.raises(ComputerUseError, match="does not belong"):
        asyncio.run(controller.run(ResolveElement(app, "Field", 0, int(next(iter(refs))))))
    assert app._instance.selector == "editor"


def test_helper_catalogue_uses_the_calling_apps_native_variant():
    controller, app = setup(surface("a"))
    number = ref(app, "a")
    helper = App(controller, {"selector": "helper"}, _internal=True)
    adopt(controller, helper, directory(surface("a", scope="helper"), application="helper"))
    assert ref(helper, "a") == number
    assert window_scope(controller, helper, number)._window_identity.instance == "owner:helper"
    assert window_scope(controller, app, number)._window_identity.instance == "owner:a"


def test_scoped_query_uses_confirmed_modal_without_reading_blocked_parent():
    sheet = {**surface("sheet", parent="a"), "modal": True}
    controller, app = setup(surface("a"), surface("b"), sheet)
    requested = []

    async def locate(request, control):
        root = controller._query_root(request.owner)
        requested.append(root.root_window)
        assert root.root_window == "sheet", "blocked parent is not an eligible name-query receiver"
        return {"binding": replace(root, node="save-button")}

    controller.interfaces.lookup = SimpleNamespace(arun=AsyncMock(side_effect=locate))
    found = app.element("Save", window=ref(app, "a"))
    assert found._owner is app and found._snapshot["binding"].root_window == "sheet"
    assert requested == ["sheet"]
    assert app._instance.lookup_context is None


def test_delivered_modal_updates_parent_context_without_a_retained_scope():
    sheet = {**surface("sheet", parent="a"), "modal": True}
    controller, app = setup(surface("a"), surface("b"), sheet)
    app._context = "sheet"
    publish(controller, app, "Sheet after A action", directory=app._instance.directory)
    scoped_a = window_scope(controller, app, ref(app, "a"))
    assert scoped_a._input_context == WindowIdentity.of(sheet)
    app._context = "b"
    publish(controller, app, "Unrelated B", directory=app._instance.directory)
    assert scoped_a._input_context == WindowIdentity.of(sheet)


def test_unknown_parent_does_not_make_an_independent_modal_the_query_receiver():
    child = {**surface("unknown", parent_state="unknown"), "modal": True}
    controller, app = setup(surface("a"), child)
    from computer_use.interaction.agent.scope import lookup_window

    assert lookup_window(window_scope(controller, app, ref(app, "a"))) == "a"


def test_app_owned_sheet_action_keeps_parent_recovery_data():
    from computer_use.interaction.requests import ExecuteAction
    from computer_use.interaction.write.request.observation import WriteWithObservation
    from computer_use.native.contracts import Intent
    from computer_use.pipeline import Contract

    sheet = {**surface("sheet", parent="a"), "modal": True}
    controller, app = setup(surface("a"), surface("b"), sheet)
    body = SimpleNamespace(contract=Contract(object, object), arun=AsyncMock(return_value={
        "status": "dispatched", "detail": "sheet dismissed",
    }))
    observed = WriteWithObservation(controller, body)
    observed.observe = AsyncMock(side_effect=lambda request, result, progress, session, related: related)
    target = Binding("session", sheet["instance"], "sheet", sheet["surface"], "dismiss")
    related = asyncio.run(observed.run_observed(ExecuteAction(app, Intent("click", {}), target), None))
    assert related.context_owner is None  # no public Window or ephemeral owner lifetime needed
    assert related.context_root == WindowIdentity.of(surface("a"))
    assert related.preferred(directory(surface("a"), surface("b"), focus="b"), "b") == "a"
