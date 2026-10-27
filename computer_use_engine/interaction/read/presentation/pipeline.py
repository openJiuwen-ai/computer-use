"""Prepare immutable projections, then render them at the final output boundary."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.accounting.pipeline import AccountingPipeline
from computer_use.interaction.read.presentation.accounting.relations import RelationPresentation
from computer_use.interaction.read.presentation.compression.pipeline import CompressionPipeline
from computer_use.interaction.read.presentation.fallback import UncompressedFacts, branch_references
from computer_use.interaction.read.presentation.model import (
    PRESENTATION_FAILURES,
    PURE,
    BranchFacts,
    RenderedPart,
    SnapshotParts,
    SnapshotRenderRequest,
)
from computer_use.interaction.read.presentation.passes.assemble_rendered_part import (
    stage as assemble_rendered_part,
)
from computer_use.interaction.read.presentation.preparation import (
    CapturedSnapshot,
    PreparationFailure,
    PreparedRoot,
    PreparedSnapshot,
    prepared_nodes,
)
from computer_use.interaction.read.presentation.preparation import (
    prepare_snapshot as prepare_snapshot,
)
from computer_use.interaction.read.presentation.preparation import (
    render_prepared_snapshot as render_prepared_snapshot,
)
from computer_use.interaction.read.presentation.rendering.pipeline import RenderPipeline
from computer_use.interaction.read.presentation.semantics.pipeline import SemanticPipeline
from computer_use.pipeline import Contract, Pipeline, Sequence
from computer_use.pipeline.core import Node

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.ir import ProjectionResult
    from computer_use.interaction.read.presentation.records import RenderedTree, SemanticNode, SnapshotFacts
    from computer_use.pipeline.core import Control, Step


class PresentationPipeline(Pipeline["SemanticNode", "ProjectionResult"]):
    """Prepare one source-accounted root for later display.

    Compose control semantics and compress the local projection. Return its structured
    forest together with immutable source facts and provenance.

    Attributes:
        branch: Root-specific facts, native indexes, and presentation options shared by the
            stages.
    """

    def __init__(self, branch: BranchFacts) -> None:
        self.branch = branch
        super().__init__(root=self.build())

    def build(self) -> Step[SemanticNode, ProjectionResult]:
        branch = self.branch
        return Sequence(
            name="PresentationPipeline",
            steps=[
                SemanticPipeline(branch),
                CompressionPipeline(),
            ],
        )


def _branch_facts(snapshot: SnapshotFacts, request: SnapshotRenderRequest, root: SemanticNode) -> BranchFacts:
    """Build the exact source indexes shared by preparation and final accounting."""
    native_nodes = {node["node"]: node for node in snapshot.facts["nodes"]}
    relations = RelationPresentation(snapshot.facts, snapshot.refs)
    members = branch_references(root, snapshot.refs)
    readable_text = set()
    for ref in members:
        key = snapshot.value_fields.get(ref)
        if key is None:
            continue
        source = native_nodes[snapshot.refs[ref]]
        if not isinstance(source["values"].get(key), str):
            continue
        if key not in source["fieldStatus"] or "textRef" in source["fieldStatus"][key]:
            readable_text.add(ref)
    return BranchFacts(request, root, members, native_nodes, relations, readable_text)


class _BranchCollection(Node["SnapshotRenderRequest", "PreparedSnapshot"]):
    """Prepare each captured root without generating presentation text.

    Build root-local source indexes and run the presentation flow for each root. When a
    projection contract fails, retain its source facts and failure for final rendering.
    """

    def __init__(self) -> None:
        super().__init__("PrepareSnapshotRoots", Contract(SnapshotRenderRequest, PreparedSnapshot, PURE))

    def _run(self, request: SnapshotRenderRequest, control: Control) -> PreparedSnapshot:
        from dataclasses import replace

        captured = CapturedSnapshot.capture(request.snapshot)
        snapshot = captured.materialize()
        local_request = replace(request, snapshot=snapshot)
        roots = []
        for root in snapshot.roots:
            # Identity and source validation deliberately remain outside the
            # pure display fallback; they must never publish guessed bindings.
            branch = _branch_facts(snapshot, local_request, root)
            try:
                projection = PresentationPipeline(branch).run(root, control)
                prepared = PreparedRoot.capture(root["ref"], branch.members, projection)
            except PRESENTATION_FAILURES as error:
                prepared = PreparedRoot(root["ref"], branch.members, None, failure=PreparationFailure.capture(error))
            roots.append(prepared)
        prepared_roots = tuple(roots)
        return PreparedSnapshot(
            replace(request, snapshot=captured), prepared_roots, prepared_nodes(prepared_roots, captured)
        )

    async def _arun(self, request: SnapshotRenderRequest, control: Control) -> PreparedSnapshot:
        return self._run(request, control)


class PrepareSnapshotPipeline(Pipeline["SnapshotRenderRequest", "PreparedSnapshot"]):
    """Freeze an observation and prepare its independent compressed display roots."""

    def __init__(self) -> None:
        super().__init__(root=self.build())

    def build(self) -> Step[SnapshotRenderRequest, PreparedSnapshot]:
        return _BranchCollection()


class _RenderPreparedRoots(Node["PreparedSnapshot", "SnapshotParts"]):
    """Render retained projections and account for every captured source fact.

    Use local copies of the frozen preparation. A final formatting failure retains the
    existing uncompressed fallback for that independent root.
    """

    def __init__(self) -> None:
        super().__init__("RenderPreparedRoots", Contract(PreparedSnapshot, SnapshotParts, PURE))

    def _run(self, prepared: PreparedSnapshot, control: Control) -> SnapshotParts:
        from dataclasses import replace

        snapshot = prepared.snapshot.materialize()
        request = replace(prepared.request, snapshot=snapshot)
        parts, uncompressed = [], None
        for root, projection in zip(snapshot.roots, prepared.roots):
            branch = _branch_facts(snapshot, request, root)
            try:
                if projection.failure is not None:
                    raise projection.failure.exception()
                renderer = Sequence(
                    name="RenderPreparedRoot", steps=[RenderPipeline(branch), AccountingPipeline(branch)]
                )
                part = renderer.run(projection.materialize(), control)
            except PRESENTATION_FAILURES as error:
                if uncompressed is None:
                    uncompressed = UncompressedFacts(snapshot.facts, snapshot.refs)
                rendered = uncompressed.render(branch.members, error)
                _, sources = branch.relations.render(branch.members, rendered)
                part = RenderedPart(rendered, {}, {}, sources, uncompressed=True)
            parts.append(part)
        return SnapshotParts(request, tuple(parts))

    async def _arun(self, prepared: PreparedSnapshot, control: Control) -> SnapshotParts:
        return self._run(prepared, control)


class RenderPreparedSnapshotPipeline(Pipeline["PreparedSnapshot", "RenderedTree"]):
    """Generate final snapshot text from a previously prepared structured observation."""

    def __init__(self) -> None:
        super().__init__(root=self.build())

    def build(self) -> Step[PreparedSnapshot, RenderedTree]:
        return Sequence(name="RenderPreparedSnapshotPipeline", steps=[_RenderPreparedRoots(), assemble_rendered_part()])


class SnapshotPresentationPipeline(Pipeline["SnapshotRenderRequest", "RenderedTree"]):
    """Explicitly prepare and render a complete snapshot.

    Present each root with its relationship context, then merge the rendered text and
    supplementary facts. Return full detail text, the requested compact text, and
    reference/accounting metadata.
    """

    def __init__(self) -> None:
        super().__init__(root=self.build())

    def build(self) -> Step[SnapshotRenderRequest, RenderedTree]:
        return Sequence(
            name="SnapshotPresentationPipeline", steps=[PrepareSnapshotPipeline(), RenderPreparedSnapshotPipeline()]
        )
