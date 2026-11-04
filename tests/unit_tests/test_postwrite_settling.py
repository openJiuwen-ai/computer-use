# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Delayed UI activation gets one content-based refresh, without replaying input."""

import asyncio
from contextlib import nullcontext
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from computer_use.interaction.read.model import AppObservation
from computer_use.interaction.read.presentation.document import DisplayDocument
from computer_use.interaction.read.views.pipeline import InterfaceReadPipeline, member_target
from computer_use.interaction.read.views.related import RelatedRead
from computer_use.interaction.read.views.settling import same_window_content
from computer_use.native.scopes import WorkingWindow
from computer_use.protocol.observation.model import NativeNode, NativeObservation, Relation, States, TextInfo
from computer_use.session.events import set_observation_handler
from computer_use.session.interfaces import WindowIdentity
from computer_use.session.observation_data import observation_data
from test_d34_read_contracts import directory, setup, window


def snapshot(*, expanded=False, focused=False, clock=1):
    nodes = {
        "node:A": NativeNode(role="window", states=States(focused=focused)),
        "button": NativeNode(role="button", name="Duration", text=TextInfo(source=f"capture-{clock}:button")),
    }
    if expanded:
        nodes["choice"] = NativeNode(role="list_item", name="One hour")
    nodes["node:A"] = replace(nodes["node:A"], relations={"children": Relation("children", tuple(nodes)[1:])})
    return SimpleNamespace(
        native=NativeObservation("macos", "session", str(clock), (clock, clock), ("node:A",), nodes),
        refs={}, roots=(),
    )


def test_content_comparison_ignores_capture_clocks_and_focus_but_keeps_children_and_values():
    before = snapshot()
    assert same_window_content(before, snapshot(focused=True, clock=2))
    assert not same_window_content(before, snapshot(expanded=True))
    after = snapshot()
    nodes = dict(after.native.nodes)
    nodes["button"] = replace(nodes["button"], states=States(checked="on"))
    after.native = replace(after.native, nodes=nodes)
    assert not same_window_content(before, after)
    nodes["button"] = replace(before.native.nodes["button"], text=TextInfo(source="new capture", content="New label"))
    after.native = replace(after.native, nodes=nodes)
    assert not same_window_content(before, after)


@pytest.mark.parametrize("changed_first,changed_later,failure", [
    (False, True, None), (True, True, None), (False, False, None),
    *[(False, True, stage) for stage in ("budget", "wait", "directory", "collect", "cancel")],
])
def test_unchanged_click_observation_refreshes_once_before_publication(
    monkeypatch, changed_first, changed_later, failure
):
    a = window("A")
    catalog = directory(a, focused="A")
    controller, app, state = setup(catalog)
    original = member_target(state, a)
    baseline = snapshot()
    first = snapshot(expanded=changed_first, focused=True, clock=2)
    second = snapshot(expanded=changed_later, focused=True, clock=3)

    def observation(tree):
        return AppObservation(
            selection=WorkingWindow("application", "Fixture", original), target=original,
            applications=None, source_directory=catalog, directory=catalog, tree=tree, image=None,
            document=DisplayDocument.join(()), refs={}, windows={}, gaps=(), scope_confirmed=True,
        )

    controller.reader.read = AsyncMock(side_effect=[observation(first), observation(second)])
    controller.reader.reader.wait_for_change = AsyncMock(return_value="poll")
    pipeline = InterfaceReadPipeline(controller)
    pipeline.preparation.run = lambda _: SimpleNamespace(roots=())
    monkeypatch.setattr("computer_use.interaction.read.views.pipeline.remember", lambda *_: None)
    monkeypatch.setattr(
        "computer_use.interaction.read.views.pipeline.tree_document",
        lambda _c, _t, tree, **_: DisplayDocument.join(("Expanded" if "choice" in tree.native.nodes else "Closed",)),
    )
    resolve = AsyncMock(return_value=(catalog, None))
    monkeypatch.setattr("computer_use.interaction.read.views.pipeline.resolve", resolve)
    if failure == "budget":
        def expired():
            raise TimeoutError("shared suffix budget exhausted")
        controller.port.calls.claim = lambda _: nullcontext(expired)
    if failure in ("wait", "cancel"):
        controller.reader.reader.wait_for_change.side_effect = (
            asyncio.CancelledError() if failure == "cancel" else TimeoutError("wait expired")
        )
    if failure == "directory":
        resolve.side_effect = TimeoutError("directory expired")
    if failure == "collect":
        controller.reader.read.side_effect = [observation(first), TimeoutError("new capture expired")]
    related = RelatedRead(original, (WindowIdentity.of(a),), before_tree=baseline, settle=True)
    events = []
    set_observation_handler(events.append)
    try:
        if failure == "cancel":
            with pytest.raises(asyncio.CancelledError):
                asyncio.run(pipeline._collect(app, "tree", False, None, catalog, None, None, related))
            assert not events
            return
        result = asyncio.run(pipeline._collect(app, "tree", False, None, catalog, None, None, related))
    finally:
        set_observation_handler(None)
    assert len(events) == 1
    assert events[0]["observation"]["state"] is result
    if failure:
        assert "Closed" in result.text
        assert observation_data(result)["partial"]
        assert observation_data(result)["gaps"][-1].component == "refresh"
        return
    assert ("Expanded" if changed_first or changed_later else "Closed") in result.text
    assert controller.reader.read.await_count == (1 if changed_first else 2)
    assert controller.reader.reader.wait_for_change.await_count == (0 if changed_first else 1)
    assert resolve.await_count == (0 if changed_first else 1)
