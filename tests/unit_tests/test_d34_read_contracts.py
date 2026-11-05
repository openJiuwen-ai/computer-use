# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Application detail selection, delivered input context and original-target receipts."""

import asyncio
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from computer_use.client.factory import session_controller
from computer_use.errors import ComputerUseError
from computer_use.interaction.read.acquisition.components.support import ObservationGap
from computer_use.interaction.read.views.pipeline import InterfaceReadPipeline, WindowPart, member_target
from computer_use.interaction.read.views.publication import capture_header, publish
from computer_use.interaction.read.views.related import RelatedRead, original_lifecycle
from computer_use.interaction.requests import ExecuteAction
from computer_use.native.contracts import Binding, Intent, Knowledge
from computer_use.native.lifecycle import CallLifecycle
from computer_use.native.scopes import WorkingWindow
from computer_use.session.events import observation_delivered, set_observation_handler
from computer_use.session.interfaces import DirectoryMembership, WindowIdentity, WindowMembership
from computer_use.session.observation import CapturedTextDocument, lazy_observation
from computer_use.session.observation_data import observation_data, retain_observation_data
from computer_use.session.results import result_receipt


class UI(SimpleNamespace):
    """Request-scope fixture; delivery state lives in the application session."""

    def __init__(self, **fields):
        from computer_use.session.interfaces import DeliveredWindowContext, LookupContext

        receiver = fields.pop("_input_context", None)
        lookup = fields.pop("_lookup_context", None)
        generation = fields.pop("_delivered_generation", 0)
        super().__init__(**fields)
        fixed = fields.get("_window_identity")
        if fixed is not None:
            identity = WindowIdentity(fixed.root_window, fixed.surface, fixed.instance)
            self._instance.window_contexts[identity] = DeliveredWindowContext(
                receiver, lookup or LookupContext(receiver), generation,
            )

    @property
    def _input_context(self):
        fixed = self._window_identity
        return self._instance.window_contexts[WindowIdentity(fixed.root_window, fixed.surface, fixed.instance)].input

    @property
    def _lookup_context(self):
        fixed = self._window_identity
        return self._instance.window_contexts[WindowIdentity(fixed.root_window, fixed.surface, fixed.instance)].lookup


def window(key, *, title="Same title", parent=None):
    return dict(
        window=key,
        instance="instance:" + key,
        surface="surface:" + key,
        node="node:" + key,
        title=title,
        kind="window",
        nativeWindow=True,
        accessibility=True,
        modal=False,
        parent=parent,
        parentState="known" if parent else "none",
    )


def directory(*items, focused=None, reasons=(), ended=()):
    return dict(
        kind="surfaces",
        session="session",
        application="application",
        platform="macos",
        observed=dict(id="read", startedAt=1, completedAt=2),
        surfaces=list(items),
        focusedWindow=focused,
        stopReasons=list(reasons),
        rootDiscoveryComplete=not reasons,
        endedWindows=[{key: item[key] for key in ("window", "instance", "surface")} for item in ended],
        provider=dict(status="notRequested", attribute=None, writeStatus=None),
    )


def setup(current):
    port = SimpleNamespace(
        connection=SimpleNamespace(
            session="session", session_identity=(1, "session"), capabilities=(), retain_session=lambda _: nullcontext()
        )
    )
    port.calls = CallLifecycle(port)
    controller = session_controller(port)
    state = controller.interfaces.windows.application("session", "application", "Fixture")
    state.reconcile(
        current,
        DirectoryMembership(
            not current["stopReasons"],
            tuple(WindowMembership(WindowIdentity.of(item), Knowledge.YES) for item in current["surfaces"]),
        ),
    )
    state.windows = {
        int(ref): key for ref, key in controller.interfaces.windows.register_directory(current, "Fixture").items()
    }
    first = current["surfaces"][0] if current["surfaces"] else None
    state.anchor = WorkingWindow("application", "Fixture", member_target(state, first)) if first else None
    ui = UI(_instance=state, _anchor=None, _context=first["window"] if first else None, _image=None)
    controller.register(ui, dict(selector="Fixture"))
    return controller, ui, state


