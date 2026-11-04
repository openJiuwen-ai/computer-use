"""Changing AX coverage and presentation must not discard delivered knowledge."""

from dataclasses import replace

import pytest
from computer_use.identity import Record
from computer_use.interaction.read.delivery.coverage import document_coverage
from computer_use.interaction.read.delivery.diff.baseline import ComparisonDocument
from computer_use.interaction.read.facts.normalize import normalize_facts
from computer_use.interaction.read.numbering.references import DisplayReferences
from computer_use.interaction.read.presentation.document import DisplayDocument
from computer_use.interaction.read.presentation.pipeline import GeometricPresentationPipeline, InterfaceTextPipeline
from computer_use.interaction.read.presentation.preparation import prepare_snapshot
from computer_use.protocol.observation import (
    ControlValue,
    NativeNode,
    NativeObservation,
    ReadIssue,
    Relation,
    Structure,
)
from computer_use.session.observation import lazy_observation
from computer_use_mcp.output import ObservationOutput, ObservationPresenter


class Observations:
    def __init__(self):
        self.source = Record()
        self.refs = DisplayReferences("evolution")

    def read(self, value="Initial", *, related=None, owner="field", scope="window", omitted=False, partial=False):
        children = ("field",) if omitted else ("field", "other")
        nodes = {
            "window": NativeNode(
                role="window",
                name="Form",
                relations={
                    "children": Relation(kind="children", targets=children, total=2 if partial else len(children))
                },
            ),
            "field": NativeNode(role="text_field", name="Subject", control_value=ControlValue(value=value)),
        }
        if not omitted:
            nodes["other"] = NativeNode(role="button", name="Unchanged unrelated control")
        if related is not None:
            nodes[owner] = replace(nodes[owner], relations={"rows": Relation(kind="rows", targets=(related,), total=1)})
            nodes[related] = NativeNode(role="button", name="Related option")
        native = NativeObservation(
            backend="macos", session="evolution", id=value, interval_ns=(1, 2), roots=("window",), nodes=nodes
        )
        snapshot = normalize_facts(native, self.refs)
        prepared = prepare_snapshot(snapshot, public_api=True)
        document = DisplayDocument.tree(prepared).scoped((scope,))
        # Coverage describes the captured containment, including whether the
        # absent child was actually enumerated out of its parent.
        coverage = document_coverage(document)
        return {
            "source": self.source,
            "scope": scope,
            "view": "window",
            "state": lazy_observation(document, None),
            "document": document,
            "refs": dict(snapshot.refs),
            "full": False,
            "publicApi": True,
            "coverage": coverage,
        }


@pytest.fixture
def presenter():
    value = ObservationPresenter()
    yield value
    value.close()


def deliver(presenter, observation):
    text = presenter.text(observation)
    presenter.delivered(observation)
    return text


def test_new_related_roots_do_not_replace_the_known_window(presenter):
    observations = Observations()
    deliver(presenter, observations.read())
    after = observations.read("Changed", related="row")
    text = deliver(presenter, after)
    assert "Tree: diff" in text and "Changed" in text and "Related option" in text
    assert "Related elements (rows of" in text
    assert "Full tree" not in text and "Unchanged unrelated control" not in text
    assert "Tree: unchanged" in deliver(presenter, observations.read("Changed", related="row"))


def test_relation_owner_change_is_reported_even_when_node_values_do_not_change(presenter):
    observations = Observations()
    before = observations.read(related="row")
    deliver(presenter, before)
    after = observations.read(related="row", owner="other")
    text = deliver(presenter, after)
    owner = next(ref for ref, native in after["refs"].items() if native == "other")
    assert "Tree: diff" in text and f"Related elements (rows of {owner})" in text
    assert "Related option" in text and "Full tree" not in text


def test_complete_and_partial_absence_keep_their_distinct_meanings(presenter):
    observations = Observations()
    before = observations.read()
    missing = next(ref for ref, native in before["refs"].items() if native == "other")
    deliver(presenter, before)
    partial = deliver(presenter, observations.read(omitted=True, partial=True))
    assert f"Not observed in this read: {missing}" in partial
    assert "Removed element IDs" not in partial
    deliver(presenter, observations.read())
    removed = deliver(presenter, observations.read(omitted=True))
    assert f"Removed element IDs: {missing}" in removed


@pytest.mark.parametrize("max_bytes,max_scopes", [(0, 0), (200_000, 1), (1, 32)])
def test_memory_pressure_spills_instead_of_forgetting_a_delivered_scope(max_bytes, max_scopes):
    observations = Observations()
    presenter = ObservationPresenter(max_bytes=max_bytes, max_scopes=max_scopes)
    try:
        deliver(presenter, observations.read(scope="one"))
        deliver(presenter, observations.read(scope="two"))
        spill = presenter._baselines.directory
        assert spill is not None
        path = spill.name
        changed = deliver(presenter, observations.read("Changed", scope="one"))
        assert "Tree: diff" in changed and "Changed" in changed
        assert "Unchanged unrelated control" not in changed
        unchanged = deliver(presenter, observations.read("Changed", scope="one"))
        assert "Tree: unchanged" in unchanged
        presenter.invalidate(observations.source)
        assert "Tree: diff" not in deliver(presenter, observations.read("Again", scope="one"))
        presenter.close()
        from pathlib import Path

        assert not Path(path).exists()
    finally:
        presenter.close()


