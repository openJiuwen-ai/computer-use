# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Validate real-source preservation and freeze a text-free interface document."""

from __future__ import annotations

from computer_use.interaction.read.presentation.interface.validation.passes.validate_document import (
    stage as validate_document,
)
from computer_use.interaction.read.presentation.interface.validation.passes.freeze_document import (
    stage as freeze_document,
)
from computer_use.interaction.read.presentation.interface.validation.passes.assess_layout import stage as assess_layout
from computer_use.interaction.read.presentation.interface.validation.passes.needs_recovery import (
    stage as needs_recovery,
)
from computer_use.interaction.read.presentation.interface.validation.passes.restore_local_layout import (
    stage as restore_local_layout,
)
from computer_use.interaction.read.presentation.interface.validation.passes.retain_layout import stage as retain_layout
from computer_use.interaction.read.presentation.interface.model import InterfaceDocument, InterfaceDraft
from computer_use.pipeline import Pipeline, Select, Sequence
from computer_use.pipeline.core import Step


class InterfaceValidationPipeline(Pipeline[InterfaceDraft, InterfaceDocument]):
    """Check source coverage and finalize immutable blocks for later delivery and rendering."""

    def __init__(self) -> None:
        super().__init__(root=self.build())

    def build(self) -> Step[InterfaceDraft, InterfaceDocument]:
        return Sequence(
            name="InterfaceValidationPipeline",
            steps=[
                assess_layout(),
                Select(
                    name="ChooseLocalLayoutRecovery",
                    selector=needs_recovery(),
                    branches={True: restore_local_layout(), False: retain_layout()},
                ),
                validate_document(),
                freeze_document(),
            ],
        )
