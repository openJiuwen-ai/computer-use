# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Account relations."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.model import PURE, FieldAccounting, RenderedPart
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from typing import Callable

    from computer_use.interaction.read.presentation.model import BranchFacts
    from computer_use.pipeline.core import Control


def create(*, branch: BranchFacts) -> Callable[[FieldAccounting, Control], RenderedPart]:
    def relations(accounted: FieldAccounting, control: Control) -> RenderedPart:
        """Attach unrepresented native relation groups and their source records to the rendered
        part.
        """
        groups, sources = branch.relations.render(branch.members, accounted.rendered)
        return RenderedPart(accounted.rendered, accounted.residuals, groups, sources)

    return relations


stage = Pass.factory(
    name="account_relations",
    factory=create,
    contract=Contract(FieldAccounting, RenderedPart, PURE, requires=("final forest", "native relations")),
)
