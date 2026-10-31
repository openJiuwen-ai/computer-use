# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Select final inline, field, record and tree templates before serialization."""

from __future__ import annotations

from computer_use.interaction.read.presentation.interface.structure.algorithms.group_empty_atoms import (
    apply as group_empty_atoms,
)
from computer_use.interaction.read.presentation.interface.structure.algorithms.group_inline_members import (
    apply as group_inline_members,
)
from computer_use.interaction.read.presentation.interface.structure.algorithms.share_inline_headers import (
    apply as share_inline_headers,
)
from computer_use.interaction.read.presentation.interface.structure.algorithms.plan_record_members import (
    apply as plan_record_members,
)
from computer_use.interaction.read.presentation.interface.structure.algorithms.plan_visual_chains import (
    apply as plan_visual_chains,
)
from computer_use.interaction.read.presentation.interface.geometric.algorithms.layout import select_templates
from computer_use.interaction.read.presentation.interface.model import InterfaceDraft
from computer_use.pipeline import Contract, Effect, Pass
from computer_use.pipeline.core import Control


def execute(draft: InterfaceDraft, control: Control) -> InterfaceDraft:
    """Select final inline, field, record and tree templates before serialization."""
    draft = group_empty_atoms(draft)
    draft = group_inline_members(draft)
    draft = share_inline_headers(draft)
    draft = plan_record_members(draft)
    draft = plan_visual_chains(draft)
    draft = select_templates(draft)
    return draft


stage = Pass.define(
    name="GeometricLayoutPass",
    function=execute,
    contract=Contract(
        InterfaceDraft,
        InterfaceDraft,
        {Effect.PURE},
        requires=("immutable captured facts and explicit source membership",),
        writes=("local geometric layout plan",),
    ),
)
