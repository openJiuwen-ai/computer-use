# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Prepare scoped coverage declarations before whole-view text serialization."""

from __future__ import annotations

from computer_use.interaction.read.presentation.interface.coverage.model import CoveragePlan, CoverageRequest
from computer_use.interaction.read.presentation.interface.coverage.passes.share_child_coverage import (
    stage as share_child_coverage,
)
from computer_use.pipeline import Pipeline
from computer_use.pipeline.core import Step


class CoveragePipeline(Pipeline[CoverageRequest, CoveragePlan]):
    """Share precise repeated empty-page hints while preserving individual read routes."""

    def __init__(self) -> None:
        super().__init__(root=self.build())

    def build(self) -> Step[CoverageRequest, CoveragePlan]:
        return share_child_coverage()
