# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Select structured record and inline forms for already-bound interface units."""

from __future__ import annotations

from computer_use.interaction.read.presentation.interface.structure.passes.build_records import stage as build_records
from computer_use.interaction.read.presentation.interface.structure.passes.build_record_blocks import (
    stage as build_record_blocks,
)
from computer_use.interaction.read.presentation.interface.structure.passes.group_inline_members import (
    stage as group_inline_members,
)
from computer_use.interaction.read.presentation.interface.structure.passes.share_inline_headers import (
    stage as share_inline_headers,
)
from computer_use.interaction.read.presentation.interface.structure.passes.plan_visual_chains import (
    stage as plan_visual_chains,
)
from computer_use.interaction.read.presentation.interface.structure.passes.plan_record_members import (
    stage as plan_record_members,
)
from computer_use.interaction.read.presentation.interface.structure.passes.group_empty_atoms import (
    stage as group_empty_atoms,
)
from computer_use.interaction.read.presentation.interface.model import InterfaceDraft
from computer_use.pipeline import Pipeline, Sequence
from computer_use.pipeline.core import Step


class InterfaceStructurePipeline(Pipeline[InterfaceDraft, InterfaceDraft]):
    """Build visual records and compact homogeneous runs without changing source order."""

    def __init__(self) -> None:
        super().__init__(root=self.build())

    def build(self) -> Step[InterfaceDraft, InterfaceDraft]:
        return Sequence(
            name="InterfaceStructurePipeline",
            steps=[
                build_records(),
                build_record_blocks(),
                group_empty_atoms(),
                group_inline_members(),
                share_inline_headers(),
                plan_record_members(),
                plan_visual_chains(),
            ],
        )