def test_window_bindings_use_each_windows_own_native_scope():
    a, b = window("A"), window("B")
    _, _, state = setup(directory(a, b))
    assert member_target(state, b) == Binding("session", b["instance"], "B", b["surface"], b["node"])
    assert state.anchor.root.root_window == "A"


def test_related_selection_distinguishes_new_windows_from_old_focus_and_lifecycle():
    a, b, child = window("A"), window("B"), window("Child", title="")
    before = directory(a, b)
    _, _, state = setup(before)
    related = RelatedRead(member_target(state, a), tuple(WindowIdentity.of(item) for item in before["surfaces"]))
    after = directory(a, b, child, focused="B")
    assert related.preferred(directory(a, b, focused="B"), "B") == "A"
    assert related.preferred(after, "B") == "Child"
    assert related.new_windows(after) == ("Child",)
    assert original_lifecycle(related.original, after, related.original_window) == "observed"
    assert original_lifecycle(related.original, directory(b, child)) == "unknown"
    assert original_lifecycle(related.original, directory(b, child, ended=(a,))) == "closed"
    replacement = {**a, "surface": "surface:A-new", "instance": "instance:A-new"}
    assert original_lifecycle(related.original, directory(replacement, b)) == "closed"
    assert original_lifecycle(related.original, directory(b, child, reasons=("incomplete",))) == "unknown"
    assert original_lifecycle(related.original, directory({**a, "surface": "replacement"}, b)) == "closed"


def test_publication_only_delivered_generation_updates_shared_application_context():
    a, b = window("A"), window("B")
    current = directory(a, b, focused="B")
    controller, app, state = setup(current)
    events = []
    set_observation_handler(events.append)
    try:
        app._context = "A"
        displayed = publish(controller, app, "A body", directory=current)
        app._context = "B"
        sampled = publish(controller, app, "B undisclosed body", directory=current)
        assert state.input_context is None
        observation_delivered(displayed)
        assert state.input_context == WindowIdentity.of(a)
        # The later sample cannot revoke an earlier result that is the only one
        # actually printed, but later actual delivery advances the context.
        observation_delivered(sampled)
        assert state.input_context == WindowIdentity.of(b)
        observation_delivered(displayed)
        assert state.input_context == WindowIdentity.of(b)
    finally:
        set_observation_handler(None)


def test_fixed_window_delivery_updates_shared_context_without_rebinding_handle():
    a, b = window("A"), window("B")
    current = directory(a, b)
    controller, app, state = setup(current)
    fixed = UI(_instance=state, _anchor="B", _context="B", _image=None)
    controller.register(fixed, dict(selector="Fixture"))
    publish(controller, fixed, "B body", directory=current)
    assert state.input_context == WindowIdentity.of(b)
    assert fixed._anchor == "B" and app._anchor is None
    assert state.anchor.root.root_window == "A"


def test_catalogue_and_lifecycle_evidence_is_frozen_at_publication():
    a, b = window("A"), window("B")
    current = directory(a, b)
    controller, app, state = setup(current)
    result = publish(controller, app, "A body", directory=current)
    current["surfaces"].clear()
    captured = observation_data(result)["directory"]
    assert original_lifecycle(Binding("session", a["instance"], "A", a["surface"]), captured) == "observed"
    assert state.input_context == WindowIdentity.of(a)


def test_application_header_preserves_all_windows_and_unknown_parent_selection():
    a, b = window("A"), {**window("B", title=""), "parentState": "unknown"}
    current = directory(a, b, focused="A")
    controller, app, _ = setup(current)
    header = capture_header(controller, app, current, parts=())
    from computer_use.interaction.read.presentation.headers import interface_header

    text = interface_header(header)
    assert "window=0" in text and "window=1" in text
    assert "parent unknown" in text and header.scope == "application"
    assert len(header.scoped) == 2


