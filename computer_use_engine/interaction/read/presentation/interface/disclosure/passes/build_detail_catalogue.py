# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Freeze validated excerpt routes and retain their complete captured source values."""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING

from computer_use.pipeline import Contract, Effect, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control
from ..model import DisclosurePlan, SelectedDisclosure
from ..policy import validate_excerpt


def build(value: SelectedDisclosure, control: Control) -> DisclosurePlan:
    """Index exact source/range decisions while rejecting duplicate display-field owners."""
    entries = {}
    for excerpt in value.excerpts:
        validate_excerpt(excerpt)
        key = (excerpt.source.ref, excerpt.source.attribute)
        if key in entries:
            raise ValueError("Disclosure has competing sources for the same display field")
        entries[key] = excerpt
    return DisclosurePlan(MappingProxyType(entries))


stage = Pass.define(
    name="build_detail_catalogue",
    function=build,
    contract=Contract(
        SelectedDisclosure,
        DisclosurePlan,
        {Effect.PURE},
        requires=("selected excerpts",),
        writes=("immutable detail routes",),
        failures=("presentation_contract",),
    ),
)
