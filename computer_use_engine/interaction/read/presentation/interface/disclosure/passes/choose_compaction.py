# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Select high-benefit text excerpts without mutating original fields."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.pipeline import Contract, Effect, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control
from ..model import ClassifiedDisclosure, SelectedDisclosure
from ..policy import choose_excerpt


def choose(value: ClassifiedDisclosure, control: Control) -> SelectedDisclosure:
    """Apply one policy to each eligible field, accounting for recovery notice cost."""
    selected = []
    for field in value.candidates:
        excerpt = choose_excerpt(field, value.request.policy)
        if excerpt is not None:
            selected.append(excerpt)
    return SelectedDisclosure(tuple(selected))


stage = Pass.define(
    name="choose_compaction",
    function=choose,
    contract=Contract(
        ClassifiedDisclosure,
        SelectedDisclosure,
        {Effect.PURE},
        requires=("eligible fields",),
        writes=("selected excerpts",),
    ),
)