def test_related_failed_new_detail_preserves_original_image_and_catalogue(monkeypatch):
    a, child = window("A"), window("Child", title="")
    current = directory(a, child, focused="Child")
    controller, app, state = setup(current)
    related = RelatedRead(member_target(state, a), (WindowIdentity.of(a),))
    controller.reader.read = AsyncMock(
        side_effect=ComputerUseError(
            dict(
                code="observation_incomplete",
                target="Child",
                effect="none",
                detail="new child not ready",
                next="observe",
            )
        )
    )
    pipeline = InterfaceReadPipeline(controller)
    gap = ObservationGap("image", "observation_incomplete", "child image unavailable")

    async def member(owner, item, view, **kwargs):
        assert view == "both"  # new windows receive AX and visual fallback together
        return WindowPart(
            member_target(state, item),
            image=SimpleNamespace(image=b"old pixels") if item["window"] == "A" else None,
            gaps=() if item["window"] == "A" else (gap,),
        )

    pipeline.member = member
    monkeypatch.setattr("computer_use.interaction.read.views.pipeline.remember", lambda *_: None)
    monkeypatch.setattr(
        "computer_use.interaction.read.acquisition.images.composite.pipeline.InterfaceImagePipeline.run",
        lambda self, owner, parts, **_kw: (SimpleNamespace(png=b"old pixels"), []),
    )
    monkeypatch.setattr("computer_use.protocol.captures.capture_metadata", lambda _: {})
    monkeypatch.setattr(
        "computer_use.interaction.read.views.pipeline.publish",
        lambda _c, _o, text, screenshot=None, **kw: (text.text, screenshot, kw),
    )
    text, image, metadata = asyncio.run(pipeline._collect(app, "tree", False, None, current, None, None, related))
    assert image == b"old pixels" and metadata["content_available"]
    assert "window=0" in text and "window=1" in text
    assert "original window is shown" in text and metadata["partial"]
    assert app._context == "A"
    assert related.original.root_window == "A"


def test_write_suffix_observes_owning_app_and_retains_original_lifecycle():
    a, child = window("A"), window("Child", title="")
    before, after = directory(a), directory(a, child, focused="Child")
    controller, app, state = setup(before)
    fixed = UI(_instance=state, _anchor="A", _app=app, _image=None)
    controller.register(fixed, dict(selector="Fixture"))
    target = member_target(state, a)
    receipt = dict(status="dispatched", target="A button", detail="Click delivered once")
    controller.engine.execute = AsyncMock(return_value=receipt)
    observed = lazy_observation(CapturedTextDocument("Child input; window=1"))
    retain_observation_data(observed, (), (), directory=after)

    async def read(owner, view, full, menu_path, *, related):
        assert owner is app and fixed._anchor == "A"
        assert related.known_windows == (WindowIdentity.of(a),)
        assert related.original == target
        return observed

    controller.interfaces.read = AsyncMock(side_effect=read)
    result = asyncio.run(controller.run(ExecuteAction(fixed, Intent("click", {}), target)))
    assert result_receipt(result) is receipt and result.observation is observed
    assert result.window_lifecycle == "observed"
    assert [choice.title for choice in result.choices] == ["Same title", ""]
    assert fixed._anchor == "A"
    controller.engine.execute.assert_awaited_once()


