# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Diff semantics and detached baselines across native observations."""

import gc
import weakref
from collections import deque
from dataclasses import replace
from pathlib import Path
from unittest.mock import PropertyMock, patch

import pytest
from computer_use.identity import WeakIdentityMap
from computer_use.interaction.read.delivery.diff import baseline
from computer_use.interaction.read.delivery.diff.baseline import ComparisonDocument, ComparisonTree
from computer_use.interaction.read.presentation.document import DisplayDocument
from computer_use.interaction.read.presentation.rendering.document import prepare_document
from computer_use.pipeline.core import Control
from computer_use.session.observation import peek_observation_text
from computer_use.session.operations import OperationBudget
from computer_use.session.operations import OperationDeadlineExceeded
from computer_use_mcp.output import ObservationOutput, ObservationPresenter
from computer_use_mcp.runtime import Runtime

from regression.compatibility.test_mcp_default_diff_delivery import fixture


@pytest.fixture
def observe(native_read):
    capture, _ = fixture()

    def observe(value="Initial", **options):
        return capture(value, publish=False, **options)

    return observe


@pytest.fixture
def presenter():
    output = ObservationPresenter()
    try:
        yield output
    finally:
        output.close()


def deliver(presenter, observation):
    text = presenter.text(observation)
    presenter.delivered(observation)
    return text


def test_new_related_roots_do_not_replace_known_window(presenter, observe):
    deliver(presenter, observe())
    text = deliver(presenter, observe("Changed", related="row"))
    assert "Tree: diff" in text and "Changed" in text and "Related option" in text
    assert "Related elements (rows of" in text
    assert "Full tree" not in text and "Unchanged unrelated control" not in text
    assert "Tree: unchanged" in deliver(presenter, observe("Changed", related="row"))


def test_relation_owner_change_is_reported_without_node_value_change(presenter, observe):
    deliver(presenter, observe(related="row"))
    after = observe(related="row", owner="other")
    text = deliver(presenter, after)
    owner = next(ref for ref, native in after["refs"].items() if native == "other")
    assert "Tree: diff" in text and f"Related elements (rows of {owner})" in text
    assert "Related option" in text and "Full tree" not in text


def test_partial_children_absence_diff_is_not_a_removal(presenter, observe):
    before = observe()
    missing = next(ref for ref, native in before["refs"].items() if native == "other")
    deliver(presenter, before)
    partial = deliver(presenter, observe(short=True, partial=True, incomplete=True))
    assert f"Not observed in this read: {missing}" in partial
    assert "Removed element IDs" not in partial
    deliver(presenter, observe())
    assert f"Removed element IDs: {missing}" in deliver(presenter, observe(short=True))


def test_unchanged_incomplete_tree_keeps_current_child_coverage_while_omitting_steady_rows(presenter, observe):
    from types import SimpleNamespace

    from computer_use.interaction.read.pages import ContentPages
    from computer_use.interaction.read.views.publication import tree_document
    from computer_use.native.contracts import Binding
    from computer_use.session.observation import lazy_observation

    controller = SimpleNamespace(pages=ContentPages(), transport=SimpleNamespace(capabilities=("nativeRelationPages",)))

    def page_observation(cursor):
        observation = observe(short=True, partial=True, incomplete=True, child_cursor=cursor)
        prepared = observation["document"].parts[0].prepared
        snapshot = prepared.snapshot
        target = Binding(snapshot.session, "instance", "window", snapshot.surface, "window")
        controller.pages.remember(target, snapshot)
        document = tree_document(controller, target, snapshot, prepared=prepared)
        return dict(observation, document=document, state=lazy_observation(document)), target

    old, _ = page_observation("previous-children")
    assert "Initial" in deliver(presenter, old)
    current, target = page_observation("current-children")
    text = deliver(presenter, current)
    assert "Tree: unchanged" in text
    assert "Children:" in text and "partial" in text
    assert 'cursor="p2"' in text and 'cursor="p1"' not in text
    assert controller.pages.resolve("p2", target)["cursor"] == "current-children"
    assert "Initial" not in text
    assert "Initial" in current["state"].text


def test_unchanged_tree_keeps_current_collection_note_and_related_context(presenter, observe):
    from computer_use.interaction.read.views.publication import publish
    from computer_use.native.contracts import Binding

    def published(observation):
        document = observation["document"]
        snapshot = document.parts[0].prepared.snapshot
        binding = Binding(snapshot.session, "instance", "window", snapshot.surface, "window")
        deliveries = []
        publish(
            observation["source"],
            None,
            document,
            trees=((binding, snapshot),),
            scope=observation["scope"],
            on_observation=deliveries.append,
        )
        (delivery,) = deliveries
        return delivery

    deliver(presenter, published(observe(related="row")))
    current = published(observe(related="row", collection_issue="capture-local root-query timeout"))
    text = deliver(presenter, current)
    assert "Tree: unchanged" in text
    assert "Related elements (rows of" in text
    assert "capture-local root-query timeout" in text
    assert "Unchanged unrelated control" not in text and "Related option" not in text
    assert "Unchanged unrelated control" in current["state"].text and "Related option" in current["state"].text


