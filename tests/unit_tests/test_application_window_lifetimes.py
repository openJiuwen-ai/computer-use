"""Application discovery never changes explicitly bound input/window lifetimes."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from computer_use.client.computer import App, computer_for_controller
from computer_use.client.factory import session_controller
from computer_use.errors import ComputerUseError
from computer_use.interaction.query.instances.scope import adopt, display_selection, validate_session
from computer_use.interaction.read.views.publication import publish
from computer_use.native.contracts import Binding, Knowledge
from computer_use.native.scopes import RunningApplication
from computer_use.session.events import observation_delivered, set_observation_handler
from computer_use.session.interfaces import WindowIdentity, WindowLifetime
from computer_use.session.state import AppState
from computer_use.session.windows import WindowReferences


def surface(key, *, scope=None, parent=None, accessible=True, parent_state=None):
    return dict(
        window=key,
        instance=f"owner:{scope or key}",
        surface=f"surface:{scope or key}",
        node=f"node:{key}" if accessible else None,
        title="same title",
        kind="window",
        parent=parent,
        parentState=parent_state or ("known" if parent else "none"),
        modal=False,
        nativeWindow=True,
        accessibility=accessible,
    )


def directory(*items, focus="a", partial=False, application="editor"):
    return dict(
        kind="surfaces",
        session="session",
        platform="macos",
        application=application,
        observed=dict(id="directory", startedAt=1, completedAt=2),
        surfaces=list(items),
        focusedWindow=focus,
        stopReasons=["incomplete"] if partial else [],
        rootDiscoveryComplete=not partial,
        provider=dict(status="alreadyEnabled", attribute=None, writeStatus=None),
    )


def setup(*items):
    port = SimpleNamespace(
        connection=SimpleNamespace(
            session="session", platform="macos", capabilities=(), session_identity=(1, "session")
        ),
        calls=SimpleNamespace(retired=False),
    )
    controller = session_controller(port)
    app = App(
        controller,
        dict(
            selector="editor",
            _nativeState=AppState("editor", running=RunningApplication("editor", "Editor", "session")),
        ),
        _internal=True,
    )
    facts = directory(*(items or (surface("a"), surface("b"))))
    adopt(controller, app, facts)
    app._instance.anchor = display_selection(controller, app._instance, facts)
    return controller, app


def ref(app, key):
    return next(number for number, window in app._instance.windows.items() if window == key)


def test_global_and_application_window_use_same_fixed_handle_without_focus_query():
    controller, app = setup()
    computer = computer_for_controller(controller)
    a = app.window(ref(app, "a"))
    b = computer.window(ref(app, "b"))
    assert computer.window(ref(app, "a")) is a
    assert app.window(ref(app, "b")) is b
    assert a._window_identity == Binding("session", "owner:a", "a", "surface:a", None)
    assert b._window_identity == Binding("session", "owner:b", "b", "surface:b", None)
    before = a._window_identity
    updated = directory(surface("a"), surface("b"), focus="b")
    adopt(controller, app, updated)
    app._instance.anchor = display_selection(controller, app._instance, updated)
    validate_session(controller, a)
    assert a._window_identity == before
    assert app._instance.anchor.root.root_window == "b"
    assert controller.state(app).selection is None


def test_closing_display_primary_keeps_app_and_independent_window_alive():
    controller, app = setup()
    a, b = app.window(ref(app, "a")), app.window(ref(app, "b"))
    old_ref = ref(app, "a")
    identity = WindowIdentity.of(app._instance.members["a"])
    app._instance.input_context = identity
    app._instance.observed.update(("a", "b"))
    snapshot = dict(binding=Binding("session", "owner:a", "a", "surface:a", "node:a"), window_identity=identity)
    adopt(controller, app, directory(surface("b"), focus="b"))
    validate_session(controller, app)
    validate_session(controller, b)
    for operation in (
        lambda: validate_session(controller, a),
        lambda: app.window(old_ref),
        lambda: controller.interfaces.validate_snapshot(app, snapshot),
        lambda: controller.interfaces.keyboard_context(app),
    ):
        with pytest.raises(ComputerUseError) as caught:
            operation()
        assert caught.value.code == "stale_target"
    assert app._instance.evidence[identity].lifetime is WindowLifetime.RETIRED


def test_independent_element_binding_survives_app_display_primary_changes():
    controller, app = setup()
    identity = WindowIdentity.of(app._instance.members["a"])
    snapshot = dict(binding=Binding("session", "owner:a", "a", "surface:a", "node:a"), window_identity=identity)
    updated = directory(surface("a"), surface("b"), focus="b")
    adopt(controller, app, updated)
    app._instance.anchor = display_selection(controller, app._instance, updated)
    controller.interfaces.validate_snapshot(app, snapshot)
    assert snapshot["binding"].surface == "surface:a"


def test_weak_ax_unknown_parent_window_is_selectable_and_has_own_native_binding():
    controller, app = setup(surface("a", accessible=False, parent_state="unknown"), surface("b"))
    app._instance.anchor = None
    window = computer_for_controller(controller).window(ref(app, "a"))
    validate_session(controller, window)
    assert window._window_identity == Binding("session", "owner:a", "a", "surface:a", None)
    assert window._anchor == "a"


def test_app_window_rejects_other_application_even_same_name():
    controller, app = setup()
    other = directory(surface("c"), application="other")
    refs = controller.interfaces.windows.register_directory(other, "Editor")
    with pytest.raises(ComputerUseError, match="does not belong"):
        app.window(int(next(iter(refs))))


def test_helper_scope_alias_preserves_original_window_binding():
    controller, app = setup(surface("a"))
    number = ref(app, "a")
    first = app.window(number)
    helper = directory(surface("a", scope="helper"), application="helper")
    refs = controller.interfaces.windows.register_directory(helper, "Editor")
    assert int(next(iter(refs))) == number
    assert controller.interfaces.windows.resolve(number, "session").identity == WindowIdentity.of(surface("a"))
    assert computer_for_controller(controller).window(number) is first
    assert first._window_identity.instance == "owner:a"


def test_retired_physical_alias_is_not_reused_for_new_owner_scope():
    controller, app = setup(surface("a"))
    number = ref(app, "a")
    old = app.window(number)
    adopt(controller, app, directory(focus=None))
    helper = directory(surface("a", scope="new-process"), application="helper")
    fresh = int(next(iter(controller.interfaces.windows.register_directory(helper))))
    assert fresh != number
    with pytest.raises(ComputerUseError):
        validate_session(controller, old)
    with pytest.raises(ComputerUseError):
        computer_for_controller(controller).window(number)


def test_new_lifetime_in_same_native_slot_has_new_reference():
    registry = WindowReferences()
    first = registry.register_directory(directory(surface("a")))
    changed = registry.register_directory(directory(surface("a", scope="replacement")))
    assert set(first) != set(changed)
    assert registry.resolve(int(next(iter(first))), "session").identity.surface == "surface:a"


def test_keyboard_pins_delivered_context_before_refresh_and_never_follows_focus():
    controller, app = setup()
    app._instance.input_context = WindowIdentity.of(app._instance.members["a"])
    app._instance.observed.update(("a", "b"))
    controller.reader.directory._query = AsyncMock(return_value=directory(surface("a"), surface("b"), focus="b"))
    dispatch = AsyncMock(return_value="sent")
    controller.interfaces.write = SimpleNamespace(arun=dispatch)
    assert asyncio.run(controller.interfaces.keyboard(app, "type_text", {"text": "one"}, None)) == "sent"
    target = dispatch.call_args.args[0].target
    assert target == Binding("session", "owner:a", "a", "surface:a", None)
    controller.reader.directory._query.assert_awaited_once()
    dispatch.assert_awaited_once()


def test_partial_missing_delivered_target_can_recover_before_input():
    controller, app = setup()
    identity = WindowIdentity.of(app._instance.members["a"])
    app._instance.input_context = identity
    app._instance.observed.add("a")
    adopt(controller, app, directory(surface("b"), focus="b", partial=True))
    assert app._instance.evidence[identity].membership is Knowledge.UNKNOWN
    controller.reader.directory._query = AsyncMock(return_value=directory(surface("a"), surface("b"), focus="b"))
    dispatch = AsyncMock(return_value="sent")
    controller.interfaces.write = SimpleNamespace(arun=dispatch)
    asyncio.run(controller.interfaces.keyboard(app, "type_text", {"text": "one"}, None))
    assert dispatch.call_args.args[0].target.root_window == "a"


def test_target_disappearing_during_refresh_cannot_fall_back_to_other_document():
    controller, app = setup()
    app._instance.input_context = WindowIdentity.of(app._instance.members["a"])
    app._instance.observed.add("a")
    controller.reader.directory._query = AsyncMock(return_value=directory(surface("b"), focus="b"))
    dispatch = AsyncMock()
    controller.interfaces.write = SimpleNamespace(arun=dispatch)
    with pytest.raises(ComputerUseError) as caught:
        asyncio.run(controller.interfaces.keyboard(app, "type_text", {"text": "one"}, None))
    assert caught.value.code == "stale_target"
    dispatch.assert_not_awaited()


def test_only_actual_delivery_changes_shared_app_input_context():
    controller, app = setup()
    events = []
    set_observation_handler(events.append)
    try:
        app._context = "a"
        first = publish(controller, app, "A", None, directory=app._instance.directory)
        app._context = "b"
        second = publish(controller, app, "B", None, directory=app._instance.directory)
        assert app._instance.input_context is None
        observation_delivered(first)
        assert app._instance.input_context.window == "a"
        observation_delivered(second)
        assert app._instance.input_context.window == "b"
        observation_delivered(first)
        assert app._instance.input_context.window == "b"
    finally:
        set_observation_handler(None)
    app._context = "a"
    publish(controller, app, "A", None, directory=app._instance.directory)
    assert app._instance.input_context.window == "a"


def test_window_read_delivery_updates_owning_application_context_without_rebinding():
    controller, app = setup()
    window = app.window(ref(app, "b"))
    window._context = "b"
    publish(controller, window, "B", None, directory=app._instance.directory)
    assert app._instance.input_context.window == "b"
    assert window._window_identity.root_window == "b"


def test_exact_single_window_image_reuse_keeps_delivery_expiry_object():
    controller, app = setup(surface("a"))
    identity = WindowIdentity.of(app._instance.members["a"])
    target = Binding("session", "owner:a", "a", "surface:a", None, capture="capture")
    piece = SimpleNamespace(window="a", image=SimpleNamespace(target=target, image=b"png"), box=(0, 0, 100, 80))
    image = SimpleNamespace(expired=False, unavailable_mapping=False, pieces=(piece,), png=b"png", delivered=7)
    with patch("computer_use.protocol.captures.capture_metadata", return_value={"width": 100, "height": 80}):
        controller.interfaces.windows.retain_image(app._instance, image)
    window = computer_for_controller(controller).window(ref(app, "a"))
    assert window._image is image
    assert app._instance.window_images[identity] is image
    image.expired = True
    assert window._image.expired


def test_composite_or_reframed_pixels_are_not_shared_as_window_coordinates():
    controller, app = setup(surface("a"))
    target = Binding("session", "owner:a", "a", "surface:a", None, capture="capture")
    for box, png, count in [((3, 0, 100, 80), b"png", 1), ((0, 0, 100, 80), b"other", 1), ((0, 0, 100, 80), b"png", 2)]:
        piece = SimpleNamespace(window="a", image=SimpleNamespace(target=target, image=b"png"), box=box)
        image = SimpleNamespace(expired=False, unavailable_mapping=False, pieces=(piece,) * count, png=png)
        with patch("computer_use.protocol.captures.capture_metadata", return_value={"width": 100, "height": 80}):
            controller.interfaces.windows.retain_image(app._instance, image)
        assert not app._instance.window_images


def test_fixed_window_keyboard_uses_delivered_modal_without_following_app_context():
    child = {**surface("sheet", parent="a"), "modal": True}
    controller, app = setup(surface("a"), surface("b"), child)
    app._instance.observed.update(("a", "b", "sheet"))
    window = app.window(ref(app, "a"))
    original = window._window_identity
    window._context = "sheet"
    publish(controller, window, "attached sheet", None, directory=app._instance.directory)
    app._context = "b"
    publish(controller, app, "unrelated B", None, directory=app._instance.directory)
    target = controller.interfaces.keyboard_context(window)
    assert target == Binding("session", "owner:sheet", "sheet", "surface:sheet", None)
    assert controller.interfaces.keyboard_context(app).root_window == "b"
    assert window._window_identity == original
    adopt(controller, app, directory(surface("b"), child, focus="b"))
    with pytest.raises(ComputerUseError, match="no longer exists"):
        controller.interfaces.keyboard_context(window)


def test_hidden_window_modal_observation_does_not_grant_receiver_and_ack_is_per_window():
    child = {**surface("sheet", parent="a"), "modal": True}
    controller, app = setup(surface("a"), surface("b"), child)
    app._instance.observed.update(("a", "b", "sheet"))
    window = app.window(ref(app, "a"))
    events = []
    set_observation_handler(events.append)
    try:
        window._context = "sheet"
        observed_sheet = publish(controller, window, "attached sheet", None, directory=app._instance.directory)
        assert controller.interfaces.keyboard_context(window).root_window == "a"
        app._context = "b"
        observed_b = publish(controller, app, "B", None, directory=app._instance.directory)
        observation_delivered(observed_b)
        observation_delivered(observed_sheet)
        assert controller.interfaces.keyboard_context(window).root_window == "sheet"
        assert controller.interfaces.keyboard_context(app).root_window == "b"
    finally:
        set_observation_handler(None)


def test_modal_receiver_relationship_is_rechecked_after_target_pin():
    child = {**surface("sheet", parent="a"), "modal": True}
    controller, app = setup(surface("a"), surface("b"), child)
    app._instance.observed.update(("a", "b", "sheet"))
    window = app.window(ref(app, "a"))
    window._context = "sheet"
    publish(controller, window, "attached sheet", None, directory=app._instance.directory)
    moved = {**child, "parent": "b"}
    controller.reader.directory._query = AsyncMock(return_value=directory(surface("a"), surface("b"), moved))
    dispatch = AsyncMock()
    controller.interfaces.write = SimpleNamespace(arun=dispatch)
    with pytest.raises(ComputerUseError, match="no longer confirmed inside"):
        asyncio.run(controller.interfaces.keyboard(window, "press_key", {"key": "Escape"}, None))
    dispatch.assert_not_awaited()


def image_for(key, marker):
    pixels = marker.encode()
    target = Binding("session", f"owner:{key}", key, f"surface:{key}", None, capture=marker)
    piece = SimpleNamespace(window=key, image=SimpleNamespace(target=target, image=pixels), box=(0, 0, 100, 80))
    return SimpleNamespace(expired=False, unavailable_mapping=False, pieces=(piece,), png=pixels, delivered=7)


def test_shared_image_bootstrap_does_not_replace_a_windows_own_later_read():
    controller, app = setup()
    window = app.window(ref(app, "a"))
    shared, new = image_for("a", "shared"), image_for("a", "new")
    own = image_for("a", "own-window-read")
    with patch("computer_use.protocol.captures.capture_metadata", return_value={"width": 100, "height": 80}):
        controller.interfaces.windows.retain_image(app._instance, shared, 1)
        assert window._image is shared
        window._image = own
        controller.interfaces.windows.retain_image(app._instance, new, 2)
        controller.interfaces.windows.retain_image(app._instance, image_for("b", "other"), 3)
    assert window._image is own