def test_fixed_window_read_needs_no_application_display_primary(monkeypatch):
    a, b = window("A"), window("B")
    current = directory(a, b, focused="B")
    controller, app, state = setup(current)
    fixed = UI(_instance=state, _anchor="A", _context=None, _image=None)
    controller.register(fixed, dict(selector="Fixture"))
    state.anchor = None
    pipeline = InterfaceReadPipeline(controller)
    pipeline.member = AsyncMock(return_value=WindowPart(member_target(state, a)))
    monkeypatch.setattr("computer_use.interaction.read.views.pipeline.remember", lambda *_: None)
    monkeypatch.setattr(
        "computer_use.interaction.read.acquisition.images.composite.pipeline.InterfaceImagePipeline.run",
        lambda *_: (None, []),
    )
    monkeypatch.setattr(
        "computer_use.interaction.read.views.pipeline.publish", lambda _c, _o, text, *args, **kw: text.text
    )
    text = asyncio.run(pipeline._collect(fixed, "image", False, None, current, None, None, None))
    assert pipeline.member.await_count == 1
    assert pipeline.member.call_args.args[1]["window"] == "A"
    assert "window=0" in text and "window=1" in text
    assert "outside read scope" in text
    assert state.anchor is None and fixed._anchor == "A"


def test_sheet_dismissal_restores_original_window_context_only_on_related_delivery():
    a, b, sheet = window("A"), window("B"), {**window("Sheet", parent="B"), "modal": True}
    before, after = directory(a, b, sheet, focused="Sheet"), directory(a, b, focused="A", ended=(sheet,))
    controller, app, state = setup(before)
    fixed = UI(
        _instance=state,
        _anchor="B",
        _app=app,
        _image=None,
        _window_identity=member_target(state, b),
        _input_context=WindowIdentity.of(sheet),
        _delivered_generation=0,
    )
    controller.register(fixed, dict(selector="Fixture"))
    target = member_target(state, sheet)
    controller.engine.execute = AsyncMock(
        return_value=dict(status="dispatched", target="Sheet", detail="Dismissed once")
    )
    events = []
    set_observation_handler(events.append)
    try:

        async def read(owner, view, full, menu_path, *, related):
            assert owner is app and related.context_owner() is fixed
            assert related.preferred(after, "A") == "B"
            state.reconcile(
                after,
                DirectoryMembership(
                    True, tuple(WindowMembership(WindowIdentity.of(item), Knowledge.YES) for item in after["surfaces"]),
                    (WindowIdentity.of(sheet),)
                ),
            )
            state.windows = {
                int(ref): key for ref, key in controller.interfaces.windows.register_directory(after, "Fixture").items()
            }
            app._context = "B"
            return publish(controller, app, "B after dismissal", directory=after, context_owner=related.context_owner)

        controller.interfaces.read = AsyncMock(side_effect=read)
        result = asyncio.run(controller.run(ExecuteAction(fixed, Intent("key", {"key": "Escape"}), target)))
        assert result.window_lifecycle == "closed"  # Original target was the dismissed Sheet.
        assert fixed._input_context == WindowIdentity.of(sheet)
        observation_delivered(result.observation)
        assert fixed._input_context == WindowIdentity.of(b)
        assert state.input_context == WindowIdentity.of(b)
        assert fixed._window_identity.root_window == "B"
        assert state.evidence[WindowIdentity.of(sheet)].lifetime.value == "retired"
    finally:
        set_observation_handler(None)


def test_related_independent_window_cannot_change_fixed_window_receiver():
    import weakref

    a, child = window("A"), window("Child")
    current = directory(a, child, focused="Child")
    controller, app, state = setup(current)
    fixed = UI(
        _instance=state,
        _anchor="A",
        _window_identity=member_target(state, a),
        _input_context=WindowIdentity.of(a),
        _delivered_generation=0,
    )
    app._context = "Child"
    publish(controller, app, "Child", directory=current, context_owner=weakref.ref(fixed))
    assert state.input_context == WindowIdentity.of(child)
    assert fixed._input_context == WindowIdentity.of(a)


