"""Application discovery never changes explicitly bound input/window lifetimes."""

import asyncio
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from computer_use.client.computer import App
from computer_use.client.factory import session_controller
from computer_use.errors import ComputerUseError
from computer_use.interaction.agent.scope import window_scope
from computer_use.interaction.query.instances.scope import adopt, display_selection, validate_session
from computer_use.interaction.read.views.publication import publish
from computer_use.native.contracts import Binding, Knowledge
from computer_use.native.scopes import RunningApplication
from computer_use.session.events import observation_delivered, set_observation_handler
from computer_use.session.interface_images import ImagePiece, InterfaceImage, signature
from computer_use.session.interfaces import WindowIdentity, WindowLifetime
from computer_use.session.state import AppState
from computer_use.session.windows import WindowReferences
from PIL import Image


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


def directory(*items, focus="a", partial=False, application="editor", ended=()):
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
        endedWindows=[{key: item[key] for key in ("window", "instance", "surface")} for item in ended],
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
    a = window_scope(controller, app, ref(app, "a"))
    b = window_scope(controller, app, ref(app, "b"))
    assert window_scope(controller, app, ref(app, "a"))._window_identity == a._window_identity
    assert window_scope(controller, app, ref(app, "b"))._window_identity == b._window_identity
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
    a, b = window_scope(controller, app, ref(app, "a")), window_scope(controller, app, ref(app, "b"))
    old_ref = ref(app, "a")
    identity = WindowIdentity.of(app._instance.members["a"])
    app._instance.input_context = identity
    app._instance.observed.update(("a", "b"))
    snapshot = dict(binding=Binding("session", "owner:a", "a", "surface:a", "node:a"), window_identity=identity)
    adopt(controller, app, directory(surface("b"), focus="b", ended=(surface("a"),)))
    validate_session(controller, app)
    validate_session(controller, b)
    for operation in (
        lambda: validate_session(controller, a),
        lambda: window_scope(controller, app, old_ref),
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
    window = window_scope(controller, app, ref(app, "a"))
    validate_session(controller, window)
    assert window._window_identity == Binding("session", "owner:a", "a", "surface:a", None)
    assert window._anchor == "a"


def test_app_window_rejects_other_application_even_same_name():
    controller, app = setup()
    other = directory(surface("c"), application="other")
    refs = controller.interfaces.windows.register_directory(other, "Editor")
    with pytest.raises(ComputerUseError, match="does not belong"):
        window_scope(controller, app, int(next(iter(refs))))


def test_helper_scope_alias_preserves_original_window_binding():
    controller, app = setup(surface("a"))
    number = ref(app, "a")
    first = window_scope(controller, app, number)
    helper = directory(surface("a", scope="helper"), application="helper")
    refs = controller.interfaces.windows.register_directory(helper, "Editor")
    assert int(next(iter(refs))) == number
    assert controller.interfaces.windows.resolve(number, "session").identity == WindowIdentity.of(surface("a"))
    assert window_scope(controller, app, number)._window_identity == first._window_identity
    assert first._window_identity.instance == "owner:a"


def test_retired_physical_alias_is_not_reused_for_new_owner_scope():
    controller, app = setup(surface("a"))
    number = ref(app, "a")
    old = window_scope(controller, app, number)
    adopt(controller, app, directory(focus=None, ended=(surface("a"),)))
    helper = directory(surface("a", scope="new-process"), application="helper")
    fresh = int(next(iter(controller.interfaces.windows.register_directory(helper))))
    assert fresh != number
    with pytest.raises(ComputerUseError):
        validate_session(controller, old)
    with pytest.raises(ComputerUseError):
        window_scope(controller, app, number)


def test_new_lifetime_in_same_native_slot_has_new_reference():
    registry = WindowReferences()
    first = registry.register_directory(directory(surface("a")))
    changed = registry.register_directory(directory(surface("a", scope="replacement")))
    assert set(first) != set(changed)
    assert registry.resolve(int(next(iter(first))), "session").identity.surface == "surface:a"


@pytest.mark.parametrize("retired", [False, True])
def test_exact_alias_lookup_retains_unknown_or_retired_identity_without_reviving_scope(retired):
    registry = WindowReferences()
    original = surface("a")
    identity = WindowIdentity.of(original)
    number = int(next(iter(registry.register_directory(directory(original)))))
    registry.register_directory(directory(focus=None, partial=not retired, ended=(original,) if retired else ()))
    state = registry.application("session", "editor", "")
    assert state.members == {}
    assert state.evidence[identity].membership is (Knowledge.NO if retired else Knowledge.UNKNOWN)
    assert state.evidence[identity].lifetime is (WindowLifetime.RETIRED if retired else WindowLifetime.RETAINED)
    evidence, members, aliases = dict(state.evidence), dict(state.members), dict(registry._aliases)
    applications, next_number = dict(registry._applications), registry._next
    for _ in range(2):
        assert registry.reference("session", "editor", identity) == number
        assert registry.reference("other-session", "editor", identity) is None
        assert registry.reference("session", "other-application", identity) is None
        for different in (
            WindowIdentity("other-window", identity.surface, identity.instance),
            WindowIdentity(identity.window, "other-surface", identity.instance),
            WindowIdentity(identity.window, identity.surface, "other-instance"),
        ):
            assert registry.reference("session", "editor", different) is None
    assert state.evidence == evidence and state.members == members
    assert registry._aliases == aliases and registry._applications == applications and registry._next == next_number


def test_complete_onscreen_absence_reappears_with_same_reference_and_fixed_binding():
    controller, app = setup()
    number = ref(app, "a")
    fixed = window_scope(controller, app, number)
    identity, binding = WindowIdentity.of(surface("a")), fixed._window_identity
    adopt(controller, app, directory(surface("b"), focus="b"))
    assert app._instance.evidence[identity].lifetime is WindowLifetime.RETAINED
    assert app._instance.evidence[identity].membership is Knowledge.UNKNOWN
    adopt(controller, app, directory(surface("a"), surface("b"), focus="a"))
    assert ref(app, "a") == number
    assert window_scope(controller, app, number)._window_identity == fixed._window_identity
    validate_session(controller, fixed)
    assert fixed._window_identity == binding


def test_positively_ended_identity_is_never_republished_but_new_lifetime_is():
    registry = WindowReferences()
    original = surface("a")
    first = registry.register_directory(directory(original))
    old = int(next(iter(first)))
    registry.register_directory(directory(focus=None, ended=(original,)))
    assert registry.register_directory(directory(original)) == {}
    fresh = registry.register_directory(directory(surface("a", scope="recreated")))
    assert int(next(iter(fresh))) != old
    state = registry.application("session", "editor", "")
    assert state.evidence[WindowIdentity.of(original)].lifetime is WindowLifetime.RETIRED


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
    controller.reader.directory._query = AsyncMock(
        return_value=directory(surface("b"), focus="b", ended=(surface("a"),))
    )
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
    window = window_scope(controller, app, ref(app, "b"))
    window._context = "b"
    publish(controller, window, "B", None, directory=app._instance.directory)
    assert app._instance.input_context.window == "b"
    assert window._window_identity.root_window == "b"


def test_exact_single_window_image_reuse_keeps_delivery_expiry_object():
    controller, app = setup(surface("a"))
    identity = WindowIdentity.of(app._instance.members["a"])
    target = Binding("session", "owner:a", "a", "surface:a", None, capture="capture")
    piece = ImagePiece("a", SimpleNamespace(target=target, image=b"png"), (0, 0, 100, 80))
    image = InterfaceImage(b"png", (piece,), signature(app._instance.members), delivered=7)
    controller.interfaces.windows.retain_image(app._instance, image)
    window = window_scope(controller, app, ref(app, "a"))
    assert controller.interfaces.windows.delivered_image(app._instance, window) is image
    assert app._instance.window_images[identity] is image
    image.expired = True
    assert controller.interfaces.windows.delivered_image(app._instance, window).expired


def test_composite_bootstrap_preserves_displayed_canvas_and_restricts_window_scope():
    controller, app = setup()
    targets = [Binding("session", f"owner:{key}", key, f"surface:{key}", None, capture=key) for key in ("a", "b")]
    pieces = tuple(
        ImagePiece(key, SimpleNamespace(target=target, image=b"png"), (index * 100, 0, 100, 80))
        for index, (key, target) in enumerate(zip(("a", "b"), targets))
    )
    buffer = BytesIO()
    with Image.new("RGBA", (200, 80), "white") as pixels:
        pixels.putpixel((21, 30), (0, 0, 0, 0))
        pixels.save(buffer, format="PNG")
    image = InterfaceImage(buffer.getvalue(), pieces, signature(app._instance.members), delivered=7)
    controller.interfaces.windows.retain_image(app._instance, image)
    window = window_scope(controller, app, ref(app, "b"))
    assert controller.interfaces.windows.delivered_image(app._instance, window).png is image.png
    assert controller.interfaces.windows.delivered_image(app._instance, window).pieces == (pieces[1],)
    assert controller.interfaces.windows.delivered_image(app._instance, window).pieces[0].box == (100, 0, 100, 80)
    with pytest.raises(ComputerUseError, match="outside the selected window's captured scope"):
        controller.interfaces.windows.delivered_image(app._instance, window).hit((20, 30))
    with pytest.raises(ComputerUseError, match="gap outside"):
        controller.interfaces.windows.delivered_image(app._instance, window).hit((21, 30))


def test_fixed_window_keyboard_uses_delivered_modal_without_following_app_context():
    child = {**surface("sheet", parent="a"), "modal": True}
    controller, app = setup(surface("a"), surface("b"), child)
    app._instance.observed.update(("a", "b", "sheet"))
    window = window_scope(controller, app, ref(app, "a"))
    original = window._window_identity
    window._context = "sheet"
    publish(controller, window, "attached sheet", None, directory=app._instance.directory)
    app._context = "b"
    publish(controller, app, "unrelated B", None, directory=app._instance.directory)
    target = controller.interfaces.keyboard_context(window)
    assert target == Binding("session", "owner:sheet", "sheet", "surface:sheet", None)
    assert controller.interfaces.keyboard_context(app).root_window == "b"
    assert window._window_identity == original
    adopt(controller, app, directory(surface("b"), child, focus="b", ended=(surface("a"),)))
    with pytest.raises(ComputerUseError, match="no longer exists"):
        controller.interfaces.keyboard_context(window)


def test_hidden_window_modal_observation_does_not_grant_receiver_and_ack_is_per_window():
    child = {**surface("sheet", parent="a"), "modal": True}
    controller, app = setup(surface("a"), surface("b"), child)
    app._instance.observed.update(("a", "b", "sheet"))
    window = window_scope(controller, app, ref(app, "a"))
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
    window = window_scope(controller, app, ref(app, "a"))
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
    piece = ImagePiece(key, SimpleNamespace(target=target, image=pixels), (0, 0, 100, 80))
    member = surface(key)
    return InterfaceImage(pixels, (piece,), signature({key: member}), delivered=7)


def test_latest_delivered_canvas_replaces_older_window_image_but_pending_sample_does_not():
    controller, app = setup()
    window = window_scope(controller, app, ref(app, "a"))
    shared, new = image_for("a", "shared"), image_for("a", "new")
    own = image_for("a", "own-window-read")
    controller.interfaces.windows.retain_image(app._instance, shared, 1)
    assert controller.interfaces.windows.delivered_image(app._instance, window) is shared
    window._image = own  # A newer pending sample has not been delivered.
    assert controller.interfaces.windows.delivered_image(app._instance, window) is shared
    controller.interfaces.windows.retain_image(app._instance, new, 2)
    controller.interfaces.windows.retain_image(app._instance, image_for("b", "other"), 3)
    assert controller.interfaces.windows.delivered_image(app._instance, window) is new
    assert controller.interfaces.windows.delivered_image(app._instance, window) is new
