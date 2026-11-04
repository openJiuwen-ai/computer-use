# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Build human-readable units while preserving distinct native input targets."""

from __future__ import annotations

from computer_use.interaction.read.presentation.interface.composition.passes.classify_units import (
    stage as classify_units,
)
from computer_use.interaction.read.presentation.interface.composition.passes.associate_field_labels import (
    stage as associate_field_labels,
)
from computer_use.interaction.read.presentation.interface.composition.passes.group_form_bands import (
    stage as group_form_bands,
)
from computer_use.interaction.read.presentation.interface.model import InterfaceDraft
from computer_use.pipeline import Pipeline, Sequence
from computer_use.pipeline.core import Step


class InterfaceCompositionPipeline(Pipeline[InterfaceDraft, InterfaceDraft]):
    """Classify controls and join proven label/input neighbors without moving their facts."""

    def __init__(self) -> None:
        super().__init__(root=self.build())

    def build(self) -> Step[InterfaceDraft, InterfaceDraft]:
        return Sequence(
            name="InterfaceCompositionPipeline", steps=[classify_units(), group_form_bands(), associate_field_labels()]
        )