def test_visual_sampling_preserves_query_root_only_for_same_window_identity():
    from dataclasses import replace

    from computer_use.interaction.read.views.pipeline import remember

    item = window("A")
    controller, app, state = setup(directory(item))
    owner = UI()
    controller.register(owner, dict(selector="Fixture"))
    state.owners["A"] = owner
    original = member_target(state, item)
    native_state = controller.state(owner)
    native_state.query_target = original
    before_tree = object()
    native_state.query_snapshot = before_tree
    remember(controller, app, WindowPart(replace(original, node=None)))
    assert native_state.query_target is original
    assert native_state.query_snapshot is before_tree
    # A real new AX root replaces old evidence; a reused owner cannot carry a
    # query root across a changed native identity even on a visual-only read.
    replacement = replace(original, node="replacement AX root")
    remember(controller, app, WindowPart(replacement))
    assert native_state.query_target == replacement
    native_state.query_target = replace(original, instance="retired instance")
    remember(controller, app, WindowPart(replace(original, node=None)))
    assert native_state.query_snapshot is None
    assert native_state.query_target.node is None
    assert native_state.query_target.instance == original.instance


def test_application_lookup_context_captures_delivered_window_and_menu_configuration():
    a, b = window("A"), window("B")
    current = directory(a, b)
    controller, alias, state = setup(current)
    internal = UI(_instance=state, _anchor=None, _context="B", _image=None)
    controller.register(internal, dict(selector="Fixture"))
    old_menu, new_menu = UI(), UI()
    for menu in (old_menu, new_menu):
        controller.register(menu, {"selector": "Fixture"})
    alias._context, alias._menu_only, alias._lookup_scopes = "A", True, (old_menu,)
    internal._menu_only, internal._lookup_scopes = False, (new_menu,)
    set_observation_handler(lambda _: None)
    try:
        first = publish(controller, alias, "A menu", directory=current)
        second = publish(controller, internal, "B detail", directory=current)
        alias._menu_only, alias._lookup_scopes = False, ()
        assert state.lookup_context is None
        observation_delivered(first)
        assert state.lookup_context.window == WindowIdentity.of(a)
        assert state.lookup_context.menu_only and state.lookup_context.menus == (old_menu,)
        observation_delivered(second)
        assert alias._instance.lookup_context is internal._instance.lookup_context
        assert state.lookup_context.window == WindowIdentity.of(b)
        assert not state.lookup_context.menu_only and state.lookup_context.menus == (new_menu,)
    finally:
        set_observation_handler(None)


def test_related_window_lookup_resets_menu_mode_and_excludes_unrelated_menus():
    import weakref

    from computer_use.session.interfaces import LookupContext

    a, b = window("A"), window("B")
    current = directory(a, b)
    controller, app, state = setup(current)
    fixed = UI(
        _instance=state,
        _anchor="A",
        _window_identity=member_target(state, a),
        _input_context=WindowIdentity.of(a),
        _delivered_generation=0,
        _lookup_context=LookupContext(WindowIdentity.of(a), True, ()),
    )
    menu_a, menu_b = UI(), UI()
    for owner, key in ((menu_a, "A"), (menu_b, "B")):
        controller.register(owner, dict(selector="Fixture"))
        controller.state(owner).query_target = Binding("session", "menu-instance", None, "menu:" + key, "menu-node")
        controller.state(owner).menu_window_identity = WindowIdentity.of(state.members[key])
    app._menu_only, app._lookup_scopes = False, (menu_a, menu_b)
    publish(controller, app, "A after menu dismissed", directory=current, context_owner=weakref.ref(fixed))
    assert not fixed._lookup_context.menu_only
    assert fixed._lookup_context.menus == (menu_a,)
    assert state.lookup_context.menus == (menu_a, menu_b)


def test_catalogue_only_observation_does_not_republish_previous_menu_lookup():
    a, b = window("A"), window("B")
    current = directory(a, b)
    controller, app, state = setup(current)
    state.anchor = None
    app._menu_only, app._lookup_scopes = True, (UI(),)
    result = asyncio.run(
        InterfaceReadPipeline(controller)._collect(app, "image", False, None, current, None, None, None)
    )
    assert "window=0" in result.text
    assert state.lookup_context.window is None
    assert not state.lookup_context.menu_only and state.lookup_context.menus == ()


