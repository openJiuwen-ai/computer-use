# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""An unchanged click result is observation evidence, not a wait/replay request."""

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from computer_use.interaction.query.instances.scope import adopt
from compat_read_fixtures import children, element, scalar, snapshot
from computer_use.interaction.read.model import AppObservation
from computer_use.interaction.read.presentation.document import DisplayDocument
from computer_use.interaction.read.views.pipeline import InterfaceReadPipeline, member_target
from computer_use.interaction.read.views.related import RelatedRead
from computer_use.interaction.requests import ExecuteAction
from computer_use.interaction.write.request.observation import selection_target
from computer_use.native.contracts import Intent
from computer_use.native.scopes import WorkingWindow
from computer_use.session.events import set_observation_handler
from computer_use.session.interfaces import WindowIdentity
from regression.test_compat_d34_read_contracts import directory, setup, window


pytestmark = pytest.mark.usefixtures("native_read")


@pytest.mark.parametrize("expanded", [False, True])
def test_postwrite_snapshot_is_delivered_once_without_sleeping_for_business_change(monkeypatch, expanded):
    a = window("A")
    catalog = directory(a, focused="A")
    controller, app, state = setup(catalog)
    original = member_target(state, a)
    root = element("node:A", "AXWindow", "Expanded" if expanded else "Closed")
    members = [element("button", "AXButton", "Duration")]
    if expanded:
        members.append(element("choice", "AXRow", "One hour"))
    root["properties"].append(children([item["id"] for item in members]))
    captured = snapshot(original, nodes=[root, *members], observation_id="actual-read")
    observed = AppObservation(
        selection=WorkingWindow("application", "Fixture", original), target=original,
        applications=None, source_directory=catalog, directory=catalog, tree=captured, image=None,
        document=DisplayDocument.join(()), refs={}, windows={}, gaps=(), scope_confirmed=True,
    )
    controller.reader.read = AsyncMock(return_value=observed)
    controller.reader.reader.wait_for_change = AsyncMock(side_effect=AssertionError("Unchanged is not pending"))
    pipeline = InterfaceReadPipeline(controller)
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


def captured_detail(controller, target, properties, scope_kind):
    node = element(target.node, "AXUnknown", "")
    node["properties"] = [{"key": ["macos.ax", key], "value": scalar(value)} for key, value in properties.items()]
    return snapshot(
        target, nodes=[node], scope=scope_kind,
        references=controller.reader.reader.display_references(target.session),
    )


@pytest.mark.parametrize("selected, reasons", [(True, ()), (True, ("descendant discovery budget",)), (False, ())])
def test_click_related_read_uses_exact_selected_item_or_falls_back(monkeypatch, selected, reasons):
    a = window("A")
    catalog = directory(a, focused="A", reasons=reasons)
    controller, app, state = setup(catalog)
    adopt(controller, app, catalog)
    root = member_target(state, a)
    target = replace(root, node="file-row")
    detail = captured_detail(
        controller, target, {"AXRole": "AXRow", "AXTitle": "selected file", "AXSelected": selected}, None,
    )
    full = captured_detail(controller, root, {"AXRole": "AXWindow", "AXTitle": "host fallback"}, "window")
    controller.reader.reader.read = AsyncMock(return_value=detail)
    controller.reader.read = AsyncMock(return_value=AppObservation(
        selection=WorkingWindow("application", "Fixture", root), target=root,
        applications=None, source_directory=catalog, directory=catalog, tree=full, image=None,
        document=DisplayDocument.join(()), refs={}, windows={}, gaps=(), scope_confirmed=True,
    ))
    monkeypatch.setattr("computer_use.interaction.read.views.pipeline.refresh", AsyncMock(return_value=catalog))
    result = asyncio.run(InterfaceReadPipeline(controller)._collect(
        app, "tree", False, None, catalog, None, None,
        RelatedRead(target, (WindowIdentity.of(a),), selection_target=target),
    ))
    assert controller.reader.reader.read.call_args.args == (target,)
    assert controller.reader.reader.read.call_args.kwargs["max_nodes"] == 4
    assert controller.reader.read.await_count == (0 if selected else 1)
    if selected:
        assert "Selected item observed." in result.text
        assert controller.state(state.owners["A"]).query_target == root
        assert "selected file" in result.text
    if reasons:
        assert reasons[0] in result.text