@pytest.mark.parametrize("max_bytes,max_scopes", [(0, 0), (200_000, 1), (1, 32)])
def test_memory_pressure_spills_and_keeps_delivered_scope(max_bytes, max_scopes, observe):
    presenter = ObservationPresenter(max_bytes=max_bytes, max_scopes=max_scopes)
    try:
        first = observe(scope="one")
        deliver(presenter, first)
        deliver(presenter, observe(scope="two"))
        spill = presenter._baselines.directory
        assert spill is not None
        path = Path(spill.name)
        changed = deliver(presenter, observe("Changed", scope="one"))
        assert "Tree: diff" in changed and "Changed" in changed
        assert "Unchanged unrelated control" not in changed
        assert "Tree: unchanged" in deliver(presenter, observe("Changed", scope="one"))
        presenter.invalidate(first["source"])
        assert "Tree: diff" not in deliver(presenter, observe("Again", scope="one"))
    finally:
        presenter.close()
    assert not path.exists()


def test_unpublished_pending_capture_does_not_advance_baseline(presenter, observe):
    deliver(presenter, observe())
    pending = ObservationOutput(presenter)
    pending.observe(observe("Undelivered"))
    (retained,) = pending.take()
    assert "Undelivered" in presenter.text(retained)
    assert "Tree: unchanged" in presenter.text(observe())
    assert "Undelivered" in presenter.text(observe("Undelivered"))


def test_detached_comparison_size_is_bounded_and_roundtrips_through_spill(observe):
    document = observe("x" * 200_000)["document"]
    comparison = ComparisonDocument.capture(document)
    assert comparison.estimated_bytes < 5_000
    assert ComparisonDocument.from_spill_data(comparison.spill_data()).to_data() == comparison.to_data()


def test_comparison_cache_uses_exact_capture_identity(observe):
    tree = observe()["document"].parts[0]
    first = ComparisonTree.capture(tree)
    assert ComparisonTree.capture(tree) is first
    copied = ComparisonTree.capture(replace(tree))
    assert copied is not first and copied.to_data() == first.to_data()
    second = ComparisonTree.capture(observe("Changed")["document"].parts[0])
    assert second is not first and second.to_data() != first.to_data()


def test_tree_scope_and_display_options_keep_comparison_separate(observe):
    tree = observe(scope="one")["document"].parts[0]
    normal = ComparisonTree.capture(tree)
    details = ComparisonTree.capture(replace(tree, details=True))
    indented = ComparisonTree.capture(replace(tree, indent=2))
    other = ComparisonTree.capture(replace(tree, scope=("two",)))
    assert normal.to_data() != details.to_data()
    assert normal.to_data() != indented.to_data()
    assert normal.scope == ("one",) and other.scope == ("two",)
    assert len({id(normal), id(details), id(indented), id(other)}) == 4


def test_detached_cache_does_not_retain_discarded_display_tree(observe):
    tree = observe()["document"].parts[0]
    original, prepared = weakref.ref(tree), weakref.ref(tree.prepared)
    comparison = ComparisonTree.capture(tree)
    del tree
    gc.collect()
    assert original() is None and prepared() is None
    assert comparison.to_data()


def test_stale_numeric_identity_cannot_recover_another_scopes_baseline(observe, monkeypatch):
    cache = WeakIdentityMap()
    monkeypatch.setattr(baseline, "_comparison_trees", cache)
    previous = observe(scope="old-window")["document"].parts[0]
    old = ComparisonTree.capture(previous)
    current = observe(scope="current-window")["document"].parts[0]
    cache._items[id(current)] = (weakref.ref(previous), old)
    result = ComparisonTree.capture(current)
    assert result is not old and result.scope == ("current-window",)
    assert ComparisonTree.capture(previous) is old


def test_incremental_then_explicit_full_keeps_one_baseline(presenter, observe):
    first = deliver(presenter, observe(related="row"))
    assert "Unchanged unrelated control" in first and "Related option" in first
    changed = observe("Changed", related="row")
    diff = deliver(presenter, changed)
    assert "Tree: diff" in diff and "Changed" in diff and "Related option" not in diff
    assert "Tree: unchanged" in deliver(presenter, observe("Changed", related="row"))
    full = deliver(presenter, dict(changed, full=True))
    assert "Related option" in full and "Changed" in full and "Tree: diff" not in full
    assert str(changed["state"].text) == full
    next_diff = presenter.text(observe("Next change", related="row"))
    assert "Tree: diff" in next_diff and "Next change" in next_diff and "Related option" not in next_diff