def test_menu_source_window_identity_survives_later_same_surface_menu_read():
    from computer_use.interaction.read.views.menus import remember_menu

    a, b = window("A"), window("B")
    controller, app, state = setup(directory(a, b))
    controller.reader.number_snapshots = lambda values: values
    controller._remember = lambda native_state, target, tree: native_state.refs.update(
        {ref: dict(binding=target) for ref in tree.refs}
    )
    target = Binding("session", "menu-instance", None, "shared-application-menu", "menu-root")
    first = SimpleNamespace(
        parts=(
            SimpleNamespace(target=target, tree=SimpleNamespace(scope_kind="applicationMenu", refs={"1": "node:1"})),
        )
    )
    second = SimpleNamespace(
        parts=(
            SimpleNamespace(target=target, tree=SimpleNamespace(scope_kind="applicationMenu", refs={"2": "node:2"})),
        )
    )
    a_sources, _ = remember_menu(controller, app, first, "A")
    b_sources, _ = remember_menu(controller, app, second, "B")
    assert controller.state(a_sources[0]).menu_window_identity == WindowIdentity.of(a)
    assert controller.state(b_sources[0]).menu_window_identity == WindowIdentity.of(b)
    assert state.refs["1"]["menu_window_identity"] == WindowIdentity.of(a)
    assert state.refs["2"]["menu_window_identity"] == WindowIdentity.of(b)


@pytest.mark.parametrize("invalidated", [False, True])
def test_sheet_closes_during_related_capture_then_surviving_fixed_parent_is_delivered(monkeypatch, invalidated):
    monkeypatch.setattr("computer_use.interaction.read.views.collection.window_content_unexposed", lambda _: False)
    import weakref

    from computer_use.interaction.read.model import AppObservation
    from computer_use.interaction.read.presentation.document import DisplayDocument
    from computer_use.session.interfaces import LookupContext

    b, other = window("B"), window("Other")
    sheet = {**window("Sheet", parent="B"), "node": None, "modal": None, "accessibility": False}
    before, after = directory(b, other, sheet, focused="B"), directory(b, other, focused="Other", ended=(sheet,))
    controller, app, state = setup(before)
    parent = UI(
        _instance=state,
        _anchor="B",
        _window_identity=member_target(state, b),
        _input_context=WindowIdentity.of(sheet),
        _lookup_context=LookupContext(WindowIdentity.of(sheet), False, ()),
        _delivered_generation=0,
    )
    original = member_target(state, sheet)
    related = RelatedRead(
        original,
        tuple(WindowIdentity.of(item) for item in before["surfaces"]),
        weakref.ref(parent),
        WindowIdentity.of(b),
    )
    # The first suffix directory still contains the closing Sheet. Acquisition
    # rechecks it and returns a later directory without that exact identity.
    controller.reader.read = AsyncMock(
        return_value=AppObservation(
            selection=WorkingWindow("application", "Fixture", original),
            target=original,
            applications=None,
            source_directory=before,
            directory=after,
            tree=None,
            image=None,
            document=DisplayDocument.join(()),
            refs={},
            windows={},
            gaps=(),
            scope_confirmed=False,
        )
    )
    if invalidated:
        from computer_use.native.scopes import FixedWindowInvalidated

        controller.reader.read.side_effect = FixedWindowInvalidated(
            original, "Sheet closed during recheck", directory=after
        )
    pipeline = InterfaceReadPipeline(controller)
    tree = SimpleNamespace(native=SimpleNamespace(session="session", roots=(b["node"],)), refs={}, roots=())
    pipeline.member = AsyncMock(return_value=WindowPart(member_target(state, b), tree=tree))
    pipeline.preparation.run = lambda _value, _control=None: SimpleNamespace(roots=())
    monkeypatch.setattr("computer_use.interaction.read.views.pipeline.remember", lambda *_: None)
    monkeypatch.setattr(
        "computer_use.interaction.read.views.pipeline.tree_document",
        lambda *_a, **_kw: DisplayDocument.join(("B input",)),
    )
    set_observation_handler(lambda _: None)
    try:
        result = asyncio.run(pipeline._collect(app, "tree", False, None, before, None, None, related))
        assert pipeline.member.await_count == 1
        assert pipeline.member.call_args.args[1]["window"] == "B"
        assert "B input" in result.text
        assert parent._input_context == WindowIdentity.of(sheet)
        assert original_lifecycle(original, observation_data(result)["directory"], related.original_window) == "closed"
        observation_delivered(result)
        assert parent._input_context == WindowIdentity.of(b)
        assert state.lookup_context.window == WindowIdentity.of(b)
        assert parent._window_identity.root_window == "B"
        assert state.evidence[WindowIdentity.of(sheet)].lifetime.value == "retired"
    finally:
        set_observation_handler(None)


