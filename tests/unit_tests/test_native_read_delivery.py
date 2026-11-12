# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Verify opaque native delivery across complete, incremental, and spilled observations."""

import json
import sys
from types import SimpleNamespace

import pytest
from computer_use.identity import Record
from computer_use.interaction.read.accessibility.snapshot import NativeSnapshot
from computer_use.interaction.read.delivery import coverage
from computer_use.interaction.read.delivery.diff.baseline import ComparisonDocument, ComparisonTree
from computer_use.interaction.read.delivery.diff.formatting import plan_document_diff
from computer_use.interaction.read.numbering.pipeline import number_snapshots
from computer_use.interaction.read.numbering.references import DisplayReferences
from computer_use.interaction.read.presentation.content import omit_public_details_hint
from computer_use.interaction.read.presentation.delta import RustDeltaPlan
from computer_use.interaction.read.presentation.document import DisplayDocument
from computer_use.interaction.read.accessibility.preparation import SnapshotRenderRequest
from computer_use.interaction.read.accessibility.preparation import PreparedSnapshot, prepare_snapshot
from computer_use.interaction.read.presentation.rendering.document import prepare_document, render_document
from computer_use.json import dumps, utf8
from computer_use.pipeline.core import Control
from computer_use.interaction.read.accessibility._native import require_read_engine
from computer_use.session.observation import lazy_observation
from computer_use.session.observation_data import retain_observation_data
from computer_use.session.operations import OperationDeadlineExceeded
from computer_use_mcp.baseline_store import DeliveredBaselineStore
from computer_use_mcp.output import ObservationOutput, ObservationPresenter


pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="macOS native read delivery")


def _capture(extension, name, *, sibling=None, child=False, children_total=None):
    def string(value):
        return {"nativeType": "CFString", "data": value}

    def element(token, title, role="AXWindow"):
        return {
            "id": token,
            "kind": "element",
            "nativeType": "AXUIElement",
            "source": "macos.ax",
            "properties": [
                {"key": ["macos.ax", "AXRole"], "value": string(role)},
                {"key": ["macos.ax", "AXTitle"], "value": string(title)},
            ],
        }

    raw = {
        "kind": "observation",
        "session": "session",
        "platform": "macos",
        "roots": ["root"],
        "nodes": [element("root", name)],
        "read": {
            "intervalNs": [1, 2],
            "details": [["surface", string("surface")], ["observation_id", string("capture")]],
        },
    }
    if sibling is not None:
        raw["roots"].append("sibling")
        raw["nodes"].append(element("sibling", sibling))
    if children_total is not None:
        items = []
        if child:
            raw["nodes"].append(element("child", "Removed child", "AXButton"))
            items.append({"nativeType": "AXUIElement", "data": {"ref": "child"}})
        complete = len(items) == children_total
        details = {
            "relation": True, "certainty": "confirmed", "state": "complete" if complete else "partial",
            "total": children_total,
        }
        raw["nodes"][0]["properties"].append({
            "key": ["macos.ax", "AXChildren"],
            "value": {"nativeType": "CFArray", "data": {"items": items}},
            "read": {
                "complete": complete,
                "details": [[key, {"nativeType": "provider", "data": value}] for key, value in details.items()],
            },
        })
    frame = extension.open_read_frame("json", json.dumps(raw).encode(), 8192)
    capture = frame.capture("window", [])
    return NativeSnapshot.number(capture, {token: str(ref) for ref, token in enumerate(capture.source_tokens)})


def _document(snapshot, *, details=False):
    return DisplayDocument.tree(prepare_snapshot(snapshot), details=details).scoped(
        ("session", None, None, "surface", None)
    )


def test_native_delivery_keeps_exact_text_and_detached_baseline_through_spill(monkeypatch):
    extension = require_read_engine()

    before_native = _capture(extension, "原始标题")
    after_native = _capture(extension, "Updated")
    before, after = _document(before_native), _document(after_native)
    assert before.text == "0 window 原始标题, Secondary Actions: <unavailable>"
    assert before.estimated_bytes == len(utf8(dumps(before.to_data())))
    assert "Native read issues" in _document(before_native, details=True).text

    baseline = ComparisonDocument.capture(before)
    assert baseline.estimated_bytes == len(utf8(dumps(baseline.to_data())))

    store = DeliveredBaselineStore(max_bytes=0, max_scopes=0)
    try:
        def require_native(*_args):
            pytest.fail("native baseline spill attempted to materialize comparison rows in Python")

        with monkeypatch.context() as patch:
            patch.setattr(ComparisonTree, "to_data", require_native)
            store.publish("scope", {"document": baseline, "bytes": baseline.estimated_bytes})
            restored = store.get("scope")["document"]
    finally:
        store.close()
    assert isinstance(restored.trees[0], ComparisonTree)
    assert dumps(restored.to_data()) == dumps(baseline.to_data())

    previous_refs, current_refs = before_native.refs, after_native.refs
    draft = prepare_document(after, public_api=True)
    expected_diff = "~0 window Updated, Secondary Actions: <unavailable>\nTree: diff"
    for prior in (baseline, restored):
        plan = plan_document_diff(prior, after, previous_refs, current_refs)
        assert isinstance(plan, RustDeltaPlan)
        assert render_document(after, plan, public_api=True, prepared=draft) == expected_diff

    unchanged = plan_document_diff(after, after, current_refs, current_refs)
    assert "Tree: unchanged" in render_document(after, unchanged, public_api=True, prepared=draft)

    unchanged_sibling = _document(_capture(extension, "Unchanged")).scoped(("other-scope",)).indented()
    grouped_before = DisplayDocument.join((before.indented(), unchanged_sibling))
    grouped_after = DisplayDocument.join((after.indented(), unchanged_sibling))
    grouped_plan = plan_document_diff(grouped_before, grouped_after, previous_refs, current_refs)
    assert "Updated" in render_document(grouped_after, grouped_plan)
    assert "Tree: diff" in render_document(grouped_after, grouped_plan)


