# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Organize retained semantic scopes into readable window-local sections."""

from __future__ import annotations

from computer_use.interaction.read.presentation.interface.regions.passes.seed_regions import stage as seed_regions
from computer_use.interaction.read.presentation.interface.regions.passes.group_heading_sections import (
    stage as group_heading_sections,
)
from computer_use.interaction.read.presentation.interface.regions.passes.partition_local_layout import (
    stage as partition_local_layout,
)
from computer_use.interaction.read.presentation.interface.regions.passes.order_regions import stage as order_regions
from computer_use.interaction.read.presentation.interface.model import InterfaceDraft
from computer_use.pipeline import Pipeline, Sequence
from computer_use.pipeline.core import Step


class InterfaceRegionPipeline(Pipeline[InterfaceDraft, InterfaceDraft]):
    """Expose native regions and local columns under stable semantic reading constraints."""

    def __init__(self) -> None:
        super().__init__(root=self.build())

    def build(self) -> Step[InterfaceDraft, InterfaceDraft]:
        return Sequence(
            name="InterfaceRegionPipeline",
            steps=[seed_regions(), group_heading_sections(), partition_local_layout(), order_regions()],
        )
