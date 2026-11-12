# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Assign preview references."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.numbering.helpers import assign_native_batch
from computer_use.interaction.read.numbering.model import RESOURCE, _PartPreview
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def assign_preview_references(value: _PartPreview, control: Control) -> dict[str, str]:
    """Allocate native-to-display aliases in prepared projection traversal order."""
    snapshot, mapping = value.snapshot, {}
    assign_native_batch(snapshot, mapping, value.references, value.visible_tokens)
    return mapping


stage = Pass.define(
    name="assign_preview_references",
    function=assign_preview_references,
    contract=Contract(_PartPreview, dict, RESOURCE, writes=("session reference allocator",)),
)
