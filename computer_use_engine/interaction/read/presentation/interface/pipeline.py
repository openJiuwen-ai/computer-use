# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Build a source-preserving visual interface plan before final text generation."""

from __future__ import annotations

from computer_use.interaction.read.presentation.interface.evidence.pipeline import InterfaceEvidencePipeline
from computer_use.interaction.read.presentation.interface.regions.pipeline import InterfaceRegionPipeline
from computer_use.interaction.read.presentation.interface.composition.pipeline import InterfaceCompositionPipeline
from computer_use.interaction.read.presentation.interface.structure.pipeline import InterfaceStructurePipeline
from computer_use.interaction.read.presentation.interface.validation.pipeline import InterfaceValidationPipeline
from computer_use.interaction.read.presentation.interface.model import InterfaceDocument, InterfaceRequest
from computer_use.interaction.read.presentation.ir import ProjectionResult
from computer_use.pipeline import Pipeline, Sequence
from computer_use.pipeline.core import Step


class InterfacePresentationPipeline(Pipeline[InterfaceRequest, InterfaceDocument]):
    """Organize projected sources as regions, controls and records with fixed real targets.

    Retain immutable fields and provenance while introducing separate layout block keys.
    Every projected object keeps a unique location; no native calls or text formatting
    occur in this pipeline.
    """

    def __init__(self) -> None:
        super().__init__(root=self.build())

    def build(self) -> Step[InterfaceRequest, InterfaceDocument]:
        return Sequence(
            name="InterfacePresentationPipeline",
            steps=[
                InterfaceEvidencePipeline(),
                InterfaceRegionPipeline(),
                InterfaceCompositionPipeline(),
                InterfaceStructurePipeline(),
                InterfaceValidationPipeline(),
            ],
        )


def build_interface(projection: ProjectionResult, *, enabled: bool = True) -> InterfaceDocument:
    """Build an immutable interface from one already-compressed source projection.

    Args:
        projection: Structured roots with original source fields and composition evidence.
        enabled: Enable compact organization; false retains the projected hierarchy.

    Returns:
        A text-free layout and exact original-reference inventory.
    """
    return InterfacePresentationPipeline().run(InterfaceRequest(projection, enabled))
