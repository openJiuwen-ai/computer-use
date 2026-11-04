# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Share a final field's exact visual label relation with its contained controls."""

from __future__ import annotations

from computer_use.pipeline import Contract, Effect, Pass
from computer_use.pipeline.core import Control
from ...model import AtomFinalizeRequest
from ..field_labels import share_field_labels


def execute(request: AtomFinalizeRequest, control: Control) -> AtomFinalizeRequest:
    """Record field context as the destination of already-expressed visual label slots."""
    return share_field_labels(request)


stage = Pass.define(
    name="GeometricFieldLabelPass",
    function=execute,
    contract=Contract(
        AtomFinalizeRequest,
        AtomFinalizeRequest,
        {Effect.PURE},
        requires=("final field membership", "source-bound label references"),
        writes=("compact relation slots", "field-context source destinations"),
    ),
)