def test_related_recovery_prefers_exact_parent_then_observes_current_app_window():
    a, b, sheet = window("A"), window("B"), window("Sheet", parent="B")
    _, _, state = setup(directory(a, b, sheet))
    identities = tuple(WindowIdentity.of(item) for item in state.directory["surfaces"])
    related = RelatedRead(member_target(state, sheet), identities, context_root=WindowIdentity.of(b))
    assert related.recovery_window(directory(a, b, focused="A")) == "B"
    assert related.recovery_window(directory(a, {**b, "surface": "replacement-B"}, focused="A")) == "A"
    closed_a = RelatedRead(member_target(state, a), identities, context_root=WindowIdentity.of(a))
    assert closed_a.recovery_window(directory(b, focused="B")) == "B"
    # A still-enumerated but rootless popup must not select itself again.
    rootless = {**sheet, "node": None, "accessibility": False}
    fixed_popup = RelatedRead(member_target(state, sheet), identities, context_root=WindowIdentity.of(sheet))
    assert fixed_popup.recovery_window(directory(rootless, b, focused="B"), frozenset({"Sheet"})) == "B"


def test_rootless_fixed_popup_observes_current_app_window_without_rebinding_popup(monkeypatch):
    monkeypatch.setattr("computer_use.interaction.read.views.collection.window_content_unexposed", lambda _: False)
    import weakref

    from computer_use.interaction.read.model import AppObservation
    from computer_use.interaction.read.presentation.document import DisplayDocument
    from computer_use.session.interfaces import LookupContext

    b, popup = window("B"), window("Popup")
    before = directory(popup, b, focused="Popup")
    rootless = {**popup, "node": None, "accessibility": False}
    after = directory(rootless, b, focused="B")
    controller, app, state = setup(before)
    original = member_target(state, popup)
    fixed = UI(
        _instance=state, _anchor="Popup", _window_identity=original,
        _input_context=WindowIdentity.of(popup),
        _lookup_context=LookupContext(WindowIdentity.of(popup), False, ()), _delivered_generation=0,
    )
    related = RelatedRead(
        original, tuple(WindowIdentity.of(item) for item in before["surfaces"]),
        weakref.ref(fixed), WindowIdentity.of(popup),
    )
    controller.reader.read = AsyncMock(return_value=AppObservation(
        selection=WorkingWindow("application", "Fixture", original), target=original,
        applications=None, source_directory=before, directory=after, tree=None, image=None,
        document=DisplayDocument.join(()), refs={}, windows={}, gaps=(), scope_confirmed=False,
    ))
    pipeline = InterfaceReadPipeline(controller)
    tree = SimpleNamespace(native=SimpleNamespace(session="session", roots=(b["node"],)), refs={}, roots=())
    async def member(owner, item, view, **kwargs):
        return WindowPart(member_target(state, item), tree=tree if item["window"] == "B" else None)

    pipeline.member = AsyncMock(side_effect=member)
    pipeline.preparation.run = lambda _value, _control=None: SimpleNamespace(roots=())
    monkeypatch.setattr("computer_use.interaction.read.views.pipeline.remember", lambda *_: None)
    monkeypatch.setattr(
        "computer_use.interaction.read.views.pipeline.tree_document",
        lambda *_a, **_kw: DisplayDocument.join(("Parent form content",)),
    )
    result = asyncio.run(pipeline._collect(app, "tree", False, None, before, None, None, related))
    assert "Parent form content" in result.text
    assert any(call.args[1]["window"] == "B" for call in pipeline.member.call_args_list)
    assert sum(call.args[1]["window"] == "Popup" and call.args[2] == "image"
               for call in pipeline.member.call_args_list) == 1
    assert fixed._window_identity is original
    assert fixed._input_context == WindowIdentity.of(popup)
    assert state.input_context == WindowIdentity.of(b)


