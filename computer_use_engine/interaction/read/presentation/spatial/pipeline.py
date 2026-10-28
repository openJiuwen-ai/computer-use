# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Organize a compressed display using immutable local spatial evidence."""

from __future__ import annotations

from computer_use.interaction.read.presentation.compression.model import CompressionRun
from computer_use.interaction.read.presentation.spatial.passes.associate_labels import stage as associate_labels
from computer_use.interaction.read.presentation.spatial.passes.group_rows import stage as group_rows
from computer_use.interaction.read.presentation.spatial.passes.describe_regions import stage as describe_regions
from computer_use.interaction.read.presentation.spatial.passes.mark_visual_chains import stage as mark_visual_chains
from computer_use.pipeline import Pipeline, Sequence
from computer_use.pipeline.core import Step


class SpatialOrganizationPipeline(Pipeline[CompressionRun, CompressionRun]):
    """Associate visual labels, group repeated rows, and distinguish parallel regions.

    Operate on the local presentation after native semantic compression. Every
    stage retains original element identities, independent facts and actions.
    """

    def __init__(self) -> None:
        super().__init__(root=self.build())

    def build(self) -> Step[CompressionRun, CompressionRun]:
        return Sequence(
            name="SpatialOrganizationPipeline",
            steps=[associate_labels(), group_rows(), describe_regions(), mark_visual_chains()],
        )
