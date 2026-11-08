# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""An unchanged click result is observation evidence, not a wait/replay request."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from computer_use.interaction.read.model import AppObservation
from computer_use.interaction.read.presentation.document import DisplayDocument
from computer_use.interaction.read.views.pipeline import InterfaceReadPipeline, member_target
from computer_use.interaction.read.views.related import RelatedRead
from computer_use.native.scopes import WorkingWindow
from computer_use.protocol.observation.model import NativeNode, NativeObservation, Relation
from computer_use.session.events import set_observation_handler
from computer_use.session.interfaces import WindowIdentity
from test_d34_read_contracts import directory, setup, window


@pytest.mark.parametrize("expanded", [False, True])
def test_postwrite_snapshot_is_delivered_once_without_sleeping_for_business_change(monkeypatch, expanded):
    a = window("A")
    catalog = directory(a, focused="A")
    controller, app, state = setup(catalog)
    original = member_target(state, a)
    nodes = {
        "node:A": NativeNode(role="window"),
        "button": NativeNode(role="button", name="Duration"),
    }
    if expanded:
        nodes["choice"] = NativeNode(role="list_item", name="One hour")
    nodes["node:A"] = replace(nodes["node:A"], relations={"children": Relation("children", tuple(nodes)[1:])})
    captured = SimpleNamespace(
        native=NativeObservation("macos", "session", "actual-read", (1, 2), ("node:A",), nodes),
        refs={}, roots=(),
    )
    observed = AppObservation(
        selection=WorkingWindow("application", "Fixture", original), target=original,
        applications=None, source_directory=catalog, directory=catalog, tree=captured, image=None,
        document=DisplayDocument.join(()), refs={}, windows={}, gaps=(), scope_confirmed=True,
    )
    controller.reader.read = AsyncMock(return_value=observed)
    controller.reader.reader.wait_for_change = AsyncMock(side_effect=AssertionError("Unchanged is not pending"))
    pipeline = InterfaceReadPipeline(controller)
    pipeline.preparation.run = lambda _value, _control=None: SimpleNamespace(
        roots=(), snapshot=SimpleNamespace(collection=None),
    )
    monkeypatch.setattr("computer_use.interaction.read.views.pipeline.remember", lambda *_: None)
    monkeypatch.setattr(
        "computer_use.interaction.read.views.pipeline.tree_document",
        lambda *_args, **_: DisplayDocument.join(("Expanded" if expanded else "Closed",)),
    )
    resolve = AsyncMock(side_effect=AssertionError("No second full read"))
    monkeypatch.setattr("computer_use.interaction.read.views.pipeline.resolve", resolve)
    events = []
    set_observation_handler(events.append)
    try:
        result = asyncio.run(pipeline._collect(
            app, "tree", False, None, catalog, None, None,
            RelatedRead(original, (WindowIdentity.of(a),)),
        ))
    finally:
        set_observation_handler(None)
    assert ("Expanded" if expanded else "Closed") in result.text
    assert len(events) == 1 and events[0]["observation"]["state"] is result
    assert controller.reader.read.await_count == 1
    controller.reader.reader.wait_for_change.assert_not_awaited()
    resolve.assert_not_awaited()
