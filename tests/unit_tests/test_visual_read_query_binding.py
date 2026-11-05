"""Image-only observation must not revoke retained exact AX lookup evidence."""

from computer_use.interaction.agent.scope import window_scope
import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from computer_use.diagnostics import local_failure
from computer_use.errors import ComputerUseError
from computer_use.interaction.query.instances.scope import adopt
from computer_use.interaction.read.views.pipeline import WindowPart, remember
from computer_use.native.contracts import Binding
from unit_tests.test_application_window_lifetimes import directory, ref, setup, surface


def visual_after_accessibility(*, child_has_ax=True, primary_has_ax=True):
    main, child = surface("a", accessible=primary_has_ax), surface("child", parent="a", accessible=child_has_ax)
    controller, app = setup(main, surface("b"), child)
    window = window_scope(controller, app, ref(app, "a"))
    for item in (main, child):
        target = Binding("session", item["instance"], item["window"], item["surface"], item["node"])
        remember(controller, window, WindowPart(target))
    visual = directory(
        *[{**item, "node": None, "accessibility": False} for item in (main, surface("b"), child)], focus="b"
    )
    visual["provider"]["status"] = "notRequested"
    adopt(controller, app, visual)
    for key in ("a", "child"):
        item = app._instance.members[key]
        remember(controller, window, WindowPart(Binding("session", item["instance"], key, item["surface"], None)))
    return controller, app, window


def test_tree_then_visual_postwrite_keeps_named_lookup_and_checks_attached_scope():
    controller, app, window = visual_after_accessibility()
    queried = []

    async def lookup(request, _control):
        root = controller._query_root(request.owner)
        queried.append(root)
        if root.root_window == "child":
            raise local_failure("no match here", "element_not_found")
        return dict(binding=replace(root, node="button"))

    controller.interfaces.lookup = SimpleNamespace(arun=AsyncMock(side_effect=lookup))
    snapshot = asyncio.run(controller.interfaces.element(window, "Open next", 1000, None))
    assert snapshot["binding"] == Binding("session", "owner:a", "a", "surface:a", "button")
    assert [target.root_window for target in queried] == ["a", "child"]
    assert all(target.node is not None for target in queried)
    # Keep raw visual facts honest: retained AX evidence belongs to its owner,
    # not to a fabricated accessibility field in the new window catalogue.
    assert all(item["node"] is None for item in app._instance.directory["surfaces"])


def test_visual_sampling_does_not_bypass_duplicate_name_ambiguity():
    controller, _app, window = visual_after_accessibility()

    async def lookup(request, _control):
        return dict(binding=replace(controller._query_root(request.owner), node="same-name-control"))

    resolve = AsyncMock(side_effect=lookup)
    controller.interfaces.lookup = SimpleNamespace(arun=resolve)
    with pytest.raises(ComputerUseError) as caught:
        asyncio.run(controller.interfaces.element(window, "Same label", 1000, None))
    assert caught.value.code == "ambiguous_target"
    assert resolve.await_count == 2


def test_truly_missing_primary_ax_scope_cannot_claim_unique_match():
    controller, _app, window = visual_after_accessibility(primary_has_ax=False)
    controller.reader.directory._query = AsyncMock(return_value=_app._instance.directory)
    resolve = AsyncMock(
        return_value=dict(binding=Binding("session", "owner:child", "child", "surface:child", "button"))
    )
    controller.interfaces.lookup = SimpleNamespace(arun=resolve)
    with pytest.raises(ComputerUseError) as caught:
        asyncio.run(controller.interfaces.element(window, "Open next", 1000, None))
    assert caught.value.code == "observation_incomplete"
    resolve.assert_awaited_once()


def test_replaced_window_cannot_inherit_prior_query_binding():
    controller, app, window = visual_after_accessibility()
    adopt(controller, app, directory(surface("a", scope="new-lifetime", accessible=False), surface("b")))
    resolve = AsyncMock()
    controller.interfaces.lookup = SimpleNamespace(arun=resolve)
    with pytest.raises(ComputerUseError) as caught:
        asyncio.run(controller.interfaces.element(window, "Open next", 1000, None))
    assert caught.value.code == "stale_target"
    resolve.assert_not_awaited()
    assert controller.state(app._instance.owners["a"]).query_target is None


def test_live_lookup_failure_is_preserved_without_selecting_another_window():
    controller, _app, window = visual_after_accessibility()
    failure = local_failure("observed native node is gone", "stale_target")
    resolve = AsyncMock(side_effect=failure)
    controller.interfaces.lookup = SimpleNamespace(arun=resolve)
    with pytest.raises(ComputerUseError) as caught:
        asyncio.run(controller.interfaces.element(window, "Open next", 1000, None))
    assert caught.value is failure
    resolve.assert_awaited_once()


def test_new_visual_only_child_does_not_invent_a_required_ax_lookup_scope():
    controller, app = setup(surface("a"), surface("b"))
    fixed = window_scope(controller, app, ref(app, "a"))
    remember(controller, fixed, WindowPart(Binding("session", "owner:a", "a", "surface:a", "node:a")))
    # A coordinate action's image-only suffix can discover and display a new
    # attached native window without asking that child for AX content.
    visual = directory(
        surface("a", accessible=False),
        surface("b", accessible=False),
        surface("new-child", parent="a", accessible=False),
    )
    visual["provider"]["status"] = "notRequested"
    adopt(controller, app, visual)
    for key in ("a", "new-child"):
        item = app._instance.members[key]
        remember(controller, fixed, WindowPart(Binding("session", item["instance"], key, item["surface"], None)))
    assert "new-child" in app._instance.observed
    assert controller.state(app._instance.owners["new-child"]).query_target.node is None
    requested = []

    async def lookup(request, _control):
        root = controller._query_root(request.owner)
        requested.append(root)
        return dict(binding=replace(root, node="matched-input"))

    controller.interfaces.lookup = SimpleNamespace(arun=AsyncMock(side_effect=lookup))
    result = asyncio.run(controller.interfaces.element(fixed, [{"identifier": "input"}], 0, None))
    assert result["binding"].root_window == "a"
    assert [item.root_window for item in requested] == ["a"]
