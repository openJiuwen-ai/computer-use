# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Apply selected snapshot aliases, then present the final observation."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from computer_use.interaction.read.acquisition.model import AcquiredObservation
from computer_use.interaction.read.numbering.observation.model import (
    PURE,
    RESOURCE,
    RenderedObservation,
)
from computer_use.interaction.read.numbering.observation.passes.account_acquisition_gaps import (
    stage as account_acquisition_gaps,
)
from computer_use.interaction.read.numbering.observation.passes.annotate_busy_content import (
    stage as annotate_busy_content,
)
from computer_use.interaction.read.numbering.pipeline import NumberSnapshotsPipeline
from computer_use.interaction.read.presentation.model import SnapshotRenderRequest
from computer_use.interaction.read.presentation.pipeline import PrepareSnapshotPipeline
from computer_use.pipeline import Contract, Pipeline, Sequence
from computer_use.pipeline.core import Node

if TYPE_CHECKING:
    from computer_use.interaction.read.app import NativeAppReader
    from computer_use.pipeline.core import Control, Step


class NumberedPresentationPipeline(Pipeline["AcquiredObservation", "RenderedObservation"]):
    """Number accessibility snapshots and prepare the working-window display.

    Annotate collection gaps, assign shared display references to the window
    and menu snapshots, annotate busy elements, and freeze the numbered window
    projection. Return the presentation together with the updated acquisition data
    for final observation assembly.

    Attributes:
        services: Read services providing coverage analysis and snapshot numbering.
    """

    def __init__(self, services: NativeAppReader) -> None:
        self.services = services
        super().__init__(root=self.build())

    def build(self) -> Step[AcquiredObservation, RenderedObservation]:
        services = self.services

        return Sequence(
            name="NumberedPresentationPipeline",
            steps=[
                account_acquisition_gaps(services=services),
                _NumberObservedSnapshots(services),
                annotate_busy_content(),
                _PrepareSelectedSnapshot(),
            ],
        )


class _NumberObservedSnapshots(Node["AcquiredObservation", "AcquiredObservation"]):
    """Number the window and menu snapshots in one shared sequence.

    Collect the available window tree followed by the menu source trees and assign aliases
    through the application reader. Replace those component snapshots in the acquisition
    result.

    Attributes:
        services: Application reader that owns the shared display-reference allocator.
    """

    def __init__(self, services: NativeAppReader) -> None:
        self.services = services
        self.children = (NumberSnapshotsPipeline(),)
        super().__init__("NumberObservedSnapshots", Contract(AcquiredObservation, AcquiredObservation, RESOURCE))

    def _run(self, acquired: AcquiredObservation, control: Control) -> AcquiredObservation:
        components = acquired.components
        tree, menus = components.tree, components.menus
        parts = (tree,) if tree is not None else ()
        if menus is not None:
            parts += tuple(part.tree for part in menus.parts)
        numbered = iter(self.services.number_snapshots(parts, _control=control))
        if tree is not None:
            tree = next(numbered)
        if menus is not None:
            menus = replace(menus, parts=tuple(replace(part, tree=next(numbered)) for part in menus.parts))
        return replace(acquired, components=replace(components, tree=tree, menus=menus))

    async def _arun(self, acquired: AcquiredObservation, control: Control) -> AcquiredObservation:
        return self._run(acquired, control)


class _PrepareSelectedSnapshot(Node["AcquiredObservation", "RenderedObservation"]):
    """Prepare the numbered window tree without formatting text.

    Retain the selected compact projection and captured facts for final rendering.
    Return None for an acquisition without an accessibility tree.

    Attributes:
        preparation: Pure source-freezing and projection flow for the selected snapshot.
    """

    def __init__(self) -> None:
        self.preparation = PrepareSnapshotPipeline()
        self.children = (self.preparation,)
        super().__init__("PrepareSelectedSnapshot", Contract(AcquiredObservation, RenderedObservation, PURE))

    def _run(self, acquired: AcquiredObservation, control: Control) -> RenderedObservation:
        tree = acquired.components.tree
        rendered = (
            self.preparation.run(SnapshotRenderRequest(tree, compact=True), control) if tree is not None else None
        )
        return RenderedObservation(acquired, rendered)

    async def _arun(self, acquired: AcquiredObservation, control: Control) -> RenderedObservation:
        return self._run(acquired, control)
