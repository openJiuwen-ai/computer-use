# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Pure full/diff selection and delivery-confirmed baseline publication.

The existing caller decides when output was delivered. These pipelines neither
emit a message nor advance a baseline merely because a read has completed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.delivery.baseline.passes.assemble_baseline import (
    stage as assemble_baseline,
)
from computer_use.interaction.read.delivery.baseline.passes.publish_observation import (
    stage as publish_observation,
)
from computer_use.interaction.read.delivery.baseline.passes.retire_previous_baseline import (
    stage as retire_previous_baseline,
)
from computer_use.pipeline import Pipeline, Sequence

if TYPE_CHECKING:
    from computer_use.interaction.read.delivery.baseline.model import BaselineInput, BaselineStore
    from computer_use.pipeline.core import Step


class BaselinePublicationPipeline(Pipeline["BaselineInput", "None"]):
    """Replace the baseline retained for an observation stream.

    Retire the preceding baseline and prepare the new delivered representation
    and its size. Store it when it fits the retention budget; retained baselines
    support later compatible diff comparisons.

    Attributes:
        store: Bounded owner of delivered baselines and their retained-size accounting.
    """

    def __init__(self, store: BaselineStore) -> None:
        self.store = store
        super().__init__(root=self.build())

    def build(self) -> Step[BaselineInput, None]:
        store = self.store

        return Sequence(
            name="BaselinePublicationPipeline",
            steps=[
                retire_previous_baseline(store=store),
                assemble_baseline(),
                publish_observation(store=store),
            ],
        )
