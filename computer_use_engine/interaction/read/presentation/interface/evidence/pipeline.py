# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Prepare immutable source and local boundary evidence for interface organization."""

from __future__ import annotations

from computer_use.interaction.read.presentation.interface.evidence.passes.index_sources import stage as index_sources
from computer_use.interaction.read.presentation.interface.evidence.passes.derive_boundaries import (
    stage as derive_boundaries,
)
from computer_use.interaction.read.presentation.interface.model import InterfaceDraft, InterfaceRequest
from computer_use.pipeline import Pipeline, Sequence
from computer_use.pipeline.core import Step


class InterfaceEvidencePipeline(Pipeline[InterfaceRequest, InterfaceDraft]):
    """Index the captured projection and classify the boundaries that grouping must retain."""

    def __init__(self) -> None:
        super().__init__(root=self.build())

    def build(self) -> Step[InterfaceRequest, InterfaceDraft]:
        return Sequence(name="InterfaceEvidencePipeline", steps=[index_sources(), derive_boundaries()])
