# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Choose reversible field excerpts before the terminal interface formatter."""

from __future__ import annotations

from computer_use.pipeline import Pipeline, Sequence
from computer_use.pipeline.core import Step
from .model import DisclosurePlan, DisclosureRequest
from .passes.classify_disclosure import stage as classify_disclosure
from .passes.choose_compaction import stage as choose_compaction
from .passes.build_detail_catalogue import stage as build_detail_catalogue


class DisclosurePipeline(Pipeline[DisclosureRequest, DisclosurePlan]):
    """Classify protected content, choose beneficial excerpts, and freeze their detail routes."""

    def __init__(self) -> None:
        super().__init__(root=self.build())

    def build(self) -> Step[DisclosureRequest, DisclosurePlan]:
        return Sequence(
            name="DisclosurePipeline",
            steps=[
                classify_disclosure(),
                choose_compaction(),
                build_detail_catalogue(),
            ],
        )
