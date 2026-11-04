"""Incremental presentation does only the formatting needed by actual delivery."""

import gc
import weakref
from collections import deque
from unittest.mock import patch

from computer_use.identity import Record
from computer_use.interaction.read.facts.normalize import normalize_facts
from computer_use.interaction.read.numbering.references import DisplayReferences
from computer_use.interaction.read.presentation.document import DisplayDocument
from computer_use.interaction.read.presentation.pipeline import RenderPreparedSnapshotPipeline
from computer_use.interaction.read.presentation.preparation import prepare_snapshot
from computer_use.interaction.read.presentation.rendering.document import incremental
from computer_use.protocol.observation import NativeNode, NativeObservation, ReadIssue, Relation
from computer_use.session.observation import lazy_observation, peek_observation_text
from computer_use_mcp.output import ObservationPresenter
from computer_use_mcp.runtime import Runtime


def observation(source, references, *, partial=False, related=False, issues=(), label="Unchanged control"):
    if partial:
        issues = (*issues, ReadIssue(("nodes", "window", "relations", "children"), "partial"))
    nodes = {
        "window": NativeNode(
            role="window",
            name="Form",
            relations={"children": Relation(kind="children", targets=("button",), total=2 if partial else 1)},
        ),
        "button": NativeNode(role="button", name=label),
    }
    if related:
        nodes["button"] = NativeNode(
            role="button",
            name=label,
            relations={"rows": Relation(kind="rows", targets=("option",), total=1)},
        )
        nodes["option"] = NativeNode(role="button", name="Related option")
    facts = NativeObservation("macos", "render-work", "captured", (1, 2), ("window",), nodes, issues=issues)
    snapshot = normalize_facts(facts, references)
    prepared = prepare_snapshot(snapshot, compact=True, public_api=True)
    document = DisplayDocument.tree(prepared).scoped(("window",))
    return {
        "source": source,
        "scope": "window",
        "view": "window",
        "state": lazy_observation(document),
        "document": document,
        "refs": dict(snapshot.refs),
        "full": False,
        "publicApi": True,
    }


def full_render_diff(presenter, value):
    """Use the existing complete renderer before applying the same delta selection."""
    with patch.object(incremental, "incremental_snapshot", return_value=None):
        return presenter.text(value)


def test_unchanged_tree_skips_body_but_retains_current_read_notes_and_related_context():
    source, references = Record(), DisplayReferences("render-work")
    presenter = ObservationPresenter()
    try:
        before = observation(source, references, related=True)
        presenter.delivered(before)
        after = observation(
            source,
            references,
            related=True,
            issues=(ReadIssue(("roots",), "timeout", detail="capture-local root-query timeout"),),
        )
        prepared = after["document"].parts[0].prepared
        assert incremental.incremental_snapshot(prepared, frozenset()) is not None
        expected = full_render_diff(presenter, after)
        with patch.object(RenderPreparedSnapshotPipeline, "run", side_effect=AssertionError("full body rendered")):
            text = presenter.text(after)
        assert text == expected
        assert "Tree: unchanged" in text
        assert "Related elements (rows of" in text
        assert "timeout" in text.lower()
        assert "Unchanged control" not in text and "Related option" not in text
        # The public SDK observation still provides its complete captured view.
        assert "Unchanged control" in after["state"].text
        assert "Related option" in after["state"].text
    finally:
        presenter.close()


def test_partial_tree_uses_regular_renderer_and_keeps_current_continuation_hints():
    source, references = Record(), DisplayReferences("render-work")
    presenter = ObservationPresenter()
    try:
        before = observation(source, references, partial=True)
        presenter.delivered(before)
        after = observation(source, references, partial=True)
        assert incremental.incremental_snapshot(after["document"].parts[0].prepared, frozenset()) is None
        expected = full_render_diff(presenter, after)
        assert presenter.text(after) == expected
        assert "Children:" in expected and "partial" in expected
        assert "Unchanged control" not in expected
    finally:
        presenter.close()


def test_full_body_is_rendered_once_per_exact_prepared_capture():
    source, references = Record(), DisplayReferences("render-work")
    first = observation(source, references)
    state = first["state"]
    original = RenderPreparedSnapshotPipeline.run
    rendered = []

    def run(self, value, control=None):
        rendered.append(value)
        return original(self, value, control)

    with patch.object(RenderPreparedSnapshotPipeline, "run", run):
        assert state.text == first["document"].text
        assert "Unchanged control" in first["document"].text
        another = observation(source, references)["state"]
        assert "Unchanged control" in another.text
    assert len(rendered) == 2
    assert rendered[0] is not rendered[1], "Different native captures must not share source authority"


def test_initial_changed_unchanged_explicit_full_and_next_change_keep_one_truthful_baseline():
    source, references = Record(), DisplayReferences("render-work")
    presenter = ObservationPresenter()
    try:
        initial = observation(source, references, related=True)
        assert "Unchanged control" in presenter.text(initial)
        presenter.delivered(initial)
        # Obtain reference wording from an equivalent separate immutable capture.
        # The actual changed source has never entered the complete-render cache.
        expected = full_render_diff(presenter, observation(source, references, related=True, label="Changed"))
        changed = observation(source, references, related=True, label="Changed")
        with patch.object(RenderPreparedSnapshotPipeline, "run", side_effect=AssertionError("full body rendered")):
            actual = presenter.text(changed)
        assert actual == expected
        assert "Tree: diff" in actual and "Changed" in actual
        assert "Related option" not in actual
        presenter.delivered(changed)

        same = observation(source, references, related=True, label="Changed")
        with patch.object(RenderPreparedSnapshotPipeline, "run", side_effect=AssertionError("full body rendered")):
            assert "Tree: unchanged" in presenter.text(same)
        presenter.delivered(same)

        # Reuse the very same capture after its partial model delivery. It must
        # still render the complete view and cannot find partial text in a cache.
        complete = dict(changed, full=True)
        text = presenter.text(complete)
        assert "Related option" in text and "Changed" in text and "Tree: diff" not in text
        assert changed["state"].text == text
        presenter.delivered(complete)
        following = observation(source, references, related=True, label="Next change")
        assert "Next change" in presenter.text(following)
        assert "Related option" not in presenter.text(following)
    finally:
        presenter.close()


