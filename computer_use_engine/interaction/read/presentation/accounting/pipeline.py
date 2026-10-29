# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.accounting.passes.account_fields_actions import (
    stage as account_fields_actions,
)
from computer_use.interaction.read.presentation.accounting.passes.account_relations import (
    stage as account_relations,
)
from computer_use.pipeline import Pipeline, Sequence

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.model import BranchFacts, RenderedPart
    from computer_use.interaction.read.presentation.records import RenderedTree
    from computer_use.pipeline.core import Step


class AccountingPipeline(Pipeline["RenderedTree", "RenderedPart"]):
    """Account for source facts omitted from a root's primary rendering.

    Compare the render against captured native fields and actions, then describe
    unrepresented relationships. Return the rendered part together with supplementary values
    and their source records.

    Attributes:
        branch: Root members and native relationship index used for source accounting.
    """

    def __init__(self, branch: BranchFacts) -> None:
        self.branch = branch
        super().__init__(root=self.build())

    def build(self) -> Step[RenderedTree, RenderedPart]:
        branch = self.branch
        snapshot = branch.request.snapshot

        return Sequence(
            name="AccountingPipeline",
            steps=[
                account_fields_actions(branch=branch, snapshot=snapshot),
                account_relations(branch=branch),
            ],
        )
