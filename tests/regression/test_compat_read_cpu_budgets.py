# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Use the caller's deadline and cancellation on a wide Rust-owned read."""

import asyncio

import pytest
from compat_read_fixtures import children, element, snapshot
from computer_use.interaction.read.accessibility.preparation import prepare_snapshot
from computer_use.interaction.read.delivery.diff.baseline import ComparisonDocument
from computer_use.interaction.read.presentation.document import DisplayDocument
from computer_use.interaction.read.presentation.rendering.document import prepare_document
from computer_use.native.contracts import Binding
from computer_use.pipeline.core import Control
from computer_use.session.operations import OperationBudget, OperationDeadlineExceeded


WIDTH = 512


@pytest.fixture
def wide_snapshot(native_read):
    target = Binding("session", "instance", "window", "surface", "root")
    items = [f"setting-{i}" for i in range(WIDTH)]
    # A wide group in one capture exercises the intra-branch checkpoints.
    root = element("root", "AXWindow", "Settings")
    root["properties"].append(children(["group"]))
    group = element("group", "AXGroup", "All settings")
    group["properties"].append(children(items))
    nodes = [root, group, *(element(token, "AXButton", f"Setting {i}") for i, token in enumerate(items))]
    return snapshot(target, nodes=nodes, observation_id="original-wide")


@pytest.mark.parametrize("reason", ["cancel", "timeout"])
@pytest.mark.parametrize("compact", [False, True])
def test_wide_native_preparation_checks_original_budget_before_publishing(wide_snapshot, reason, compact):
    polls = 0
    budget = OperationBudget(20, clock=lambda: polls * 0.005, cancelled=lambda: reason == "cancel" and polls == 4)

    def remaining():
        nonlocal polls
        polls += 1
        return budget.remaining()

    expected = asyncio.CancelledError if reason == "cancel" else OperationDeadlineExceeded
    evidence = []
    frame_info = wide_snapshot.capture.node_view("group", ["relations"])
    with pytest.raises(expected):
        prepare_snapshot(wide_snapshot, compact=compact, _control=Control(remaining=remaining, diagnostic=evidence.append))
    # Python _prepare checks only on entry and on return. This fourth check
    # must happen inside native projection of the captured source.
    assert polls == 4
    assert evidence[-1].path[-1] == "PrepareSnapshotRoots" and evidence[-1].phase == "error"
    assert wide_snapshot.id == "original-wide"
    assert wide_snapshot.capture.node_view("group", ["relations"]) == frame_info
    assert len(frame_info["relations"]["children"]["targets"]) == WIDTH
    assert prepare_snapshot(wide_snapshot, compact=compact).snapshot is wide_snapshot


@pytest.mark.parametrize("stage", ["render", "comparison"])
def test_rejected_wide_delivery_never_caches_interrupted_output(wide_snapshot, stage):
    document = DisplayDocument.tree(prepare_snapshot(wide_snapshot))
    events = []

    def remaining():
        if stage == "comparison" or (events and events[-1].path[-1] == "RenderPreparedRoots"):
            raise OperationDeadlineExceeded("original delivery deadline")
        return 30000

    with pytest.raises(OperationDeadlineExceeded, match="original delivery deadline"):
        if stage == "render":
            prepare_document(document, control=Control(remaining=remaining, diagnostic=events.append))
        else:
            ComparisonDocument.capture(document, checkpoint=remaining)
    if stage == "render":
        events.clear()
        prepare_document(document, control=Control(diagnostic=events.append))
        assert any(event.path[-1] == "RenderPreparedRoots" and event.phase == "enter" for event in events)
        assert f"Setting {WIDTH - 1}" in document.text
    else:
        # A denied comparison did not publish a cached baseline. Rechecking a
        # fresh budget is required when the same source is used again.
        checks = []

        def run():
            checks.append(1)
            return 30000

        assert ComparisonDocument.capture(document, checkpoint=run).trees
        assert len(checks) >= 2


@pytest.mark.parametrize("stage", ["render", "comparison"])
@pytest.mark.parametrize("reason", ["cancel", "timeout"])
def test_wide_native_delivery_polls_cancellation_or_deadline_within_branch(wide_snapshot, stage, reason):
    document = DisplayDocument.tree(prepare_snapshot(wide_snapshot))
    polls, events = 0, []
    budget = OperationBudget(15, clock=lambda: polls * 0.005, cancelled=lambda: reason == "cancel" and polls == 3)

    def remaining():
        nonlocal polls
        if stage == "comparison" or (events and events[-1].path[-1] == "RenderPreparedRoots"):
            polls += 1
        return budget.remaining()

    expected = asyncio.CancelledError if reason == "cancel" else OperationDeadlineExceeded
    stopped = False
    try:
        if stage == "render":
            prepare_document(document, control=Control(remaining=remaining, diagnostic=events.append))
        else:
            ComparisonDocument.capture(document, checkpoint=remaining)
    except expected:
        stopped = True
    assert stopped, f"{stage} completed a {WIDTH}-item branch after {polls} budget polls"
