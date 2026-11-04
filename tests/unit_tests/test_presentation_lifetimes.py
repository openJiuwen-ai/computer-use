"""Finished presentation work releases its private graphs without cyclic GC."""

import asyncio
import gc
import weakref
from unittest.mock import patch

import pytest
from computer_use.interaction.read.facts.normalize import normalize_facts
from computer_use.interaction.read.numbering.references import DisplayReferences
from computer_use.interaction.read.presentation.compression.pipeline import CompressionPipeline, CompressionTraversal
from computer_use.interaction.read.presentation.document import DisplayDocument
from computer_use.interaction.read.presentation.ir import PresentationIR, ProjectionInput, ProjectionView, view_node
from computer_use.interaction.read.presentation.preparation import prepare_snapshot
from computer_use.protocol.observation import NativeNode, NativeObservation, Relation


def snapshot():
    native = NativeObservation(
        "macos",
        "lifetime",
        "capture",
        (1, 2),
        ("window",),
        {
            "window": NativeNode(
                role="window", name="Lifetime", relations={"children": Relation("children", ("button",))}
            ),
            "button": NativeNode(role="button", name="Press"),
        },
    )
    return normalize_facts(native, DisplayReferences("lifetime"))


def source():
    return ProjectionInput.build(snapshot().roots[0], {"nativeFacts": True, "recordFields": True})


def test_completed_compression_releases_ir_while_preserving_cached_views_during_work():
    gc.collect()
    references, caches, owned = [], [], []
    original_build = PresentationIR.build.__func__
    original_traversal = CompressionTraversal._run

    def record(cls, captured):
        ir = original_build(cls, captured)
        references.append(weakref.ref(ir))
        owned.append(ir)
        return ir

    def traverse(owner, run, control):
        assert run.ir.views
        caches.append(all(view_node(run.ir, ref) is node for ref, node in run.view.nodes.items()))
        return original_traversal(owner, run, control)

    with (
        patch.object(PresentationIR, "build", classmethod(record)),
        patch.object(CompressionTraversal, "_run", traverse),
    ):
        projected = CompressionPipeline().run(source())
    assert caches == [True]
    assert projected["roots"][0]["name"] == "Lifetime"
    assert all(not ir.views for ir in owned)
    owned.clear()
    assert references and all(reference() is None for reference in references)
    assert gc.isenabled()


@pytest.mark.parametrize("error", [RuntimeError("rule failed"), asyncio.CancelledError()])
def test_compression_failure_and_cancellation_close_the_private_view_cache(error):
    held = []

    def fail(owner, run, control):
        held.append(run.ir)
        assert run.ir.views
        raise error

    with patch.object(CompressionTraversal, "_run", fail), pytest.raises(type(error)):
        CompressionPipeline().run(source())
    assert len(held) == 1
    assert held[0].views == {}
    # Explicitly retained view owners remain readable. Cleanup only releases
    # the completed run's cache, rather than invalidating captured source facts.
    assert ProjectionView(held[0]).view["name"] == "Lifetime"
    held[0].views.clear()


def test_failure_during_ir_admission_clears_views_before_lifetime_scope_exists():
    held = []
    original = ProjectionView.__init__

    def fail(view, ir):
        original(view, ir)
        held.append(ir)
        assert ir.views
        raise ValueError("failed admission")

    with patch.object(ProjectionView, "__init__", fail), pytest.raises(ValueError, match="failed admission"):
        CompressionPipeline().run(source())
    assert held and held[0].views == {}


@pytest.mark.parametrize("compact", [True, False])
def test_finished_full_and_compact_render_release_captures_before_another_gc(compact):
    gc.collect()

    def render():
        prepared = prepare_snapshot(snapshot(), compact=compact, public_api=compact)
        document = DisplayDocument.tree(prepared)
        text = document.text
        references = [weakref.ref(prepared), weakref.ref(prepared.snapshot)]
        references.extend(weakref.ref(root.source) for root in prepared.roots if root.source is not None)
        return text, references

    text, references = render()
    assert "Lifetime" in text and "Press" in text
    assert all(reference() is None for reference in references), [
        type(reference()).__name__ for reference in references if reference() is not None
    ]
    assert gc.isenabled()