def test_native_delivery_preserves_missing_reference_coverage_after_spill():
    extension = require_read_engine()

    source = Record()

    def observation(snapshot, *, partial=False):
        document = _document(snapshot)
        state = lazy_observation(document)
        retain_observation_data(state, (), snapshot.refs, partial=partial)
        return {
            "source": source, "scope": "window", "view": "window", "state": state,
            "document": document, "refs": snapshot.refs, "full": False, "publicApi": True,
            "coverage": coverage.document_coverage(document),
        }

    before = _capture(extension, "Form", child=True, children_total=1)
    complete = _capture(extension, "Updated", children_total=0)
    partial = _capture(extension, "Updated", children_total=1)
    missing = next(ref for ref, token in before.refs.items() if token == "child")

    def deliver():
        presenter = ObservationPresenter(max_bytes=0, max_scopes=0)
        try:
            preceding = observation(before)
            presenter.delivered(preceding)
            complete_observation = observation(complete)
            partial_observation = observation(partial, partial=True)
            pending = ObservationOutput(presenter)
            incoming = {presenter.tree_key(partial_observation, partial_observation["document"].parts[0])}
            assert not pending.superseded(preceding, partial_observation, incoming)
            return presenter.text(complete_observation), presenter.text(partial_observation)
        finally:
            presenter.close()

    expected = deliver()
    assert f"Removed element IDs: {missing}" in expected[0]
    assert f"Not observed in this read: {missing}" in expected[1]


def test_native_root_lifecycle_keeps_the_originating_control_and_budget():
    extension = require_read_engine()

    snapshot = _capture(extension, "Lifecycle fixture")
    events, budget_checks = [], []

    def remaining():
        budget_checks.append(events[-1].path)
        return 30000

    control = Control(
        remaining=remaining, diagnostic=events.append, path=("Read",), call_id="existing-call", attempt=2,
        branch="selected",
    )
    prepared = prepare_snapshot(snapshot, _control=control)
    assert budget_checks and all(path == ("Read", "PrepareSnapshotRoots") for path in budget_checks)
    assert [(event.path, event.phase) for event in events] == [
        (("Read", "PrepareSnapshotRoots"), "enter"),
        (("Read", "PrepareSnapshotRoots"), "exit"),
    ]

    document = DisplayDocument.tree(prepared)
    events.clear()
    prepare_document(document, control=control)
    root_events = [event for event in events if event.path[-1] == "RenderPreparedRoots"]
    assert [event.phase for event in root_events] == ["enter", "exit"]
    assert root_events[0].path == (
        "Read", "PrepareDocumentRenderingPipeline", "RenderDocumentSnapshotSources",
        "RenderPreparedSnapshotPipeline", "RenderPreparedRoots",
    )
    assert all(
        event.call_id == "existing-call" and event.attempt == 2 and event.branch == "selected"
        for event in events
    )
    assert budget_checks[-1] == root_events[0].path

    events.clear()
    prepare_document(document, control=control)
    assert not any(event.path[-1] == "RenderPreparedRoots" for event in events)

    events.clear()
    number_snapshots((snapshot,), DisplayReferences("session"), _control=control)
    preview_events = [event for event in events if event.path[-1] == "PrepareSnapshotRoots"]
    assert [event.phase for event in preview_events] == ["enter", "exit"]
    assert preview_events[0].path[-2:] == ("PreviewSnapshot", "PrepareSnapshotRoots")
    assert preview_events[0].call_id == control.call_id
    assert preview_events[0].path in budget_checks
    rules = [
        event for event in events
        if event.path[-2:] == ("CompressionTraversal", "promote_description")
    ]
    assert [event.phase for event in rules] == ["enter", "exit"]
    assert all(event.call_id == control.call_id and event.timestamp.endswith("Z") for event in rules)
    events.clear()
    checks = 0

    def expire_in_native_preparation():
        nonlocal checks
        checks += 1
        if checks == 2:
            raise OperationDeadlineExceeded("original deadline")
        return 30000

    with pytest.raises(OperationDeadlineExceeded, match="original deadline"):
        prepare_snapshot(snapshot, _control=control.with_remaining(expire_in_native_preparation))
    assert checks == 2
    assert [(event.path[-1], event.phase, event.error_type) for event in events] == [
        ("PrepareSnapshotRoots", "enter", None),
        ("PrepareSnapshotRoots", "error", "OperationDeadlineExceeded"),
    ]


def test_native_preparation_fallback_keeps_the_details_hint():
    snapshot = object()
    request = SnapshotRenderRequest(snapshot, compact=True, public_api=True)
    successful = SimpleNamespace(has_uncompressed_preparation=lambda: False)
    fallback = SimpleNamespace(has_uncompressed_preparation=lambda: True)
    assert omit_public_details_hint(PreparedSnapshot(request, snapshot, successful))
    assert not omit_public_details_hint(PreparedSnapshot(request, snapshot, fallback))
