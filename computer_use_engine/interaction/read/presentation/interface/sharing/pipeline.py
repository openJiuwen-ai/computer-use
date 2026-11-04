# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Prepare scoped action and field declarations before final text serialization."""

from __future__ import annotations

from computer_use.pipeline import Pipeline, Sequence
from computer_use.pipeline.core import Step
from .model import SharingPlan, SharingRequest
from .passes.initialize_sharing import stage as initialize_sharing
from .passes.share_native_actions import stage as share_native_actions
from .passes.share_complete_fields import stage as share_complete_fields
from .passes.validate_sharing import stage as validate_sharing


class SharingPipeline(Pipeline[SharingRequest, SharingPlan]):
    """Select beneficial complete field/action groups within proven source ancestors."""

    def __init__(self) -> None:
        super().__init__(root=self.build())

    def build(self) -> Step[SharingRequest, SharingPlan]:
        return Sequence(
            name="InterfaceSharingPipeline",
            steps=[initialize_sharing(), share_native_actions(), share_complete_fields(), validate_sharing()],
        )
