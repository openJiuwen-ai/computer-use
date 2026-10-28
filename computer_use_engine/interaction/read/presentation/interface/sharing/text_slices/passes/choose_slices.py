# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Choose beneficial complete child-to-ancestor text ranges."""

from computer_use.pipeline import Contract, Effect, Pass
from computer_use.pipeline.core import Control
from ..model import SelectedTextSlices, TextSliceEvidence
from ..planning import choose


def execute(evidence: TextSliceEvidence, control: Control) -> SelectedTextSlices:
    """Select only unique occurrences with a net saving and a literal anchor."""
    return choose(evidence)


stage = Pass.define(
    name="choose_interface_text_slices",
    function=execute,
    contract=Contract(
        TextSliceEvidence,
        SelectedTextSlices,
        {Effect.PURE},
        requires=("literal complete scalar candidates",),
        writes=("selected exact UTF-16 ranges",),
    ),
)
