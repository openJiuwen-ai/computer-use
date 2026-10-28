# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Plan exact text reuse while preserving every original element and scalar."""

from __future__ import annotations

from computer_use.pipeline import Pipeline, Sequence
from computer_use.pipeline.core import Step
from .model import TextSliceDocument, TextSliceRequest
from .passes.capture_sources import stage as capture_sources
from .passes.choose_slices import stage as choose_slices
from .passes.validate_slices import stage as validate_slices


class TextSlicePipeline(Pipeline[TextSliceRequest, TextSliceDocument]):
    """Reuse complete ancestor text through readable, independently recoverable excerpts."""

    def __init__(self) -> None:
        super().__init__(root=self.build())

    def build(self) -> Step[TextSliceRequest, TextSliceDocument]:
        return Sequence(
            name="InterfaceTextSlicePipeline", steps=[capture_sources(), choose_slices(), validate_slices()]
        )
