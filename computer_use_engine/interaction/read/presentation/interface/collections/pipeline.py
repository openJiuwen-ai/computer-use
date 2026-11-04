# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Plan native table and outline layout without producing text or changing references."""

from __future__ import annotations

from computer_use.interaction.read.presentation.interface.collections.model import CollectionDocument, CollectionRequest
from computer_use.interaction.read.presentation.interface.collections.passes.capture_native_collections import (
    stage as capture_native_collections,
)
from computer_use.interaction.read.presentation.interface.collections.passes.plan_table_layouts import (
    stage as plan_table_layouts,
)
from computer_use.interaction.read.presentation.interface.collections.passes.validate_table_layouts import (
    stage as validate_table_layouts,
)
from computer_use.pipeline import Pipeline, Sequence
from computer_use.pipeline.core import Step


class CollectionPipeline(Pipeline[CollectionRequest, CollectionDocument]):
    """Build source-backed row, cell and column plans for a captured interface.

    Reuse complete native collection evidence, retain logical extents and action
    ownership, then validate the frozen plans. Unsupported local collections keep
    their original interface tree and carry an explicit rejection reason.
    """

    def __init__(self) -> None:
        super().__init__(root=self.build())

    def build(self) -> Step[CollectionRequest, CollectionDocument]:
        return Sequence(
            name="CollectionPipeline",
            steps=[capture_native_collections(), plan_table_layouts(), validate_table_layouts()],
        )
