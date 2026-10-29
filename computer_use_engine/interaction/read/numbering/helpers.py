# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from computer_use.interaction.read.facts.snapshot import SemanticSnapshot
    from computer_use.interaction.read.presentation.records import ReferenceAllocator


def assign_native(
    snapshot: SemanticSnapshot, mapping: dict[str, str], references: ReferenceAllocator, native: str
) -> None:
    surface = snapshot.collection.surface if snapshot.collection is not None else None
    mapping[native] = references.reference((surface, snapshot.scope_kind, native))