@pytest.mark.parametrize("case", ["focused", "single", "ambiguous"])
def test_app_scope_observes_fresh_primary_when_old_principal_closes_during_collection(monkeypatch, case):
    from computer_use.interaction.read.presentation.document import DisplayDocument
    from computer_use.native.scopes import FixedWindowInvalidated
    from computer_use.session.interfaces import LookupContext
    from unit_tests.test_dynamic_observation import tree as observed_tree

    b, other, sheet = window("B"), window("Other"), window("Sheet", parent="B")
    menu = {**window("Menu"), "kind": "menu"}
    before = directory(sheet, b, other, focused="Sheet")
    after = directory(b, menu) if case == "single" else directory(b, other, focused="B" if case == "focused" else None)
    after["endedWindows"] = [{key: sheet[key] for key in ("window", "instance", "surface")}]
    controller, app, state = setup(before)
    original = member_target(state, sheet)
    state.input_context = WindowIdentity.of(sheet)
    state.lookup_context = LookupContext(WindowIdentity.of(sheet), False, ())
    related = RelatedRead(original, tuple(WindowIdentity.of(item) for item in before["surfaces"]))
    controller.reader.read = AsyncMock(
        side_effect=FixedWindowInvalidated(original, "old principal closed", directory=after)
    )
    pipeline = InterfaceReadPipeline(controller)
    tree = observed_tree(b, "B input")
    pipeline.member = AsyncMock(return_value=WindowPart(member_target(state, b), tree=tree))
    pipeline.preparation.run = lambda _value, _control=None: SimpleNamespace(roots=())
    monkeypatch.setattr("computer_use.interaction.read.views.pipeline.remember", lambda *_: None)
    monkeypatch.setattr(
        "computer_use.interaction.read.views.pipeline.tree_document",
        lambda *_a, **_kw: DisplayDocument.join(("B input",)),
    )
    set_observation_handler(lambda _: None)
    try:
        result = asyncio.run(pipeline._collect(app, "tree", False, None, before, None, None, related))
        assert state.input_context == WindowIdentity.of(sheet)
        assert original_lifecycle(original, observation_data(result)["directory"], related.original_window) == "closed"
        if case == "ambiguous":
            pipeline.member.assert_not_awaited()
            assert not observation_data(result)["content_available"]
        else:
            assert pipeline.member.await_count == 1 and pipeline.member.call_args.args[1]["window"] == "B"
            assert "B input" in result.text
            assert state.anchor.root.root_window == "B"  # Presentation primary only, before delivery.
            observation_delivered(result)
            assert state.input_context == WindowIdentity.of(b)
            assert state.lookup_context.window == WindowIdentity.of(b)
        assert state.evidence[WindowIdentity.of(sheet)].lifetime.value == "retired"
    finally:
        set_observation_handler(None)