def test_local_selected_format_failure_uses_the_same_capture_without_losing_diff(monkeypatch):
    from computer_use.interaction.read.presentation.interface.text.formatting import InterfaceWriter

    source, references = Record(), DisplayReferences("render-work")
    presenter = ObservationPresenter()
    try:
        presenter.delivered(observation(source, references))
        expected = full_render_diff(presenter, observation(source, references, label="Changed"))
        changed = observation(source, references, label="Changed")
        original = InterfaceWriter.remember

        def injected(self, *args, **kwargs):
            if self.selected_refs is not None:
                raise ValueError("injected selected formatting failure")
            return original(self, *args, **kwargs)

        monkeypatch.setattr(InterfaceWriter, "remember", injected)
        assert presenter.text(changed) == expected
        assert "Changed" in changed["state"].text
    finally:
        presenter.close()


def test_render_cache_does_not_keep_a_discarded_capture_alive():
    value = observation(Record(), DisplayReferences("render-work"))
    prepared = value["document"].parts[0].prepared
    retained = weakref.ref(prepared)
    assert "Unchanged control" in value["state"].text
    del prepared, value
    gc.collect()
    assert retained() is None


def test_observation_diagnostics_do_not_materialize_undelivered_full_text():
    value = observation(Record(), DisplayReferences("render-work"))
    events = []
    runtime = object.__new__(Runtime)
    runtime.send = events.append
    run = {"id": "fixture", "observationLogs": deque((value,))}
    assert peek_observation_text(value["state"]) is None
    with patch.object(DisplayDocument, "text", property(lambda _: (_ for _ in ()).throw(AssertionError("rendered")))):
        runtime.log_observations(run)
    assert peek_observation_text(value["state"]) is None
    assert len(events) == 1
    diagnostic = events[0]["diagnostic"]
    assert diagnostic["textMaterialized"] is False
    assert diagnostic["presentation"] == value["document"].summary_data()
    assert diagnostic["elementRefs"] == list(value["refs"])
    assert "text" not in diagnostic


def test_diagnostics_keep_original_text_when_the_sdk_caller_requested_it():
    value = observation(Record(), DisplayReferences("render-work"))
    complete = value["state"].text
    events = []
    runtime = object.__new__(Runtime)
    runtime.send = events.append
    run = {"id": "fixture", "observationLogs": deque((value,))}
    runtime.log_observations(run, materializedOnly=True)
    assert events[0]["diagnostic"]["text"] == complete
    assert not run["observationLogs"]


def test_capture_drafts_rebase_against_actual_delivery_after_their_original_budget_expired():
    from computer_use.interaction.read.delivery.diff.baseline import ComparisonNode
    from computer_use.session.operations import OperationBudget

    source, references = Record(), DisplayReferences("render-work")
    presenter = ObservationPresenter()
    now = [0.0]
    budget = OperationBudget(1000, clock=lambda: now[0])
    try:
        zero = observation(source, references, related=True, label="Zero")
        presenter.text(zero)
        presenter.delivered(zero)
        first = presenter.prepare(observation(source, references, related=True, label="One"), budget)
        second = presenter.prepare(observation(source, references, related=True, label="Zero"), budget)
        assert first["operationBudget"] is budget and second["operationBudget"] is budget
        # Preparing a capture cannot claim that its text has been delivered.
        assert "Tree: unchanged" in presenter.text(zero)
        now[0] = 10.0
        assert budget.expired
        with (
            patch.object(RenderPreparedSnapshotPipeline, "run", side_effect=AssertionError("late full renderer")),
            patch.object(ComparisonNode, "capture", side_effect=AssertionError("late signature work")),
            patch.object(incremental, "incremental_snapshot", side_effect=AssertionError("late selective renderer")),
        ):
            first_text = presenter.text(first)
            presenter.delivered(first)
            second_text = presenter.text(second)
            presenter.delivered(second)
            explicit_full = presenter.text(dict(second, full=True))
        assert "One" in first_text and "Tree: diff" in first_text
        # If this were cached relative to the old Zero baseline, it would
        # incorrectly say unchanged after One had actually been delivered.
        assert "Zero" in second_text and "Tree: diff" in second_text
        assert "Related option" in explicit_full and "Zero" in explicit_full
    finally:
        presenter.close()


def test_coalescing_prepared_captures_uses_the_last_real_baseline():
    from computer_use_mcp.output import ObservationOutput

    source, references = Record(), DisplayReferences("render-work")
    presenter = ObservationPresenter()
    try:
        zero = observation(source, references, label="Zero")
        presenter.delivered(zero)
        pending = ObservationOutput(presenter)
        pending.observe(presenter.prepare(observation(source, references, label="One")))
        pending.observe(presenter.prepare(observation(source, references, label="Zero")))
        retained = pending.take()
        assert len(retained) == 1
        # Taking final slots is not a delivery acknowledgement. Render them
        # against the actual baseline, then commit only the displayed result.
        with patch.object(RenderPreparedSnapshotPipeline, "run", side_effect=AssertionError("late renderer")):
            output = presenter.text(retained[0])
            presenter.delivered(retained[0])
        assert "Tree: unchanged" in output
        assert "One" not in output
    finally:
        presenter.close()
