"""Captured presentation work keeps the caller's cancellation and absolute deadline."""

import asyncio
from collections.abc import Mapping
from dataclasses import replace
from types import SimpleNamespace

import pytest
from computer_use.interaction.read.facts.normalize import normalize_facts
from computer_use.interaction.read.numbering.references import DisplayReferences
from computer_use.interaction.read.presentation.fallback import branch_references
from computer_use.interaction.read.presentation.interface.model import InterfaceBlock
from computer_use.interaction.read.presentation.interface.signatures import interface_layout_signatures
from computer_use.interaction.read.presentation.preparation import (
    PreparedRoot,
    _copy_collections,
    prepare_snapshot,
    prepared_nodes,
    render_prepared_snapshot,
)
from computer_use.pipeline.core import Control
from computer_use.protocol.observation import NativeNode, NativeObservation
from computer_use.session.operations import OperationBudget, OperationDeadlineExceeded


class WorkMeter:
    """Advance a virtual clock only when the fixture's data is actually visited."""

    def __init__(self):
        self.reads = 0

    def visit(self):
        self.reads += 1

    def clock(self):
        return self.reads * 0.004

    def budget(self, reason):
        return OperationBudget(1000, clock=self.clock, cancelled=lambda: reason == "cancel" and self.reads >= 150)


class MeteredMapping(Mapping):
    def __init__(self, meter, size=2000):
        self.meter, self.size = meter, size

    def __len__(self):
        return self.size

    def __getitem__(self, key):
        return key

    def __iter__(self):
        return iter(range(self.size))

    def items(self):
        for key in self:
            self.meter.visit()
            yield key, key


class MeteredList(list):
    def __init__(self, meter, values):
        super().__init__(values)
        self.meter = meter

    def __iter__(self):
        for value in super().__iter__():
            self.meter.visit()
            yield value


def source_snapshot():
    native = NativeObservation(
        "macos", "session", "original", (1, 2), ("window",), {"window": NativeNode(role="window", name="Original")}
    )
    return normalize_facts(native, DisplayReferences("session"))


def expected_failure(reason):
    return asyncio.CancelledError if reason == "cancel" else OperationDeadlineExceeded


def assert_stopped_during_bounded_batch(meter, reason):
    threshold = 150 if reason == "cancel" else 250
    assert threshold <= meter.reads <= threshold + 128


@pytest.mark.parametrize("immutable", [False, True])
@pytest.mark.parametrize("reason", ["cancel", "timeout"])
def test_single_large_container_stops_during_scan_without_publishing_or_mutating_source(immutable, reason):
    meter = WorkMeter()
    source = MeteredMapping(meter)
    budget = meter.budget(reason)
    with pytest.raises(expected_failure(reason)):
        _copy_collections(source, immutable=immutable, checkpoint=budget.remaining)
    assert_stopped_during_bounded_batch(meter, reason)
    assert len(source) == 2000 and source[12] == 12
    assert budget.started == 0


@pytest.mark.parametrize("reason", ["cancel", "timeout"])
def test_prepare_pipeline_uses_original_budget_inside_capture_instead_of_display_fallback(reason):
    snapshot, meter = source_snapshot(), WorkMeter()
    snapshot.roots[0]["wide_fixture"] = MeteredMapping(meter)
    budget = meter.budget(reason)
    with pytest.raises(expected_failure(reason)):
        prepare_snapshot(snapshot, _control=Control(remaining=budget.remaining))
    assert_stopped_during_bounded_batch(meter, reason)
    assert snapshot.native.id == "original"
    assert snapshot.roots[0]["wide_fixture"][12] == 12


@pytest.mark.parametrize("reason", ["cancel", "timeout"])
def test_render_pipeline_checks_budget_while_materializing_its_captured_display(reason):
    prepared, meter = prepare_snapshot(source_snapshot()), WorkMeter()
    captured_root = dict(prepared.snapshot.roots[0], wide_fixture=MeteredMapping(meter))
    captured = replace(prepared.snapshot, roots=(captured_root,))
    budget = meter.budget(reason)
    with pytest.raises(expected_failure(reason)):
        render_prepared_snapshot(
            replace(prepared, request=replace(prepared.request, snapshot=captured)),
            _control=Control(remaining=budget.remaining),
        )
    assert_stopped_during_bounded_batch(meter, reason)
    assert captured.native.id == "original"


@pytest.mark.parametrize("reason", ["cancel", "timeout"])
def test_prepared_projection_materialization_checks_its_original_call_budget(reason):
    meter = WorkMeter()
    prepared = PreparedRoot("0", ("0",), {"wide": MeteredMapping(meter)}, source=object())
    budget = meter.budget(reason)
    with pytest.raises(expected_failure(reason)):
        prepared.materialize(checkpoint=budget.remaining)
    assert_stopped_during_bounded_batch(meter, reason)


@pytest.mark.parametrize("reason", ["cancel", "timeout"])
def test_comparison_node_index_stops_while_visiting_native_nodes(reason):
    meter = WorkMeter()
    nodes = MeteredList(meter, [{"node": str(index)} for index in range(2000)])
    captured = SimpleNamespace(facts={"nodes": nodes})
    budget = meter.budget(reason)
    with pytest.raises(expected_failure(reason)):
        prepared_nodes((), captured, checkpoint=budget.remaining)
    assert meter.reads == 256


@pytest.mark.parametrize("reason", ["cancel", "timeout"])
def test_one_wide_interface_group_checks_budget_within_source_members(reason):
    meter = WorkMeter()
    block = InterfaceBlock("group", "group", None, MeteredList(meter, map(str, range(2000))))
    document = SimpleNamespace(geometric_patterns=(), roots=("group",), blocks={"group": block})
    budget = meter.budget(reason)
    with pytest.raises(expected_failure(reason)):
        interface_layout_signatures(document, checkpoint=budget.remaining)
    assert_stopped_during_bounded_batch(meter, reason)


@pytest.mark.parametrize("reason", ["cancel", "timeout"])
def test_branch_membership_checks_budget_during_one_wide_child_scan(reason):
    meter = WorkMeter()
    children = MeteredList(meter, [{"ref": str(index), "children": []} for index in range(2000)])

    # reversed() uses indexed sequence access; instrument that path independently.
    class MeteredChildren(MeteredList):
        def __reversed__(self):
            for value in list.__reversed__(self):
                self.meter.visit()
                yield value

    root = {"ref": "root", "children": MeteredChildren(meter, children)}
    meter.reads = 0
    budget = meter.budget(reason)
    with pytest.raises(expected_failure(reason)):
        branch_references(root, {"root": "native-root"}, checkpoint=budget.remaining)
    assert_stopped_during_bounded_batch(meter, reason)
