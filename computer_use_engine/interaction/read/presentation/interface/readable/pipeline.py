# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Prepare readable controls, explanatory lines and source-explicit native notes."""

from __future__ import annotations

from computer_use.pipeline import Pipeline, Sequence
from computer_use.pipeline.core import Step
from .model import ReadablePlan, ReadableRequest
from .passes.plan_node_typography import stage as nodes
from .passes.plan_field_headings import stage as headings
from .passes.plan_visual_explanations import stage as explanations
from .passes.validate_typography import stage as validate


class ReadablePresentationPipeline(Pipeline[ReadableRequest, ReadablePlan]):
    """Choose text placements over the final geometry without changing source facts.

    Keep ordinary fields and controls in the main reading flow. Preserve ancillary
    facts as numbered notes in the same result, and derive question captions only
    from exact existing shared descriptions. The frozen plan participates in diff
    comparison; text is generated later by InterfaceTextPipeline.
    """

    def __init__(self) -> None:
        super().__init__(root=self.build())

    def build(self) -> Step[ReadableRequest, ReadablePlan]:
        return Sequence(name="ReadablePresentationPipeline", steps=[nodes(), headings(), explanations(), validate()])