def test_local_preparation_fallback_keeps_changes_and_other_roots_incremental(presenter, monkeypatch):
    observations = Observations()
    deliver(presenter, observations.read(related="row"))
    original = GeometricPresentationPipeline.run

    def fail_one(self, value, control=None):
        if self.branch.root["name"] == "Related option":
            raise ValueError("injected local projection failure")
        return original(self, value, control)

    monkeypatch.setattr(GeometricPresentationPipeline, "run", fail_one)
    text = deliver(presenter, observations.read("Changed", related="row"))
    assert "Tree: diff" in text and "Changed" in text
    assert "Presentation fallback" in text and "Related option" in text
    assert "Unchanged unrelated control" not in text and "Full tree" not in text
    unchanged = deliver(presenter, observations.read("Changed", related="row"))
    assert "Tree: unchanged" in unchanged and "Related option" not in unchanged
    monkeypatch.undo()
    recovered = deliver(presenter, observations.read("Recovered", related="row"))
    assert "Tree: diff" in recovered and "Recovered" in recovered and "Related option" in recovered
    assert "Full tree" not in recovered and "Unchanged unrelated control" not in recovered


def test_terminal_failure_refreshes_only_its_root(presenter, monkeypatch):
    observations = Observations()
    deliver(presenter, observations.read(related="row"))
    original = InterfaceTextPipeline.run

    def fail_one(self, value, control=None):
        if len(value.interface.target_refs) == 1:
            raise ValueError("injected local terminal failure")
        return original(self, value, control)

    monkeypatch.setattr(InterfaceTextPipeline, "run", fail_one)
    text = deliver(presenter, observations.read("Changed", related="row"))
    assert "Tree: diff" in text and "Changed" in text and "Related option" in text
    assert "Presentation fallback" in text
    assert "Full tree" not in text and "Unchanged unrelated control" not in text


def test_failed_output_does_not_advance_baseline(presenter):
    observations = Observations()
    deliver(presenter, observations.read())
    output = ObservationOutput(presenter)
    output.observe(observations.read("Undelivered"))
    with pytest.raises(RuntimeError, match="rejected"):
        output.flush(lambda *_: (_ for _ in ()).throw(RuntimeError("rejected")))
    assert "Tree: unchanged" in presenter.text(observations.read())
    assert "Undelivered" in presenter.text(observations.read("Undelivered"))


def test_comparison_baseline_is_detached_and_bounded_by_elements_not_value_length():
    observations = Observations()
    before = observations.read("x" * 200_000)
    baseline = ComparisonDocument.capture(before["document"])
    assert baseline.estimated_bytes < 5_000
    assert ComparisonDocument.from_data(baseline.to_data()) == baseline


def test_table_packing_change_keeps_unchanged_cells_out_of_diff(presenter):
    """Losing a redundant AXRows query changes full-view packing, not cell facts."""
    source, refs = Record(), DisplayReferences("table")

    def relation(kind, targets):
        return Relation(kind=kind, targets=targets, total=len(targets), positions=tuple(range(len(targets))))

    def observe(partial):
        nodes = {
            "window": NativeNode(
                role="window", name="Table window", relations={"children": relation("children", ("table",))}
            ),
            "table": NativeNode(
                role="tree",
                name="Navigation",
                structure=Structure(row_count=2, column_count=1),
                relations={"children": relation("children", ("r1", "r2")), "rows": relation("rows", ("r1", "r2"))},
            ),
        }
        for index in range(1, 3):
            nodes[f"r{index}"] = NativeNode(
                role="row",
                name=f"Row {index}",
                structure=Structure(index=index - 1, level=1),
                relations={"children": relation("children", (f"c{index}",))},
            )
            nodes[f"c{index}"] = NativeNode(
                role="cell",
                name=f"Cell {index}",
                structure=Structure(row_index=index - 1, column_index=0, row_span=1, column_span=1),
                relations={"children": relation("children", ())},
            )
        issues = (ReadIssue(path=("nodes", "table", "relations", "rows"), state="timeout"),) if partial else ()
        native = NativeObservation(
            backend="macos",
            session="table",
            id=str(partial),
            interval_ns=(1, 2),
            roots=("window",),
            nodes=nodes,
            issues=issues,
        )
        snapshot = normalize_facts(native, refs)
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

    before, after = observe(False), observe(True)
    assert "Outline: Rows" in before["state"].text
    assert "Outline: Rows" not in after["state"].text
    deliver(presenter, before)
    text = deliver(presenter, after)
    assert "Tree: unchanged" in text
    assert "rows (timeout" in text
    assert "Cell 1" not in text and "Cell 2" not in text
    assert "Row 1" not in text and "Row 2" not in text
