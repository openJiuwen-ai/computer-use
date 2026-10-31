# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Verify exact text restoration and native source relationships."""

from computer_use.pipeline import Contract, Effect, Pass
from computer_use.pipeline.core import Control
from ..model import SelectedTextSlices, TextSliceDocument
from ..planning import validate


def execute(selected: SelectedTextSlices, control: Control) -> TextSliceDocument:
    """Freeze substitutions after validating original sources, anchors and ranges."""
    return validate(selected)


stage = Pass.define(
    name="validate_interface_text_slices",
    function=execute,
    contract=Contract(
        SelectedTextSlices,
        TextSliceDocument,
        {Effect.PURE},
        requires=("selected original text ranges",),
        writes=("validated full-view text slice plan",),
    ),
)