def test_render_cache_releases_discarded_capture(observe):
    observation = observe()
    prepared = weakref.ref(observation["document"].parts[0].prepared)
    assert "Unchanged unrelated control" in observation["state"].text
    del observation
    gc.collect()
    assert prepared() is None


def test_full_body_is_rendered_once_per_exact_prepared_capture(observe, monkeypatch):
    first, second = observe(), observe()
    first_handle = first["document"].parts[0].prepared.handle
    second_handle = second["document"].parts[0].prepared.handle
    native = type(first_handle)
    render = native.render
    rendered = []

    def trace(handle, include_details, *, checkpoint=None):
        rendered.append(handle)
        return render(handle, include_details, checkpoint=checkpoint)

    monkeypatch.setattr(native, "render", trace)
    first_draft = prepare_document(first["document"], public_api=True)
    assert rendered == [first_handle]
    assert "Unchanged unrelated control" in first["state"].text
    repeated_draft = prepare_document(first["document"], public_api=True)
    assert rendered == [first_handle]
    assert next(iter(first_draft.snapshots.values())) is next(iter(repeated_draft.snapshots.values()))
    another_draft = prepare_document(second["document"], public_api=True)
    assert rendered == [first_handle, second_handle]
    assert next(iter(first_draft.snapshots.values())) is not next(iter(another_draft.snapshots.values()))


@pytest.mark.parametrize("stage", ["comparison", "render"])
def test_expiry_during_native_boundary_does_not_commit_the_result(observe, monkeypatch, stage):
    observation = observe()
    document = observation["document"]
    tree = document.parts[0]
    native = type(tree.prepared.handle)
    operation = getattr(native, stage)
    invocations = []

    def invoke(handle, *args, checkpoint=None):
        result = operation(handle, *args, checkpoint=checkpoint)
        invocations.append(handle)
        if len(invocations) == 1:
            now[0] = 2.0
        return result

    monkeypatch.setattr(native, stage, invoke)
    now = [0.0]
    deadline = OperationBudget(1000, clock=lambda: now[0])

    def attempt(budget):
        if stage == "comparison":
            return ComparisonTree.capture(tree, checkpoint=budget.checkpoint)
        return prepare_document(document, public_api=True, control=Control(remaining=budget.remaining))

    with pytest.raises(OperationDeadlineExceeded):
        attempt(deadline)
    assert peek_observation_text(observation["state"]) is None
    assert invocations == [tree.prepared.handle]
    # A later demand can render the same original snapshot with a valid budget.
    attempt(OperationBudget(1000, clock=lambda: now[0]))
    assert invocations == [tree.prepared.handle, tree.prepared.handle]


@pytest.mark.parametrize("materialized", [False, True])
def test_observation_diagnostics_do_not_force_full_text(observe, materialized):
    observation = observe()
    original = observation["state"].text if materialized else None
    events = []
    runtime = object.__new__(Runtime)
    runtime.send = events.append
    run = {"id": "fixture", "observationLogs": deque((observation,))}
    assert peek_observation_text(observation["state"]) == original
    with patch.object(
        DisplayDocument, "text", new_callable=PropertyMock, side_effect=AssertionError("full text rendered")
    ):
        runtime.log_observations(run, materializedOnly=materialized)
    assert len(events) == 1
    diagnostic = events[0]["diagnostic"]
    if materialized:
        assert diagnostic["text"] == original
    else:
        assert diagnostic["textMaterialized"] is False
        assert "text" not in diagnostic
        assert diagnostic["presentation"] == observation["document"].summary_data()
    assert not run["observationLogs"]


def test_prepared_captures_rebase_on_delivered_state_after_budget_expires(presenter, observe):
    now = [0.0]
    budget = OperationBudget(1000, clock=lambda: now[0])
    initial = observe("Zero", related="row")
    deliver(presenter, initial)
    first = presenter.prepare(observe("One", related="row"), budget)
    second = presenter.prepare(observe("Zero", related="row"), budget)
    assert first["operationBudget"] is budget and second["operationBudget"] is budget
    assert "Tree: unchanged" in presenter.text(initial)
    now[0] = 10.0
    assert budget.expired
    first_text = deliver(presenter, first)
    second_text = deliver(presenter, second)
    explicit_full = presenter.text(dict(second, full=True))
    assert "One" in first_text and "Tree: diff" in first_text
    assert "Zero" in second_text and "Tree: diff" in second_text
    assert "Related option" in explicit_full and "Zero" in explicit_full


def test_coalescing_prepared_captures_keeps_last_delivered_baseline(presenter, observe):
    deliver(presenter, observe("Zero"))
    pending = ObservationOutput(presenter)
    pending.observe(presenter.prepare(observe("One")))
    pending.observe(presenter.prepare(observe("Zero")))
    (retained,) = pending.take()
    output = deliver(presenter, retained)
    assert "Tree: unchanged" in output and "One" not in output


