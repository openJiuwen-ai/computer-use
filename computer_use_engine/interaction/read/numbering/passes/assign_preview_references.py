# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Assign preview references."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from computer_use.interaction.read.numbering.helpers import assign_native
from computer_use.interaction.read.numbering.model import RESOURCE, _PartPreview
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def assign_preview_references(value: _PartPreview, control: Control) -> dict[str, str]:
    """Allocate native-to-display aliases in prepared projection traversal order."""
    snapshot, mapping = value.snapshot, {}
    for ref in cast("PreparedSnapshot", value.prepared).refs:
        native = snapshot.refs[ref]
        if native not in mapping:
            assign_native(snapshot, mapping, value.references, native)
    return mapping


stage = Pass.define(
    name="assign_preview_references",
    function=assign_preview_references,
    contract=Contract(_PartPreview, dict, RESOURCE, writes=("session reference allocator",)),
)
if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.preparation import PreparedSnapshot
