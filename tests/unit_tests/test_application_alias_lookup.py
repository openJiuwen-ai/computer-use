"""Delivered App lookup scopes are canonical; explicit windows and menus stay fixed."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from computer_use.client.computer import computer_for_controller
from computer_use.errors import ComputerUseError
from computer_use.interaction.agent.scope import window_scope
from computer_use.interaction.query.instances.scope import adopt, display_selection
from computer_use.interaction.read.views.pipeline import WindowPart, remember
from computer_use.interaction.read.views.publication import publish
from computer_use.native.contracts import Binding
from computer_use.session.events import observation_delivered, set_observation_handler
from computer_use.session.interfaces import SurfaceOwner, WindowIdentity
from computer_use.session.state import AppState
from unit_tests.test_application_window_lifetimes import directory, ref, setup, surface


def observed_query_roots(controller, app):
    for item in app._instance.members.values():
        remember(
            controller,
            app,
            WindowPart(Binding("session", item["instance"], item["window"], item["surface"], item["node"])),
        )


def finder(controller):
    roots = []

    async def lookup(request, _control):
        target = controller._query_root(request.owner)
        roots.append(target)
        result = dict(binding=replace(target, node="matched-control"))
        state = controller.state(request.owner)
        if state.query_scope_kind == "applicationMenu":
            result.update(scope_kind="applicationMenu", menu_window_identity=state.menu_window_identity)
        return result

    controller.interfaces.lookup = SimpleNamespace(arun=AsyncMock(side_effect=lookup))
    return roots


def test_generated_owning_app_postwrite_lookup_updates_original_alias_without_read():
    controller, selector_app = setup()
    fixed_a = window_scope(controller, selector_app, ref(selector_app, "a"))
    generated_app = computer_for_controller(controller).app("editor")
    adopt(controller, generated_app, selector_app._instance.directory)
    assert generated_app is not selector_app
    observed_query_roots(controller, selector_app)
    selector_app._context = "b"
    selector_app._menu_only = True
    publish(controller, selector_app, "old B menu", directory=selector_app._instance.directory)

    adopt(controller, generated_app, directory(surface("a"), surface("b"), surface("child"), focus="child"))
    observed_query_roots(controller, generated_app)
    generated_app._context = "child"
    publish(
        controller, generated_app, "new independent Child after action", directory=generated_app._instance.directory
    )
    roots = finder(controller)
    result = asyncio.run(controller.interfaces.element(selector_app, [{"identifier": "child-input"}], 1000, None))
    assert result["binding"].root_window == "child"
    assert [root.root_window for root in roots] == ["child"]
    assert selector_app._context == "b" and selector_app._menu_only
    assert fixed_a._window_identity.root_window == "a"
    # Explicit Window retains its own lookup boundary after the App observes Child.
    result = asyncio.run(controller.interfaces.element(fixed_a, "A input", 1000, None))
    assert result["binding"].root_window == "a"


def test_pending_read_on_other_alias_does_not_change_delivered_lookup():
    controller, app = setup()
    generated = computer_for_controller(controller).app("editor")
    adopt(controller, generated, app._instance.directory)
    observed_query_roots(controller, app)
    app._context = "b"
    publish(controller, app, "delivered B", directory=app._instance.directory)
    set_observation_handler(lambda _event: None)
    try:
        generated._context = "a"
        pending = publish(controller, generated, "pending A", directory=app._instance.directory)
        roots = finder(controller)
        asyncio.run(controller.interfaces.element(app, "input", 0, None))
        assert roots[-1].root_window == "b"
        observation_delivered(pending)
        asyncio.run(controller.interfaces.element(app, "input", 0, None))
        assert roots[-1].root_window == "a"
    finally:
        set_observation_handler(None)


@pytest.mark.parametrize("observed,current,closed", [
    (True, True, True), (False, True, True), (True, False, True), (True, True, False),
])
def test_app_name_lookup_after_popup_closure_uses_only_observed_current_primary(observed, current, closed):
    from computer_use.interaction.read.presentation.document import DisplayDocument
    from unit_tests.test_app_current_scope_recovery import rendered_tree

    candidates = (surface("next"),) if current else (surface("next"), surface("peer"))
    controller, app = setup(surface("a"), *candidates)
    fixed = window_scope(controller, app, ref(app, "a"))
    original = fixed._window_identity
    observed_query_roots(controller, app)
    app._context = "a"
    # Sampling the current root is not caller delivery. Publish the form's
    # actual semantic tree before opening/closing the popup; the negative case
    # retains only its pending query root, without granting fallback authority.
    shown = [app._instance.members["a"]] + (list(candidates) if observed else [])
    targets = tuple(Binding("session", item["instance"], item["window"], item["surface"], item["node"])
                    for item in shown)
    rendered = tuple((target, *rendered_tree(target)) for target in targets)
    publish(
        controller, app, DisplayDocument.join(document for _, _, document in rendered),
        directory=app._instance.directory,
        trees=tuple((target, snapshot) for target, snapshot, _ in rendered),
    )
    delivered = app._instance.lookup_context
    facts = directory(*candidates, focus="next" if current else None, ended=(surface("a"),) if closed else ())
    adopt(controller, app, facts)
    app._instance.anchor = display_selection(controller, app._instance, facts) if current else None
    input_before = app._instance.input_context
    roots = finder(controller)
    if observed and current:
        result = asyncio.run(controller.interfaces.element(app, "OK", 0, None))
        assert result["binding"].root_window == "next"
        assert [root.root_window for root in roots] == ["next"]
        # Semantic query recovery does not acknowledge model delivery or
        # rebind the original popup handle/its existing element references.
        assert app._instance.lookup_context is delivered
        assert app._instance.input_context == input_before
        assert fixed._window_identity == original
        with pytest.raises(ComputerUseError):
            asyncio.run(controller.interfaces.element(fixed, "OK", 0, None))
    else:
        with pytest.raises(ComputerUseError):
            asyncio.run(controller.interfaces.element(app, "OK", 0, None))
        assert roots == []


def menu_owner(controller, app, key):
    identity = WindowIdentity.of(app._instance.members[key])
    target = Binding("session", identity.instance, key, "shared-menu-surface", "menu-root")
    state = AppState("editor", query_target=target, query_scope_kind="applicationMenu", menu_window_identity=identity)
    return SurfaceOwner(controller, state)


def test_fixed_window_hidden_menu_sampling_keeps_delivered_window_lookup():
    controller, app = setup()
    observed_query_roots(controller, app)
    fixed = window_scope(controller, app, ref(app, "a"))
    fixed._context = "a"
    publish(controller, fixed, "A contents", directory=app._instance.directory)
    source = menu_owner(controller, app, "a")
    set_observation_handler(lambda _event: None)
    try:
        fixed._menu_only, fixed._lookup_scopes = True, (source,)
        pending = publish(controller, fixed, "A menu", directory=app._instance.directory)
        roots = finder(controller)
        asyncio.run(controller.interfaces.element(fixed, "input", 0, None))
        assert roots[-1].surface == "surface:a"
        observation_delivered(pending)
        asyncio.run(controller.interfaces.element(fixed, "menu item", 0, None))
        assert roots[-1].surface == "shared-menu-surface"
    finally:
        set_observation_handler(None)


def test_old_menu_element_keeps_its_frozen_window_after_another_menu_observation():
    controller, app = setup()
    fixed_a = window_scope(controller, app, ref(app, "a"))
    original = WindowIdentity.of(app._instance.members["a"])
    snapshot = dict(
        binding=Binding("session", original.instance, "a", "shared-menu-surface", "item"),
        scope_kind="applicationMenu",
        menu_window_identity=original,
    )
    other = menu_owner(controller, app, "b")
    app._context, app._menu_only, app._lookup_scopes = "b", True, (other,)
    set_observation_handler(lambda _event: None)
    try:
        publish(controller, app, "hidden B menu", directory=app._instance.directory)
        controller.interfaces.validate_snapshot(fixed_a, snapshot)
        assert snapshot["menu_window_identity"] == original
    finally:
        set_observation_handler(None)
    adopt(controller, app, directory(surface("b"), focus="b", ended=(surface("a"),)))
    with pytest.raises(ComputerUseError) as caught:
        controller.interfaces.validate_snapshot(app, snapshot)
    assert caught.value.code == "stale_target"


def explicit_subtree(controller, owner, snapshot):
    from unittest.mock import patch

    from computer_use.interaction.read.element.agent import AgentElementReadPipeline

    pipeline = AgentElementReadPipeline(controller)
    tree = SimpleNamespace(roots=("node",), refs={"1": "node"})
    pipeline.collect = AsyncMock(return_value=(tree, snapshot))
    pipeline.preparation = SimpleNamespace(run=lambda _request: None)
    with patch("computer_use.interaction.read.element.agent.tree_document", return_value="Explicit source details"):
        return asyncio.run(pipeline.run(owner, snapshot, None))


def test_explicit_child_element_read_updates_app_alias_from_its_snapshot():
    from computer_use.session.observation_data import observation_data

    controller, app = setup(surface("a"), surface("b"), surface("child"))
    observed_query_roots(controller, app)
    app._context = "b"
    publish(controller, app, "B", directory=app._instance.directory)
    app._menu_only = True  # A stale alias's staged menu mode is not the subtree source.
    identity = WindowIdentity.of(app._instance.members["child"])
    snapshot = dict(
        binding=Binding("session", identity.instance, "child", identity.surface, "field"),
        window_identity=identity,
        scope_kind="window",
        source={"ref": "1"},
    )
    set_observation_handler(lambda _event: None)
    try:
        observed = explicit_subtree(controller, app, snapshot)
        assert app._instance.input_context.window == "b"
        observation_delivered(observed)
        assert app._instance.input_context == identity
        assert app._instance.lookup_context.window == identity
        assert not app._instance.lookup_context.menu_only
        assert observation_data(observed)["identity"]["current"] == str(ref(app, "child"))
        roots = finder(controller)
        asyncio.run(controller.interfaces.element(app, [{"identifier": "child-input"}], 0, None))
        assert roots[-1].root_window == "child"
        assert app._context == "b"  # No eager alias mutation was used for the fix.
    finally:
        set_observation_handler(None)


def test_explicit_element_read_keeps_fixed_window_and_original_menu_context():
    controller, app = setup()
    fixed = window_scope(controller, app, ref(app, "a"))
    root = fixed._window_identity
    identity = WindowIdentity.of(app._instance.members["a"])
    app._context = "b"
    publish(controller, app, "B", directory=app._instance.directory)
    fixed._context = "b"  # An explicit source still controls publication.
    snapshot = dict(
        binding=Binding("session", identity.instance, "a", "shared-menu-surface", "menu-item"),
        menu_window_identity=identity,
        scope_kind="applicationMenu",
        source={"ref": "1"},
    )
    explicit_subtree(controller, fixed, snapshot)
    assert fixed._window_identity == root
    assert fixed._input_context == identity
    assert fixed._lookup_context.window == identity
    assert fixed._lookup_context.menu_only
    source = controller.state(fixed._lookup_context.menus[0])
    assert source.query_target == snapshot["binding"]
    assert source.menu_window_identity == identity
    assert app._instance.input_context == identity


@pytest.mark.parametrize("refs", [{"1": "field", "2": "child"}, {"2": "child"}])
def test_explicit_read_retains_selected_reference_or_reports_unconfirmed(refs):
    from computer_use.interaction.read.element.agent import AgentElementReadPipeline

    controller, app = setup()
    identity = WindowIdentity.of(app._instance.members["a"])
    snapshot = dict(binding=Binding("session", identity.instance, "a", identity.surface, "field"),
                    window_identity=identity, scope_kind="window")
    controller.interfaces.validate_snapshot = lambda *_: None
    tree = SimpleNamespace(refs=refs, facts={"unread": ()})
    controller.reader.reader = SimpleNamespace(read=AsyncMock(return_value=tree))
    controller.reader.number_snapshots = lambda sources: sources

    def remember(state, target, captured):
        state.refs.update({ref: {"source": {"ref": ref}} for ref in captured.refs})

    controller.remember_observation = remember
    pipeline = AgentElementReadPipeline(controller)
    if "1" in refs:
        _, fresh = asyncio.run(pipeline.collect(app, snapshot))
        assert fresh["source"]["ref"] == "1"
    else:
        with pytest.raises(ComputerUseError) as caught:
            asyncio.run(pipeline.collect(app, snapshot))
        assert caught.value.code == "observation_incomplete"