def test_click_caption_uses_the_confirmed_selectable_cell_only():
    a = window("A")
    _controller, app, state = setup(directory(a, focused="A"))
    root = member_target(state, a)
    target = replace(root, node="filename")
    cell_target = replace(root, node="cell")
    filename = element("filename", "AXTextField", "")
    filename["properties"].append({"key": ["macos.ax", "AXSelected"], "value": scalar(False)})
    cell = element("cell", "AXCell", "")
    cell["properties"].append(children(["filename"]))
    for selected, expected in ((False, cell_target), (True, None)):
        evidence = {"key": ["macos.ax", "AXSelected"], "value": scalar(selected)}
        detail = snapshot(cell_target, nodes=[{**cell, "properties": [*cell["properties"], evidence]}, filename])
        click = ExecuteAction(app, Intent("click", {"button": "left", "count": 1}), target)
        assert selection_target(click, detail, root) == expected


def test_click_then_copy_checks_the_previous_selected_detail_within_its_own_window(monkeypatch):
    a = window("A")
    b = window("B")
    catalog = directory(a, b, focused="A")
    controller, app, state = setup(catalog)
    adopt(controller, app, catalog)
    root = member_target(state, a)
    target = replace(root, node="file-row")
    detail = captured_detail(controller, target, {
        "AXRole": "AXRow", "AXTitle": "selected file", "AXSelected": True,
    }, None)
    controller.reader.reader.read = AsyncMock(return_value=detail)
    controller.reader.read = AsyncMock(side_effect=AssertionError("The host tree is unnecessary"))
    monkeypatch.setattr("computer_use.interaction.read.views.pipeline.refresh", AsyncMock(return_value=catalog))
    read = InterfaceReadPipeline(controller)
    known = (WindowIdentity.of(a), WindowIdentity.of(b))
    asyncio.run(read._collect(
        app, "tree", False, None, catalog, None, None, RelatedRead(target, known, selection_target=target),
    ))

    copy = ExecuteAction(app, Intent("key", {"key": "super+c"}), root)
    captured = controller.state(state.owners["A"])
    selected = selection_target(copy, captured.query_snapshot, captured.query_target)
    assert selected == target
    assert selection_target(replace(copy, target=member_target(state, b)), captured.query_snapshot, root) is None
    result = asyncio.run(read._collect(
        app, "tree", False, None, catalog, None, None, RelatedRead(root, known, selection_target=selected),
    ))
    assert "Selected item observed." in result.text
    assert [call.args for call in controller.reader.reader.read.await_args_list] == [(target,), (target,)]
    controller.reader.read.assert_not_awaited()


def test_return_collects_new_attached_focused_editor_without_rereading_host(monkeypatch):
    a = window("A")
    editor = {
        **window("Inline", parent="A"), "nativeWindow": False, "content": {"kind": "unknown"},
    }
    catalog = directory(a, editor, focused="A", reasons=("descendant discovery budget",))
    controller, app, state = setup(catalog)
    adopt(controller, app, catalog)
    root, target = member_target(state, a), member_target(state, editor)
    detail = captured_detail(controller, target, {
        "AXRole": "AXTextField", "AXValue": "name to edit", "AXFocused": True,
        "AXEditable": True, "AXSelectedText": "name",
    }, "window")
    controller.reader.reader.read = AsyncMock(return_value=detail)
    controller.reader.read = AsyncMock(side_effect=AssertionError("The host tree is unnecessary"))
    monkeypatch.setattr("computer_use.interaction.read.views.pipeline.refresh", AsyncMock(return_value=catalog))
    result = asyncio.run(InterfaceReadPipeline(controller)._collect(
        app, "tree", False, None, catalog, None, None,
        RelatedRead(root, (WindowIdentity.of(a),), seek_editor=True),
    ))
    assert "name to edit" in result.text
    assert "Focused editor observed." in result.text
    assert "descendant discovery budget" in result.text
    assert controller.reader.reader.read.call_args.args == (target,)
    controller.reader.reader.read.assert_awaited_once()
    controller.reader.read.assert_not_awaited()
    assert any(ref["binding"] == target for ref in state.refs.values())
