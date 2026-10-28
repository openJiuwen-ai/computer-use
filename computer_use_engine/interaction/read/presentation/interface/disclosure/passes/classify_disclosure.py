# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Keep protected field content inline before selecting excerpt candidates."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.pipeline import Contract, Effect, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control
from ..model import ClassifiedDisclosure, DisclosureRequest


def classify(request: DisclosureRequest, control: Control) -> ClassifiedDisclosure:
    """Select complete passive fields permitted by this observation's display mode."""
    candidates = tuple(field for field in request.fields if not field.protections) if request.enabled else ()
    return ClassifiedDisclosure(request, candidates)


stage = Pass.define(
    name="classify_disclosure",
    function=classify,
    contract=Contract(
        DisclosureRequest,
        ClassifiedDisclosure,
        {Effect.PURE},
        requires=("captured detail routes", "protected states"),
        writes=("eligible fields",),
    ),
)
