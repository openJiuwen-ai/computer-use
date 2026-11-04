"""Incremental presentation does only the formatting needed by actual delivery."""

import gc
import weakref
from unittest.mock import patch

from computer_use.identity import Record
from computer_use.interaction.read.facts.normalize import normalize_facts
from computer_use.interaction.read.numbering.references import DisplayReferences
from computer_use.interaction.read.presentation.document import DisplayDocument
from computer_use.interaction.read.presentation.pipeline import RenderPreparedSnapshotPipeline
from computer_use.interaction.read.presentation.preparation import prepare_snapshot
from computer_use.interaction.read.presentation.rendering.document import unchanged
from computer_use.protocol.observation import NativeNode, NativeObservation, ReadIssue, Relation
from computer_use.session.observation import lazy_observation
from computer_use_mcp.output import ObservationPresenter


def observation(source, references, *, partial=False, related=False, issues=()):
    if partial:
        issues = (*issues, ReadIssue(("nodes", "window", "relations", "children"), "partial"))
    nodes = {
        "window": NativeNode(
            role="window",
            name="Form",
            relations={"children": Relation(kind="children", targets=("button",), total=2 if partial else 1)},
        ),
        "button": NativeNode(role="button", name="Unchanged control"),
    }
    if related:
        nodes["button"] = NativeNode(
            role="button",
            name="Unchanged control",
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
    with patch.object(unchanged, "unchanged_snapshot", return_value=None):
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
        assert unchanged.unchanged_snapshot(prepared) is not None
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
        assert unchanged.unchanged_snapshot(after["document"].parts[0].prepared) is None
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


def test_render_cache_does_not_keep_a_discarded_capture_alive():
    value = observation(Record(), DisplayReferences("render-work"))
    prepared = value["document"].parts[0].prepared
    retained = weakref.ref(prepared)
    assert "Unchanged control" in value["state"].text
    del prepared, value
    gc.collect()
    assert retained() is None