def test_lost_redundant_row_query_does_not_publish_unchanged_cells(presenter, native_read):
    import json

    from computer_use.identity import Record
    from computer_use.interaction.read.accessibility.preparation import prepare_snapshot
    from computer_use.interaction.read.accessibility.snapshot import NativeSnapshot
    from computer_use.interaction.read.delivery.coverage import document_coverage
    from computer_use.session.observation import lazy_observation

    source = Record()

    def property_(key, value, native_type="CFString"):
        return {"key": ["macos.ax", key], "value": {"nativeType": native_type, "data": value}}

    def relation(key, *targets):
        result = property_(
            key, {"items": [{"nativeType": "AXUIElement", "data": {"ref": token}} for token in targets]}, "CFArray"
        )
        result["read"] = {
            "complete": True,
            "details": [
                [key, {"nativeType": "provider", "data": value}]
                for key, value in (
                    ("relation", True),
                    ("certainty", "confirmed"),
                    ("state", "complete"),
                    ("offset", 0),
                    ("total", len(targets)),
                )
            ],
        }
        return result

    def index_range(key, start):
        return property_(
            key,
            {
                "fields": [
                    ["location", {"nativeType": "CFNumber", "data": start}],
                    ["length", {"nativeType": "CFNumber", "data": 1}],
                ]
            },
            "AXValue.CFRange",
        )

    def entity(token, role, name, *properties):
        return {
            "id": token,
            "kind": "element",
            "nativeType": "AXUIElement",
            "source": "macos.ax",
            "properties": [property_("AXRole", role), property_("AXTitle", name), *properties],
        }

    def capture(rows_timeout):
        rows = relation("AXRows", "r1", "r2")
        if rows_timeout:
            rows["read"] = {
                "complete": False,
                "details": [
                    [key, {"nativeType": "provider", "data": value}]
                    for key, value in (
                        ("relation", True),
                        ("certainty", "inferred"),
                        ("state", "timeout"),
                        ("total", 2),
                    )
                ],
            }
        nodes = [
            entity("window", "AXWindow", "Table window", relation("AXChildren", "outline")),
            entity(
                "outline",
                "AXOutline",
                "Navigation",
                property_("AXRowCount", 2, "CFNumber"),
                property_("AXColumnCount", 1, "CFNumber"),
                relation("AXParent", "window"),
                relation("AXChildren", "r1", "r2"),
                rows,
            ),
        ]
        for number in (1, 2):
            nodes.append(
                entity(
                    f"r{number}",
                    "AXRow",
                    f"Row {number}",
                    relation("AXParent", "outline"),
                    relation("AXChildren", f"c{number}"),
                    property_("AXIndex", number - 1, "CFNumber"),
                    property_("AXDisclosureLevel", 1, "CFNumber"),
                )
            )
            nodes.append(
                entity(
                    f"c{number}",
                    "AXCell",
                    f"Cell {number}",
                    relation("AXParent", f"r{number}"),
                    relation("AXChildren"),
                    index_range("AXRowIndexRange", number - 1),
                    index_range("AXColumnIndexRange", 0),
                )
            )
        body = json.dumps(
            {
                "kind": "observation",
                "session": "table",
                "platform": "macos",
                "roots": ["window"],
                "nodes": nodes,
                "read": {
                    "intervalNs": [1, 2],
                    "details": [
                        ["observation_id", {"nativeType": "CFString", "data": str(rows_timeout)}],
                        ["surface", {"nativeType": "CFString", "data": "table"}],
                    ],
                },
            }
        ).encode()
        handle = native_read.open_read_frame("json", body, len(body)).capture("window", [])
        snapshot = NativeSnapshot.number(handle, {token: str(i) for i, token in enumerate(handle.source_tokens)})
        document = DisplayDocument.tree(prepare_snapshot(snapshot)).scoped(("table-window",))
        return {
            "source": source,
            "scope": "table-window",
            "view": "window",
            "full": False,
            "publicApi": True,
            "document": document,
            "state": lazy_observation(document, None),
            "refs": dict(snapshot.refs),
            "coverage": document_coverage(document),
        }

    before, after = capture(False), capture(True)
    assert all(name in before["state"].text for name in ("Cell 1", "Cell 2", "Row 1", "Row 2"))
    assert "Outline: Rows" in before["state"].text
    assert "Outline: Rows" not in after["state"].text
    deliver(presenter, before)
    text = deliver(presenter, after)
    assert "Tree: unchanged" in text and "rows (timeout" in text
    assert all(name not in text for name in ("Cell 1", "Cell 2", "Row 1", "Row 2"))
