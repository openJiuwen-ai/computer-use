# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Capture complete literal scalar and native containment evidence."""

from computer_use.pipeline import Contract, Effect, Pass
from computer_use.pipeline.core import Control
from ..capture import capture
from ..model import TextSliceEvidence, TextSliceRequest


def execute(request: TextSliceRequest, control: Control) -> TextSliceEvidence:
    """Retain eligible original fields without creating a rendered representation."""
    return capture(request)


stage = Pass.define(
    name="capture_interface_text_slice_sources",
    function=execute,
    contract=Contract(
        TextSliceRequest,
        TextSliceEvidence,
        {Effect.PURE},
        requires=("complete final atom slots", "native reference and parent evidence"),
        writes=("typed text slice candidates",),
    ),
)
