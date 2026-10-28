# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Own projection candidates, final geometric layout and source-accounted content plans."""

from __future__ import annotations

from computer_use.interaction.read.presentation.ir import ProjectionInput, ProjectionResult
from computer_use.interaction.read.presentation.model import BranchFacts, PURE
from computer_use.interaction.read.presentation.preparation import CapturedSnapshot, PreparedRoot
from computer_use.interaction.read.presentation.compression.pipeline import CompressionPipeline
from computer_use.pipeline import Contract, Pipeline, Sequence
from computer_use.pipeline.core import Control, Node, Step
from computer_use.interaction.read.presentation.interface.geometric.pipeline import GeometricLayoutPipeline
from computer_use.interaction.read.presentation.interface.model import InterfaceRequest
from computer_use.interaction.read.presentation.interface.evidence.relations import capture_relations
from computer_use.interaction.read.presentation.interface.disclosure.pipeline import DisclosurePipeline
from computer_use.interaction.read.presentation.interface.disclosure.groups import (
    GroupDisclosurePipeline,
    GroupDisclosureRequest,
)
from computer_use.interaction.read.presentation.interface.disclosure.capture import capture_disclosure
from computer_use.interaction.read.presentation.interface.atoms.pipeline import AtomPlanningPipeline
from computer_use.interaction.read.presentation.interface.atoms.model import AtomRequest
from computer_use.interaction.read.presentation.interface.atoms.model import AtomFinalizeRequest
from computer_use.interaction.read.presentation.interface.atoms.finalization.pipeline import FinalizeAtomContentPipeline
from computer_use.interaction.read.presentation.interface.collections.model import CollectionRequest
from computer_use.interaction.read.presentation.interface.collections.pipeline import CollectionPipeline
from computer_use.interaction.read.presentation.interface.composition.content.model import FieldContentRequest
from computer_use.interaction.read.presentation.interface.composition.content.pipeline import FieldContentPipeline
from computer_use.interaction.read.presentation.interface.coverage import CoveragePipeline, CoverageRequest
from computer_use.interaction.read.presentation.interface.sharing import (
    SharingPipeline,
    SharingRequest,
    capture_sharing_evidence,
)
from computer_use.interaction.read.presentation.interface.sharing.text_slices import TextSlicePipeline, TextSliceRequest
from computer_use.interaction.read.presentation.interface.readable.model import ReadableRequest
from computer_use.interaction.read.presentation.interface.readable.pipeline import ReadablePresentationPipeline


class _ArrangePresentation(Node[ProjectionResult, PreparedRoot]):
    """Assemble the final block and content plans from one source-preserved projection."""

    def __init__(self, branch: BranchFacts, captured: CapturedSnapshot, recovery_prefix: int) -> None:
        super().__init__("ArrangeGeometricPresentation", Contract(ProjectionResult, PreparedRoot, PURE))
        self.branch, self.captured, self.recovery_prefix = branch, captured, recovery_prefix

    def _run(self, projection: ProjectionResult, control: Control) -> PreparedRoot:
        branch, captured = self.branch, self.captured
        request, snapshot = branch.request, branch.request.snapshot
        recovery_prefix = self.recovery_prefix
        atoms = AtomPlanningPipeline().run(AtomRequest(projection, branch), control)
        interface = GeometricLayoutPipeline().run(
            InterfaceRequest(projection, relations=capture_relations(snapshot), atoms=atoms, captured=captured), control
        )
        disclosure = DisclosurePipeline().run(capture_disclosure(snapshot, interface, enabled=True), control)
        atoms = FinalizeAtomContentPipeline().run(
            AtomFinalizeRequest(atoms, projection, branch, disclosure, interface=interface), control
        )
        collections = CollectionPipeline().run(
            CollectionRequest(interface, atoms, snapshot.native, snapshot.refs), control
        )
        fields = FieldContentPipeline().run(FieldContentRequest(interface, atoms, captured_details=True), control)
        groups = GroupDisclosurePipeline().run(
            GroupDisclosureRequest(
                interface,
                atoms,
                captured,
                enabled=True,
                field_disclosure=disclosure,
                window_context=request.window_context,
                recovery_prefix_units=recovery_prefix,
            ),
            control,
        )

        composed = frozenset(
            ref
            for plan in fields.fields.values()
            for ref in (plan.target_ref, *(member.ref for member in plan.members))
        )
        excluded = composed | frozenset(ref for group in groups.groups for ref in group.recovery_refs)
        sharing = SharingPipeline().run(
            SharingRequest(
                interface,
                atoms,
                capture_sharing_evidence(branch, projection, atoms),
                disclosure,
                excluded_sources=excluded,
            ),
            control,
        )
        coverage = CoveragePipeline().run(CoverageRequest(interface, atoms), control)
        text_slices = TextSlicePipeline().run(
            TextSliceRequest(
                atoms,
                snapshot.native,
                snapshot.refs,
                snapshot.value_fields,
                disclosure,
                excluded_sources=excluded,
                excluded_slots=frozenset((ref, key) for ref, keys in sharing.omitted_slots.items() for key in keys),
            ),
            control,
        )
        readable = ReadablePresentationPipeline().run(ReadableRequest(interface, atoms, sharing), control)
        return PreparedRoot.capture(
            branch.root["ref"],
            branch.members,
            projection,
            interface=interface,
            disclosure=disclosure,
            atoms=atoms,
            collections=collections,
            fields=fields,
            sharing=sharing,
            coverage=coverage,
            text_slices=text_slices,
            groups=groups,
            readable=readable,
        )

    async def _arun(self, projection: ProjectionResult, control: Control) -> PreparedRoot:
        return self._run(projection, control)


class GeometricPresentationPipeline(Pipeline[ProjectionInput, PreparedRoot]):
    """Prepare and freeze one complete final display from immutable semantic facts.

    Reuse source-preserving compression as unit/candidate preparation inside this
    flow. GeometricLayoutPipeline chooses the reading blocks; content policies then
    account for their exact slots, shared declarations and detail recovery routes.
    The result is structured data for numbering and full/diff delivery. Serialization
    occurs only at the final text boundary.
    """

    def __init__(self, branch: BranchFacts, captured: CapturedSnapshot, recovery_prefix: int = 0) -> None:
        self.branch, self.captured, self.recovery_prefix = branch, captured, recovery_prefix
        super().__init__(root=self.build())

    def build(self) -> Step[ProjectionInput, PreparedRoot]:
        return Sequence(
            name="GeometricPresentationPipeline",
            steps=[CompressionPipeline(), _ArrangePresentation(self.branch, self.captured, self.recovery_prefix)],
        )
