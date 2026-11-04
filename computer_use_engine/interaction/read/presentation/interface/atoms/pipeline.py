# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Plan source-bound atomic content before display layout and serialization."""

from __future__ import annotations

from computer_use.pipeline import Pipeline
from computer_use.pipeline.core import Step
from .model import AtomDocument, AtomRequest
from .passes.plan_atomic_fields import stage as plan_atomic_fields


class AtomPlanningPipeline(Pipeline[AtomRequest, AtomDocument]):
    """Freeze roles, states, fields, actions and coverage from one captured projection."""

    def __init__(self) -> None:
        super().__init__(root=self.build())

    def build(self) -> Step[AtomRequest, AtomDocument]:
        return plan_atomic_fields()
