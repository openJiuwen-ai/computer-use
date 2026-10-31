# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Own the final geometric organization of immutable presentation candidates."""

from __future__ import annotations

from computer_use.interaction.read.presentation.interface.geometric.passes.scope import stage as scope
from computer_use.interaction.read.presentation.interface.geometric.passes.compound import stage as compound
from computer_use.interaction.read.presentation.interface.geometric.passes.unit import stage as unit
from computer_use.interaction.read.presentation.interface.geometric.passes.pattern import stage as pattern
from computer_use.interaction.read.presentation.interface.geometric.passes.alignment import stage as alignment
from computer_use.interaction.read.presentation.interface.geometric.passes.relations import stage as relations
from computer_use.interaction.read.presentation.interface.geometric.passes.region import stage as region
from computer_use.interaction.read.presentation.interface.geometric.passes.value_controls import stage as value_controls
from computer_use.interaction.read.presentation.interface.geometric.passes.row import stage as row
from computer_use.interaction.read.presentation.interface.geometric.passes.structure import stage as structure
from computer_use.interaction.read.presentation.interface.geometric.passes.option_groups import stage as option_groups
from computer_use.interaction.read.presentation.interface.geometric.passes.order import stage as order
from computer_use.interaction.read.presentation.interface.geometric.passes.layout import stage as layout
from computer_use.interaction.read.presentation.interface.geometric.passes.projection import stage as projection
from computer_use.interaction.read.presentation.interface.geometric.passes.validation import stage as validation
from computer_use.interaction.read.presentation.interface.geometric.passes.freeze import stage as freeze
from computer_use.interaction.read.presentation.interface.validation.passes.needs_recovery import (
    stage as needs_recovery,
)
from computer_use.interaction.read.presentation.interface.validation.passes.restore_local_layout import stage as restore
from computer_use.interaction.read.presentation.interface.validation.passes.retain_layout import stage as retain
from computer_use.interaction.read.presentation.interface.model import InterfaceDocument, InterfaceRequest
from computer_use.pipeline import Pipeline, Select, Sequence
from computer_use.pipeline.core import Step


class GeometricLayoutPipeline(Pipeline[InterfaceRequest, InterfaceDocument]):
    """Turn prepared source facts into the final reading structure.

    Native boundaries and original identities constrain local visual grouping. Select
    rows, nested fields, record structures and their output templates, then validate
    source conservation. A single recovery gate restores only invalid local scopes.
    Text generation consumes the frozen plan without making new geometry decisions.
    """

    def __init__(self) -> None:
        super().__init__(root=self.build())

    def build(self) -> Step[InterfaceRequest, InterfaceDocument]:
        return Sequence(
            name="GeometricLayoutPipeline",
            steps=[
                scope(),
                compound(),
                unit(),
                pattern(),
                alignment(),
                relations(),
                region(),
                value_controls(),
                row(),
                structure(),
                option_groups(),
                order(),
                layout(),
                projection(),
                validation(),
                Select(
                    name="GeometricLocalRecovery",
                    selector=needs_recovery(),
                    branches={True: restore(), False: retain()},
                ),
                freeze(),
            ],
        )
